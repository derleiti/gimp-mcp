from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from .jobs import ArtworkJob, JobManager

ALLOWED_TOOLS = {
    "layer_create", "layer_delete", "layer_update", "layer_reorder",
    "selection_set", "text_create", "transform_layer", "layer_fill", "filter_apply",
    "preview_render",
}
MUTATING_TOOLS = ALLOWED_TOOLS - {"preview_render"}

SYSTEM_PROMPT = """You are the art director for GIMP MCP Studio.
Return ONLY one JSON object with keys goal and steps. Each step must have tool,
arguments and reason. Never emit Python, shell commands, file-system commands,
or tools outside this allowlist: %s. Keep the plan short and executable.
The session_id is injected by the host; never invent one.
For layer IDs created during this plan, use the exact string "$last_layer_id".
For the initial/background layer use "$background_layer_id" when available.
Never invent numeric layer IDs.
Tool argument guide:
- layer_create: name, optional width, height, opacity (0..100), visible, blend_mode.
- layer_fill: layer_id, color.
- layer_update: layer_id, optional name, visible, opacity.
- text_create: text, x, y, optional size, font_name.
- transform_layer: layer_id, action, values; prefer translate values=[dx,dy], rotate=[degrees], scale=[x0,y0,x1,y1]. Semantic objects are also accepted.
- filter_apply: layer_id, operation, parameters, optional name.
Do not add undocumented arguments.""" % ", ".join(sorted(ALLOWED_TOOLS))


class PlanError(ValueError):
    pass


def parse_plan(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if match:
        text = match.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"invalid model JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise PlanError("plan requires an object with a steps array")
    cleaned: list[dict[str, Any]] = []
    for index, step in enumerate(data["steps"]):
        if not isinstance(step, dict):
            raise PlanError(f"step {index + 1} must be an object")
        tool = str(step.get("tool") or "").strip()
        if tool not in ALLOWED_TOOLS:
            raise PlanError(f"step {index + 1} uses unknown tool: {tool or '<empty>'}")
        arguments = step.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise PlanError(f"step {index + 1} arguments must be an object")
        if any(k in arguments for k in ("python", "code", "shell", "command")):
            raise PlanError(f"step {index + 1} contains forbidden executable arguments")
        cleaned.append({"tool": tool, "arguments": arguments, "reason": str(step.get("reason") or "")})
    if len(cleaned) > 30:
        raise PlanError("plan exceeds 30 steps")
    return {"goal": str(data.get("goal") or ""), "steps": cleaned}


def _resolve_refs(value: Any, refs: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        key = value[1:]
        if key not in refs:
            raise PlanError(f"unknown runtime reference: {value}")
        return refs[key]
    if isinstance(value, list):
        return [_resolve_refs(item, refs) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_refs(item, refs) for key, item in value.items()}
    return value


def _result_data(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else (result if isinstance(result, dict) else {})


class PromptRunner:
    def __init__(self, jobs: JobManager, ai_chat: Callable[..., str], tool_call: Callable[[str, dict[str, Any]], dict[str, Any]], *, preview_every: int = 2, repair_attempts: int = 2) -> None:
        self.jobs = jobs
        self.ai_chat = ai_chat
        self.tool_call = tool_call
        self.preview_every = max(1, int(preview_every))
        self.repair_attempts = max(0, min(int(repair_attempts), 3))

    def _ask_plan(self, job: ArtworkJob, request: str) -> dict[str, Any]:
        raw = self.ai_chat(provider=job.provider, model=job.model, message=request, system_prompt=SYSTEM_PROMPT)
        return parse_plan(raw)

    def _execute_plan(self, job: ArtworkJob, plan: dict[str, Any], refs: dict[str, Any] | None = None) -> None:
        steps = plan["steps"]
        total = max(1, len(steps))
        mutations_since_preview = 0
        refs = dict(refs or {})
        for index, step in enumerate(steps, 1):
            self.jobs.checkpoint(job.job_id)
            self.jobs.update(job, status="running", current_step=index, progress=round((index - 1) / total * 100, 1))
            args = _resolve_refs(dict(step["arguments"]), refs)
            args["__job_mode"] = job.mode
            if job.session_id and "session_id" not in args:
                args["session_id"] = job.session_id
            result = self.tool_call(step["tool"], args)
            if not isinstance(result, dict) or result.get("ok") is False:
                raise RuntimeError(f"{step['tool']} failed: {result}")
            data = _result_data(result)
            if data.get("layer_id") is not None:
                refs["last_layer_id"] = data["layer_id"]
            if step["tool"] in MUTATING_TOOLS:
                mutations_since_preview += 1
                if mutations_since_preview >= self.preview_every:
                    self.jobs.update(job, status="rendering_preview")
                    preview = self.tool_call("preview_render", {"session_id": job.session_id, "__job_mode": job.mode})
                    if not isinstance(preview, dict) or preview.get("ok") is False:
                        raise RuntimeError(f"preview_render failed: {preview}")
                    mutations_since_preview = 0
        if job.session_id and (mutations_since_preview or not steps):
            self.jobs.update(job, status="rendering_preview")
            self.tool_call("preview_render", {"session_id": job.session_id, "__job_mode": job.mode})
        self.jobs.update(job, progress=100.0)

    def run(self, job: ArtworkJob) -> ArtworkJob:
        self.jobs.start(job)
        try:
            refs: dict[str, Any] = {}
            if not job.session_id:
                created = self.tool_call("session_create", {"__job_mode": job.mode})
                if not isinstance(created, dict) or not created.get("ok"):
                    raise RuntimeError(f"session_create failed: {created}")
                created_data = _result_data(created)
                job.session_id = str(created_data["session_id"])
                if created_data.get("layer_id") is not None:
                    refs["background_layer_id"] = created_data["layer_id"]
                    refs["last_layer_id"] = created_data["layer_id"]
                self.jobs.update(job, session_id=job.session_id)
            else:
                info = self.tool_call("session_info", {"session_id": job.session_id, "__job_mode": job.mode})
                info_data = _result_data(info) if isinstance(info, dict) and info.get("ok") else {}
                layers = info_data.get("layers") if isinstance(info_data.get("layers"), list) else []
                if layers:
                    refs["last_layer_id"] = layers[0].get("layer_id")
                    refs["background_layer_id"] = layers[-1].get("layer_id")
            ref_note = "\nRuntime references available: " + json.dumps(refs, ensure_ascii=False) if refs else ""
            request = job.prompt + ref_note
            plan = None
            last_plan_error = ""
            for attempt in range(self.repair_attempts + 1):
                self.jobs.checkpoint(job.job_id)
                self.jobs.update(job, status="planning")
                try:
                    prompt = request if not last_plan_error else request + "\nPrevious plan parsing error: " + last_plan_error + "\nReturn corrected JSON only."
                    plan = self._ask_plan(job, prompt)
                    break
                except PlanError as exc:
                    last_plan_error = str(exc)
                    if attempt >= self.repair_attempts:
                        raise
            if plan is None:
                raise PlanError(last_plan_error or "model did not return a usable plan")
            self.jobs.update(job, plan=plan)
            # Once semantic execution starts, never replay the entire plan after a
            # tool failure: successful earlier mutations may already be persisted.
            self._execute_plan(job, plan, refs)
            self.jobs.update(job, status="reviewing")
            self.jobs.update(job, status="completed", finished_at=time.time(), error=None)
            return job
        except InterruptedError:
            self.jobs.cancel(job.job_id)
            return job
        except Exception as exc:
            self.jobs.update(job, status="failed", error=str(exc), finished_at=time.time())
            return job

    def followup(self, job: ArtworkJob, prompt: str) -> ArtworkJob:
        self.jobs.add_followup(job, prompt)
        job.prompt = prompt.strip()
        self.jobs.update(job, prompt=job.prompt, status="queued", current_step=0, progress=0.0, finished_at=None, error=None)
        return self.run(job)

    def make_it_cooler(self, job: ArtworkJob, preset: str = "Make it cooler") -> ArtworkJob:
        instruction = f"{preset}. Improve the current image without changing the main concept. Use only a few targeted, undoable GIMP changes."
        return self.followup(job, instruction)
