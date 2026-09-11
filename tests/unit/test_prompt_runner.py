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
    assert ("layer_create", {"name": "Ink", "session_id": "s1", "__job_mode": "auto"}) in calls
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


def test_runner_propagates_batch_mode(tmp_path: Path):
    jobs = JobManager(tmp_path / "jobs")
    calls = []
    def ai_chat(**kwargs):
        return json.dumps({"goal":"simple","steps":[{"tool":"layer_create","arguments":{"name":"Ink"},"reason":"layer"}]})
    def tool_call(name, args):
        calls.append((name, dict(args)))
        if name == "session_create":
            return {"ok": True, "data": {"session_id": "s1"}}
        return {"ok": True, "data": {}}
    job = jobs.create("make art", "triforce", "m", mode="batch")
    result = PromptRunner(jobs, ai_chat, tool_call).run(job)
    assert result.status == "completed"
    assert all(args.get("__job_mode") == "batch" for _, args in calls)


def test_runner_resolves_last_layer_reference(tmp_path: Path):
    jobs = JobManager(tmp_path / "jobs")
    calls = []
    def ai_chat(**kwargs):
        return json.dumps({"goal":"simple","steps":[
            {"tool":"layer_create","arguments":{"name":"Ink"},"reason":"layer"},
            {"tool":"layer_fill","arguments":{"layer_id":"$last_layer_id","color":"#112233"},"reason":"fill"}
        ]})
    def tool_call(name, args):
        calls.append((name, dict(args)))
        if name == "session_create":
            return {"ok": True, "data": {"session_id":"s1", "layer_id":11}}
        if name == "layer_create":
            return {"ok": True, "data": {"layer_id":22}}
        return {"ok": True, "data": {}}
    job = jobs.create("make art", "triforce", "m")
    result = PromptRunner(jobs, ai_chat, tool_call).run(job)
    assert result.status == "completed"
    fill = next(args for name, args in calls if name == "layer_fill")
    assert fill["layer_id"] == 22


def test_tool_failure_does_not_replay_successful_steps(tmp_path: Path):
    jobs = JobManager(tmp_path / "jobs")
    ai_calls = 0
    tool_names = []
    def ai_chat(**kwargs):
        nonlocal ai_calls
        ai_calls += 1
        return json.dumps({"goal":"x","steps":[
            {"tool":"layer_create","arguments":{"name":"A"},"reason":"one"},
            {"tool":"layer_fill","arguments":{"layer_id":"$last_layer_id","color":"bad"},"reason":"two"}
        ]})
    def tool_call(name, args):
        tool_names.append(name)
        if name == "session_create": return {"ok":True,"data":{"session_id":"s1","layer_id":1}}
        if name == "layer_create": return {"ok":True,"data":{"layer_id":2}}
        if name == "layer_fill": return {"ok":False,"error":{"code":"FAIL"}}
        return {"ok":True,"data":{}}
    result=PromptRunner(jobs, ai_chat, tool_call, repair_attempts=2).run(jobs.create("x","triforce","m"))
    assert result.status == "failed"
    assert ai_calls == 1
    assert tool_names.count("layer_create") == 1


def test_parse_plan_accepts_shape_create_and_normalizes_filter_aliases():
    shape = json.dumps({"goal":"bear","steps":[{"tool":"shape_create","arguments":{"name":"Head","shape":"ellipse","x":10,"y":10,"width":100,"height":80,"color":"#654321"},"reason":"head"}]})
    parsed = parse_plan(shape)
    assert parsed["steps"][0]["tool"] == "shape_create"
    alias = json.dumps({"goal":"x","steps":[{"tool":"filter_apply","arguments":{"layer_id":"$last_layer_id","operation":"gaussian_blur","parameters":{"radius":3}},"reason":"blur"}]})
    parsed = parse_plan(alias)
    assert parsed["steps"][0]["arguments"]["operation"] == "gegl:gaussian-blur"
    bad = json.dumps({"goal":"x","steps":[{"tool":"filter_apply","arguments":{"layer_id":"$last_layer_id","operation":"totally_fake_filter","parameters":{}},"reason":"bad"}]})
    with pytest.raises(PlanError):
        parse_plan(bad)
