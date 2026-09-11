from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from .jobs import ArtworkJob, JobManager

ALLOWED_TOOLS = {
    "layer_create", "layer_delete", "layer_update", "layer_reorder",
    "selection_set", "text_create", "transform_layer", "filter_apply",
    "preview_render",
}
MUTATING_TOOLS = ALLOWED_TOOLS - {"preview_render"}

SYSTEM_PROMPT = """You are the art director for GIMP MCP Studio.
Return ONLY one JSON object with keys goal and steps. Each step must have tool,
arguments and reason. Never emit Python, shell commands, file-system commands,
or tools outside this allowlist: %s. Keep the plan short and executable.
The session_id is injected by the host; never invent one.""" % ", ".join(sorted(ALLOWED_TOOLS))


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

    def _execute_plan(self, job: ArtworkJob, plan: dict[str, Any]) -> None:
        steps = plan["steps"]
        total = max(1, len(steps))
        mutations_since_preview = 0
        for index, step in enumerate(steps, 1):
            self.jobs.checkpoint(job.job_id)
            self.jobs.update(job, status="running", current_step=index, progress=round((index - 1) / total * 100, 1))
            args = dict(step["arguments"])
            if job.session_id and "session_id" not in args:
                args["session_id"] = job.session_id
            result = self.tool_call(step["tool"], args)
            if not isinstance(result, dict) or result.get("ok") is False:
                raise RuntimeError(f"{step['tool']} failed: {result}")
            if step["tool"] in MUTATING_TOOLS:
                mutations_since_preview += 1
                if mutations_since_preview >= self.preview_every:
                    self.jobs.update(job, status="rendering_preview")
                    preview = self.tool_call("preview_render", {"session_id": job.session_id})
                    if not isinstance(preview, dict) or preview.get("ok") is False:
                        raise RuntimeError(f"preview_render failed: {preview}")
                    mutations_since_preview = 0
        if job.session_id and (mutations_since_preview or not steps):
            self.jobs.update(job, status="rendering_preview")
            self.tool_call("preview_render", {"session_id": job.session_id})
        self.jobs.update(job, progress=100.0)

    def run(self, job: ArtworkJob) -> ArtworkJob:
        self.jobs.start(job)
        try:
            if not job.session_id:
                created = self.tool_call("session_create", {})
                if not isinstance(created, dict) or not created.get("ok"):
                    raise RuntimeError(f"session_create failed: {created}")
                job.session_id = str(created["data"]["session_id"])
                self.jobs.update(job, session_id=job.session_id)
            request = job.prompt
            last_error = ""
            for attempt in range(self.repair_attempts + 1):
                self.jobs.checkpoint(job.job_id)
                self.jobs.update(job, status="planning")
                try:
                    plan = self._ask_plan(job, request if not last_error else request + "\nPrevious plan/execution error: " + last_error + "\nReturn a corrected plan only.")
                    self.jobs.update(job, plan=plan)
                    self._execute_plan(job, plan)
                    self.jobs.update(job, status="reviewing")
                    self.jobs.update(job, status="completed", finished_at=time.time(), error=None)
                    return job
                except InterruptedError:
                    raise
                except Exception as exc:
                    last_error = str(exc)
                    if attempt >= self.repair_attempts:
                        raise
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
