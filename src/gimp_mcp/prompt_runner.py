from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from .jobs import ArtworkJob, JobManager

ALLOWED_TOOLS = {
    "layer_create", "shape_create", "layer_delete", "layer_update", "layer_reorder",
    "selection_set", "text_create", "transform_layer", "layer_fill", "filter_apply",
    "filter_list", "filter_describe", "pdb_search", "pdb_describe", "pdb_call", "preview_render",
}
MUTATING_TOOLS = ALLOWED_TOOLS - {"preview_render", "filter_list", "filter_describe", "pdb_search", "pdb_describe"}

SYSTEM_PROMPT = """You are the art director for GIMP MCP Studio.
Return ONLY one JSON object with keys goal, outcome, summary and steps. Each step must have tool,
arguments and reason. Never emit Python, shell commands, file-system commands,
or tools outside this allowlist: %s. Generate only the NEXT bounded useful batch, never the whole artwork at once. Keep the batch short and executable.
The session_id is injected by the host; never invent one.
For layer IDs created during this plan, use the exact string "$last_layer_id".
For the initial/background layer use "$background_layer_id" when available.
Never invent numeric layer IDs.
Tool argument guide:
- layer_create: name, optional width, height, opacity (0..100), visible, blend_mode.
- shape_create: name, shape (ellipse or rectangle), x, y, width, height, color. Prefer this for visible object parts such as heads, bodies, ears, eyes, badges and simple silhouettes.
- layer_fill: layer_id, color.
- layer_update: layer_id, optional name, visible, opacity.
- text_create: text, x, y, optional size, font_name.
- transform_layer: layer_id, action, values; prefer translate values=[dx,dy], rotate=[degrees], scale=[x0,y0,x1,y1]. Semantic objects are also accepted.
- filter_apply: layer_id, operation, parameters, optional name.
  Gaussian blur accepts std-dev-x/std-dev-y; a single radius/size/sigma is also normalized safely by the host.
  Unsharp mask uses std-dev, scale, threshold. Brightness/contrast uses brightness (-3..3, subtle usually ±0.02..0.15) and contrast (default 1.0, subtle usually 0.9..1.15). Percentage aliases brightness_percent/contrast_percent are accepted by the host.
For advanced GIMP features, use pdb_search then pdb_describe in one batch, and pdb_call in a later batch after you know the exact signature. pdb_call arguments is a JSON object; session image arguments bind automatically and drawable/layer/item arguments use layer_id. Use filter_list/filter_describe before unfamiliar GEGL effects.
Do not add undocumented arguments.
This is a NATURAL LANGUAGE art workflow: the user describes the desired image/edit; you translate it into the safest executable plan.
Prefer a small number of deterministic steps over speculative effects.
A full-layer fill does NOT create an object silhouette. Use shape_create for geometric subject parts. If the requested subject cannot be represented photorealistically with the available semantic tools, create an honest stylized composition and say so in the goal; never pretend that flat fills are realistic painting.
For filters, use canonical GEGL names from this safe registry only: gegl:gaussian-blur, gegl:brightness-contrast, gegl:unsharp-mask, gegl:color-temperature, gegl:shadows-highlights. Never invent aliases like noise or gaussian_blur.
Use preview_render explicitly after meaningful visual milestones; the host may also add previews automatically.
outcome must be one of continue, done, stalled, restart_recommended, failed. Use continue when more useful work remains; done only when the requested artwork is genuinely complete. Return only JSON.""" % ", ".join(sorted(ALLOWED_TOOLS))


class PlanError(ValueError):
    pass


_TOOL_ALLOWED_ARGS = {
    "layer_create": {"name", "width", "height", "opacity", "visible", "blend_mode"},
    "shape_create": {"name", "shape", "x", "y", "width", "height", "color"},
    "layer_delete": {"layer_id"},
    "layer_update": {"layer_id", "name", "visible", "opacity"},
    "layer_reorder": {"layer_id", "position"},
    "layer_fill": {"layer_id", "color"},
    "selection_set": {"action", "x", "y", "width", "height"},
    "text_create": {"text", "x", "y", "size", "font_name"},
    "transform_layer": {"layer_id", "action", "values"},
    "filter_apply": {"layer_id", "operation", "parameters", "name"},
    "filter_list": {"query", "limit"},
    "filter_describe": {"operation"},
    "pdb_search": {"query", "limit"},
    "pdb_describe": {"name"},
    "pdb_call": {"name", "arguments"},
    "preview_render": {"max_width", "max_height"},
}
_SAFE_FILTERS = {
    "gegl:gaussian-blur",
    "gegl:brightness-contrast",
    "gegl:unsharp-mask",
    "gegl:color-temperature",
    "gegl:shadows-highlights",
}

_FILTER_ALIASES = {
    "blur": "gegl:gaussian-blur",
    "gaussian-blur": "gegl:gaussian-blur",
    "gaussian_blur": "gegl:gaussian-blur",
    "gegl:gaussian_blur": "gegl:gaussian-blur",
    "brightness-contrast": "gegl:brightness-contrast",
    "brightness_contrast": "gegl:brightness-contrast",
    "gegl:brightness_contrast": "gegl:brightness-contrast",
    "unsharp-mask": "gegl:unsharp-mask",
    "unsharp_mask": "gegl:unsharp-mask",
    "sharpen": "gegl:unsharp-mask",
    "gegl:unsharp_mask": "gegl:unsharp-mask",
    "color-temperature": "gegl:color-temperature",
    "color_temperature": "gegl:color-temperature",
    "gegl:color_temperature": "gegl:color-temperature",
    "shadows-highlights": "gegl:shadows-highlights",
    "shadows_highlights": "gegl:shadows-highlights",
    "gegl:shadows_highlights": "gegl:shadows-highlights",
}

def _canonical_filter_operation(value: Any) -> str:
    raw = str(value or "").strip().lower()
    canonical = _FILTER_ALIASES.get(raw, raw.replace("_", "-"))
    if canonical and not canonical.startswith("gegl:"):
        canonical = "gegl:" + canonical
    return canonical


def _validate_step_arguments(index: int, tool: str, arguments: dict[str, Any]) -> None:
    allowed = _TOOL_ALLOWED_ARGS.get(tool, set())
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise PlanError(f"step {index} {tool} has unsupported arguments: {', '.join(unknown)}")
    if tool == "filter_apply":
        operation = _canonical_filter_operation(arguments.get("operation"))
        arguments["operation"] = operation
        if operation not in _SAFE_FILTERS:
            raise PlanError(f"step {index} uses unsupported filter operation: {operation or '<empty>'}")
        if not isinstance(arguments.get("parameters", {}), dict):
            raise PlanError(f"step {index} filter parameters must be an object")
    if tool in {"layer_delete", "layer_update", "layer_reorder", "layer_fill", "transform_layer", "filter_apply"}:
        if "layer_id" not in arguments:
            raise PlanError(f"step {index} {tool} requires layer_id")
    if tool == "transform_layer":
        action = str(arguments.get("action") or "")
        values = arguments.get("values")
        if action not in {"translate", "rotate", "scale"} or not isinstance(values, list):
            raise PlanError(f"step {index} has invalid transform arguments")


def parse_plan(raw: str, *, max_steps: int = 50) -> dict[str, Any]:
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
        _validate_step_arguments(index + 1, tool, arguments)
        cleaned.append({"tool": tool, "arguments": arguments, "reason": str(step.get("reason") or "")})
    limit = max(1, min(int(max_steps), 100))
    if len(cleaned) > limit:
        raise PlanError(f"batch exceeds {limit} steps")
    outcome = str(data.get("outcome") or "done").strip().lower().replace("-", "_")
    if outcome not in {"continue", "done", "stalled", "restart_recommended", "failed"}:
        raise PlanError(f"invalid batch outcome: {outcome}")
    return {"goal": str(data.get("goal") or ""), "outcome": outcome, "summary": str(data.get("summary") or ""), "steps": cleaned}


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


def transparent_background_requested(prompt: str) -> bool:
    text = (prompt or "").lower()
    return any(token in text for token in (
        "transparent background", "transparent canvas", "transparency",
        "alpha background", "no background",
    ))


class PromptRunner:
    def __init__(self, jobs: JobManager, ai_chat: Callable[..., str], tool_call: Callable[[str, dict[str, Any]], dict[str, Any]], *,
                 max_steps_per_batch: int = 20, provider_planning_timeout: int = 600, provider_review_timeout: int = 300,
                 gimp_operation_timeout: int = 90, preview_render_timeout: int = 120, export_timeout: int = 180,
                 vision_timeout: int = 120, idle_watchdog_timeout: int = 300, preview_every: int = 4,
                 vision_review_every_batches: int = 2, creative_budget: str = "effectively_unlimited", repair_attempts: int = 2) -> None:
        self.jobs = jobs
        self.ai_chat = ai_chat
        self.tool_call = tool_call
        self.max_steps_per_batch = max(1, min(int(max_steps_per_batch), 100))
        self.provider_planning_timeout = max(10, min(int(provider_planning_timeout), 3600))
        self.provider_review_timeout = max(10, min(int(provider_review_timeout), 3600))
        self.gimp_operation_timeout = max(5, min(int(gimp_operation_timeout), 1800))
        self.preview_render_timeout = max(5, min(int(preview_render_timeout), 1800))
        self.export_timeout = max(5, min(int(export_timeout), 3600))
        self.vision_timeout = max(5, min(int(vision_timeout), 1800))
        self.idle_watchdog_timeout = max(10, min(int(idle_watchdog_timeout), 86400))
        self.preview_every = max(1, min(int(preview_every), 1000))
        self.vision_review_every_batches = max(0, min(int(vision_review_every_batches), 1000))
        self.creative_budget = creative_budget if creative_budget in {"conservative","normal","extended","effectively_unlimited"} else "effectively_unlimited"
        self.stagnant_batch_limit = {"conservative":2,"normal":3,"extended":5,"effectively_unlimited":8}[self.creative_budget]
        self.repair_attempts = max(0, min(int(repair_attempts), 3))

    def _touch_activity(self, job: ArtworkJob) -> None:
        self.jobs.update(job, last_activity_at=time.time())

    def _touch_progress(self, job: ArtworkJob, signature: str | None = None, **changes: Any) -> None:
        now=time.time(); changes.update(last_activity_at=now,last_progress_at=now)
        if signature is not None: changes["last_progress_signature"] = signature
        self.jobs.update(job, **changes)

    def _ask_plan(self, job: ArtworkJob, request: str) -> dict[str, Any]:
        try:
            try:
                raw = self.ai_chat(provider=job.provider, model=job.model, message=request, system_prompt=SYSTEM_PROMPT, timeout=self.provider_planning_timeout)
            except TypeError as exc:
                if "timeout" not in str(exc): raise
                raw = self.ai_chat(provider=job.provider, model=job.model, message=request, system_prompt=SYSTEM_PROMPT)
        except Exception as exc:
            if isinstance(exc, TimeoutError) or exc.__class__.__name__ == "TimeoutExpired":
                raise TimeoutError(f"provider planning timed out after {self.provider_planning_timeout}s") from exc
            raise
        self._touch_activity(job)
        return parse_plan(raw, max_steps=self.max_steps_per_batch)

    def _call_tool(self, job: ArtworkJob, name: str, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        payload=dict(args); payload["__job_mode"]=job.mode; payload["__timeout_seconds"]=timeout
        if job.session_id and "session_id" not in payload: payload["session_id"]=job.session_id
        result=self.tool_call(name,payload); self._touch_activity(job); return result

    @staticmethod
    def _plan_signature(plan: dict[str, Any]) -> str:
        compact=[(s.get("tool"),s.get("arguments")) for s in plan.get("steps",[])]
        return json.dumps(compact,sort_keys=True,ensure_ascii=False,default=str)

    def _document_summary(self, job: ArtworkJob) -> dict[str, Any]:
        if not job.session_id:
            return {}
        try:
            info=self._call_tool(job,"session_info",{},self.gimp_operation_timeout)
            data=_result_data(info) if isinstance(info,dict) and info.get("ok") else {}
            layers=data.get("layers") if isinstance(data.get("layers"),list) else []
            compact=[]
            for layer in layers[:120]:
                if not isinstance(layer,dict): continue
                compact.append({k:layer.get(k) for k in ("layer_id","name","x","y","width","height","visible","opacity") if k in layer})
            names=[str(x.get("name") or "") for x in compact]
            return {"width":data.get("width"),"height":data.get("height"),"layer_count":len(layers),
                    "unique_layer_names":len(set(names)),"layers":compact,"truncated":len(layers)>len(compact)}
        except Exception as exc:
            return {"warning":f"session_info unavailable: {exc}"}

    def _context(self, job: ArtworkJob, refs: dict[str, Any], last_summary: str, vision_note: dict[str, Any] | None, document_summary: dict[str, Any] | None = None) -> str:
        state={"session_id":job.session_id,"batch_number":job.batch_number,"total_steps":job.total_steps,
               "successful_mutations":job.successful_mutations,"runtime_refs":refs,"last_batch_summary":last_summary,
               "document":document_summary or {},
               "recent_vision":vision_note or (job.vision_notes[-1] if job.vision_notes else None)}
        budget_hint={
            "conservative":"Prefer focused necessary changes and stop when the request is clearly satisfied.",
            "normal":"Balance detail and efficiency; continue while each batch materially improves the artwork.",
            "extended":"Spend extra effort on coherent detail, polish and refinement while progress remains useful.",
            "effectively_unlimited":"Do not stop because the artwork is complex or many batches are needed; continue as long as each batch makes meaningful progress.",
        }[self.creative_budget]
        return (job.prompt + "\n\nCURRENT ARTWORK STATE (continue the SAME session):\n" + json.dumps(state,ensure_ascii=False,default=str) +
                f"\nCreative budget: {self.creative_budget}. {budget_hint}" +
                f"\nReturn only the NEXT batch, at most {self.max_steps_per_batch} steps. Do not repeat already completed work. outcome=continue if more useful work remains; outcome=done only when genuinely complete.")

    def _execute_batch(self, job: ArtworkJob, plan: dict[str, Any], refs: dict[str, Any]) -> int:
        mutations=0; since_preview=0
        base_total=job.total_steps
        for index, step in enumerate(plan["steps"],1):
            self.jobs.checkpoint(job.job_id)
            step["status"]="running"; step.pop("error",None)
            self.jobs.update(job,status="running",current_step=base_total+index,plan=plan)
            args=_resolve_refs(dict(step["arguments"]),refs)
            timeout=self.preview_render_timeout if step["tool"]=="preview_render" else self.gimp_operation_timeout
            try:
                result=self._call_tool(job,step["tool"],args,timeout)
            except Exception as exc:
                if step["tool"] == "preview_render":
                    step["status"]="warning"; step["error"]=f"preview failed: {exc}"; self.jobs.update(job,plan=plan,total_steps=base_total+index)
                    continue
                raise
            if not isinstance(result,dict) or result.get("ok") is False:
                if step["tool"] == "preview_render":
                    step["status"]="warning"; step["error"]=f"preview failed: {result}"; self.jobs.update(job,plan=plan,total_steps=base_total+index)
                    continue
                step["status"]="failed"; step["error"]=str(result); self.jobs.update(job,plan=plan)
                raise RuntimeError(f"step {base_total+index} {step['tool']} failed: {result}")
            step["status"]="ok"; data=_result_data(result)
            if data.get("layer_id") is not None: refs["last_layer_id"]=data["layer_id"]
            if step["tool"] in MUTATING_TOOLS:
                mutations += 1; since_preview += 1
                self._touch_progress(job, successful_mutations=job.successful_mutations+1)
                if since_preview >= self.preview_every:
                    self.jobs.checkpoint(job.job_id); self.jobs.update(job,status="rendering_preview")
                    try:
                        preview=self._call_tool(job,"preview_render",{},self.preview_render_timeout)
                        if isinstance(preview,dict) and preview.get("ok") is not False:
                            self._touch_progress(job); since_preview=0
                        else: step.setdefault("warnings",[]).append(f"preview failed: {preview}")
                    except Exception as exc:
                        step.setdefault("warnings",[]).append(f"preview failed: {exc}")
                        self._touch_activity(job)
            self.jobs.update(job,plan=plan,total_steps=base_total+index)
        if job.session_id and since_preview:
            self.jobs.checkpoint(job.job_id); self.jobs.update(job,status="rendering_preview")
            try:
                preview=self._call_tool(job,"preview_render",{},self.preview_render_timeout)
                if isinstance(preview,dict) and preview.get("ok") is not False: self._touch_progress(job)
            except Exception:
                self._touch_activity(job)
        return mutations

    def _vision_review(self, job: ArtworkJob) -> dict[str, Any] | None:
        if not self.vision_review_every_batches or job.batch_number % self.vision_review_every_batches: return None
        self.jobs.checkpoint(job.job_id); self.jobs.update(job,status="reviewing")
        try:
            result=self._call_tool(job,"vision_capture",{},self.vision_timeout)
            if not isinstance(result,dict) or result.get("ok") is False: return {"warning":str(result)}
            data=_result_data(result)
            note={k:data.get(k) for k in ("source","width","height","canvas_width","canvas_height","grid_px") if k in data}
            layers=data.get("layers") if isinstance(data.get("layers"),list) else []
            note["layer_count"]=len(layers)
            note["layers"]=[{k:x.get(k) for k in ("layer_id","name","x","y","width","height") if k in x} for x in layers[:60] if isinstance(x,dict)]
            note["truncated"]=len(layers)>60
            notes=(job.vision_notes+[note])[-5:]
            self._touch_progress(job,vision_notes=notes)
            return note
        except Exception as exc:
            self._touch_activity(job); return {"warning":str(exc)}

    def run(self, job: ArtworkJob) -> ArtworkJob:
        self.jobs.start(job)
        refs: dict[str, Any]={}
        try:
            if not job.session_id:
                create_args={}
                if not transparent_background_requested(job.prompt): create_args["background_color"]="#F5F3EE"
                created=self._call_tool(job,"session_create",create_args,self.gimp_operation_timeout)
                if not isinstance(created,dict) or not created.get("ok"): raise RuntimeError(f"session_create failed: {created}")
                data=_result_data(created); job.session_id=str(data["session_id"])
                if data.get("layer_id") is not None: refs.update(background_layer_id=data["layer_id"],last_layer_id=data["layer_id"])
                self._touch_progress(job,session_id=job.session_id)
            else:
                info=self._call_tool(job,"session_info",{},self.gimp_operation_timeout); data=_result_data(info) if isinstance(info,dict) and info.get("ok") else {}
                layers=data.get("layers") if isinstance(data.get("layers"),list) else []
                if layers: refs.update(last_layer_id=layers[0].get("layer_id"),background_layer_id=layers[-1].get("layer_id"))

            last_summary=""; last_signature=None; stagnant_batches=0
            while True:
                self.jobs.checkpoint(job.job_id)
                self.jobs.update(job,status="planning",batch_number=job.batch_number+1,current_step=job.total_steps)
                document_summary=self._document_summary(job)
                request=self._context(job,refs,last_summary,None,document_summary)
                plan=None; last_error=""
                for attempt in range(self.repair_attempts+1):
                    try:
                        plan=self._ask_plan(job, request if not last_error else request+"\nPrevious batch error: "+last_error+"\nReturn corrected JSON only.")
                        break
                    except (PlanError, TimeoutError) as exc:
                        last_error=str(exc)
                        self.jobs.update(job,error=last_error)
                        if attempt>=self.repair_attempts: raise
                        self.jobs.checkpoint(job.job_id)
                if plan is None: raise PlanError(last_error or "model did not return a usable batch")
                signature=self._plan_signature(plan)
                mutations=self._execute_batch(job,plan,refs)
                vision_note=self._vision_review(job)
                last_summary=str(plan.get("summary") or plan.get("goal") or "")[:1200]

                if mutations == 0 and signature == last_signature: stagnant_batches += 1
                elif mutations == 0: stagnant_batches += 1
                else: stagnant_batches = 0
                last_signature=signature
                self.jobs.update(job,last_progress_signature=signature)

                outcome=plan.get("outcome","done")
                if outcome == "done":
                    self.jobs.update(job,status="completed",progress=100.0,finished_at=time.time(),error=None)
                    return job
                if outcome in {"stalled","restart_recommended"}:
                    self.jobs.update(job,status="stalled",finished_at=time.time(),error=last_summary or outcome)
                    return job
                if outcome == "failed": raise RuntimeError(last_summary or "provider marked artwork job failed")
                if stagnant_batches >= self.stagnant_batch_limit:
                    self.jobs.update(job,status="stalled",finished_at=time.time(),error="No meaningful artwork mutation across repeated continuation batches")
                    return job
                if job.last_progress_at and time.time()-job.last_progress_at > self.idle_watchdog_timeout:
                    self.jobs.update(job,status="stalled",finished_at=time.time(),error=f"No meaningful artwork progress for {self.idle_watchdog_timeout}s")
                    return job
                # Continue indefinitely while useful progress occurs. No total step/batch ceiling.
                if vision_note: last_summary += "\nVision: "+json.dumps(vision_note,ensure_ascii=False,default=str)[:1500]
        except InterruptedError:
            self.jobs.cancel(job.job_id); return job
        except TimeoutError as exc:
            self.jobs.update(job,status="stalled",error=f"Timeout while preserving current artwork: {exc}",finished_at=time.time()); return job
        except Exception as exc:
            self.jobs.update(job,status="failed",error=str(exc),finished_at=time.time()); return job

    def followup(self, job: ArtworkJob, prompt: str) -> ArtworkJob:
        self.jobs.add_followup(job,prompt); job.prompt=prompt.strip()
        self.jobs.update(job,prompt=job.prompt,status="queued",current_step=0,progress=0.0,finished_at=None,error=None,batch_number=0,total_steps=0,last_progress_signature=None)
        return self.run(job)

    def make_it_cooler(self, job: ArtworkJob, preset: str = "Make it cooler") -> ArtworkJob:
        return self.followup(job,f"{preset}. Improve the current image without changing the main concept. Use targeted, undoable GIMP changes and continue in bounded batches until genuinely complete.")
