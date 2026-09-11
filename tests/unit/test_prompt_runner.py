import json
from pathlib import Path

import pytest

from gimp_mcp.jobs import JobManager
from gimp_mcp.prompt_runner import PlanError, PromptRunner, parse_plan


def test_parse_plan_accepts_markdown_json():
    plan = parse_plan('hello\n```json\n{"goal":"x","steps":[{"tool":"layer_create","arguments":{"name":"A"},"reason":"base"}]}\n```')
    assert plan["steps"][0]["tool"] == "layer_create"


def test_parse_plan_rejects_unknown_tool_and_executable_fields():
    with pytest.raises(PlanError):
        parse_plan('{"goal":"x","steps":[{"tool":"raw_python","arguments":{}}]}')
    with pytest.raises(PlanError):
        parse_plan('{"goal":"x","steps":[{"tool":"layer_create","arguments":{"shell":"rm -rf /"}}]}')


def test_runner_creates_session_executes_and_previews(tmp_path: Path):
    jobs = JobManager(tmp_path / "jobs")
    calls = []

    def ai_chat(**kwargs):
        return json.dumps({"goal":"simple","steps":[{"tool":"layer_create","arguments":{"name":"Ink"},"reason":"layer"}]})

    def tool_call(name, args):
        calls.append((name, dict(args)))
        if name == "session_create":
            return {"ok": True, "data": {"session_id": "s1"}}
        return {"ok": True, "data": {}}

    job = jobs.create("make art", "triforce", "m")
    result = PromptRunner(jobs, ai_chat, tool_call).run(job)
    assert result.status == "completed"
    assert result.session_id == "s1"
    assert ("layer_create", {"name": "Ink", "session_id": "s1"}) in calls
    assert any(name == "preview_render" for name, _ in calls)


def test_runner_repairs_bad_model_output(tmp_path: Path):
    jobs = JobManager(tmp_path / "jobs")
    responses = iter(["not json", '{"goal":"ok","steps":[]}'])

    def ai_chat(**kwargs):
        return next(responses)

    def tool_call(name, args):
        if name == "session_create":
            return {"ok": True, "data": {"session_id": "s1"}}
        return {"ok": True, "data": {}}

    job = jobs.create("make art", "triforce", "m")
    result = PromptRunner(jobs, ai_chat, tool_call, repair_attempts=1).run(job)
    assert result.status == "completed"
