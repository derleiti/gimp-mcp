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
    layer_args = next(args for name, args in calls if name == "layer_create")
    assert layer_args["name"] == "Ink" and layer_args["session_id"] == "s1" and layer_args["__job_mode"] == "auto"
    assert layer_args["__timeout_seconds"] == 90
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


def test_multi_batch_executes_more_than_30_actions_without_global_exhaustion(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0; tool_calls=[]
    def ai_chat(**kwargs):
        nonlocal ai_calls
        ai_calls += 1
        outcome='done' if ai_calls == 4 else 'continue'
        return json.dumps({'goal':'long artwork','outcome':outcome,'summary':f'batch {ai_calls}',
            'steps':[{'tool':'shape_create','arguments':{'name':f'part-{ai_calls}-{i}','shape':'ellipse','x':i*2,'y':i*2,'width':10,'height':10,'color':'#654321'},'reason':'detail'} for i in range(10)]})
    def tool_call(name,args):
        tool_calls.append((name,dict(args)))
        if name=='session_create': return {'ok':True,'data':{'session_id':'same-session','layer_id':1}}
        if name=='shape_create': return {'ok':True,'data':{'layer_id':len(tool_calls)+1}}
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,max_steps_per_batch=10,preview_every=10,vision_review_every_batches=0).run(jobs.create('complex art','triforce','m'))
    assert job.status=='completed'
    assert job.total_steps==40
    assert job.successful_mutations==40
    assert ai_calls==4
    assert {args.get('session_id') for name,args in tool_calls if name=='shape_create'}=={'same-session'}


def test_repeated_empty_continue_batches_stall_instead_of_looping(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls += 1
        return json.dumps({'goal':'x','outcome':'continue','summary':'nothing new','steps':[]})
    def tool_call(name,args):
        if name=='session_create': return {'ok':True,'data':{'session_id':'s1'}}
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,vision_review_every_batches=0).run(jobs.create('x','triforce','m'))
    assert job.status=='stalled'
    assert ai_calls==8
    assert 'meaningful' in (job.error or '').lower()


def test_provider_planning_timeout_is_retried_without_losing_session(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0; sessions=[]
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls += 1
        if ai_calls==1: raise TimeoutError('slow provider')
        return json.dumps({'goal':'x','outcome':'done','summary':'recovered','steps':[{'tool':'layer_create','arguments':{'name':'A'},'reason':'x'}]})
    def tool_call(name,args):
        if name=='session_create': sessions.append('s1'); return {'ok':True,'data':{'session_id':'s1'}}
        return {'ok':True,'data':{'layer_id':2} if name=='layer_create' else {}}
    job=PromptRunner(jobs,ai_chat,tool_call,repair_attempts=1,vision_review_every_batches=0).run(jobs.create('x','triforce','m'))
    assert job.status=='completed' and ai_calls==2 and sessions==['s1']


def test_vision_and_preview_cadence_between_batches(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0; names=[]
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls+=1
        return json.dumps({'goal':'x','outcome':'done' if ai_calls==2 else 'continue','summary':'x','steps':[{'tool':'layer_create','arguments':{'name':f'L{ai_calls}'},'reason':'x'}]})
    def tool_call(name,args):
        names.append(name)
        if name=='session_create': return {'ok':True,'data':{'session_id':'s1','layer_id':1}}
        if name=='layer_create': return {'ok':True,'data':{'layer_id':len(names)}}
        if name=='vision_capture': return {'ok':True,'data':{'source':'session-xcf','width':100,'height':100,'layers':[]}}
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,preview_every=1,vision_review_every_batches=1).run(jobs.create('x','triforce','m'))
    assert job.status=='completed'
    assert names.count('preview_render')>=2
    assert names.count('vision_capture')==2
    assert len(job.vision_notes)==2


def test_parse_plan_batch_limit_is_configurable_above_30():
    raw=json.dumps({'goal':'x','outcome':'done','steps':[{'tool':'layer_create','arguments':{'name':str(i)},'reason':'x'} for i in range(40)]})
    assert len(parse_plan(raw,max_steps=50)['steps'])==40
    with pytest.raises(PlanError,match='batch exceeds 20 steps'):
        parse_plan(raw,max_steps=20)


def test_creative_budget_changes_stall_tolerance_not_total_work(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); counts={'n':0}
    def ai_chat(**kwargs):
        counts['n']+=1
        return json.dumps({'goal':'x','outcome':'continue','steps':[]})
    def tool_call(name,args):
        if name=='session_create': return {'ok':True,'data':{'session_id':'s'}}
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,creative_budget='conservative',vision_review_every_batches=0).run(jobs.create('x','triforce','m'))
    assert job.status=='stalled' and counts['n']==2


def test_fresh_jobs_create_fresh_sessions_and_followup_reuses_current(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); created=[]; ai_calls=0
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls+=1
        return json.dumps({'goal':'x','outcome':'done','steps':[]})
    def tool_call(name,args):
        if name=='session_create':
            sid=f's{len(created)+1}'; created.append(sid); return {'ok':True,'data':{'session_id':sid}}
        if name=='session_info': return {'ok':True,'data':{'layers':[]}}
        return {'ok':True,'data':{}}
    runner=PromptRunner(jobs,ai_chat,tool_call,vision_review_every_batches=0)
    first=runner.run(jobs.create('first','triforce','m'))
    second=runner.run(jobs.create('second','triforce','m'))
    assert first.session_id=='s1' and second.session_id=='s2' and created==['s1','s2']
    runner.followup(first,'edit first')
    assert first.session_id=='s1' and created==['s1','s2']


def test_cancel_between_batches_stops_before_next_provider_call(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0; holder={}
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls+=1
        return json.dumps({'goal':'x','outcome':'continue','steps':[{'tool':'layer_create','arguments':{'name':'A'},'reason':'x'}]})
    def tool_call(name,args):
        if name=='session_create': return {'ok':True,'data':{'session_id':'s','layer_id':1}}
        if name=='layer_create': jobs.cancel(holder['job'].job_id); return {'ok':True,'data':{'layer_id':2}}
        return {'ok':True,'data':{}}
    holder['job']=jobs.create('x','triforce','m')
    job=PromptRunner(jobs,ai_chat,tool_call,vision_review_every_batches=0).run(holder['job'])
    assert job.status=='cancelled' and ai_calls==1


def test_pause_then_resume_during_multibatch_job(tmp_path: Path):
    import threading, time
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0; entered=threading.Event(); holder={}
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls+=1
        outcome='done' if ai_calls==2 else 'continue'
        return json.dumps({'goal':'x','outcome':outcome,'steps':[{'tool':'layer_create','arguments':{'name':str(ai_calls)},'reason':'x'}]})
    def tool_call(name,args):
        if name=='session_create': return {'ok':True,'data':{'session_id':'s','layer_id':1}}
        if name=='layer_create' and ai_calls==1:
            jobs.pause(holder['job'].job_id); entered.set()
        return {'ok':True,'data':{'layer_id':ai_calls+1}}
    holder['job']=jobs.create('x','triforce','m')
    runner=PromptRunner(jobs,ai_chat,tool_call,vision_review_every_batches=0)
    t=threading.Thread(target=lambda: runner.run(holder['job']),daemon=True); t.start()
    assert entered.wait(1); time.sleep(.05); assert holder['job'].status=='paused'
    jobs.resume(holder['job'].job_id); t.join(2)
    assert not t.is_alive() and holder['job'].status=='completed' and ai_calls==2


def test_transient_timeout_after_progress_preserves_completed_batch_and_session(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); ai_calls=0; created=0
    def ai_chat(**kwargs):
        nonlocal ai_calls; ai_calls+=1
        if ai_calls==1: return json.dumps({'goal':'x','outcome':'continue','summary':'first','steps':[{'tool':'layer_create','arguments':{'name':'A'},'reason':'x'}]})
        if ai_calls==2: raise TimeoutError('temporary')
        return json.dumps({'goal':'x','outcome':'done','summary':'recovered','steps':[{'tool':'layer_create','arguments':{'name':'B'},'reason':'x'}]})
    def tool_call(name,args):
        nonlocal created
        if name=='session_create': created+=1; return {'ok':True,'data':{'session_id':'same','layer_id':1}}
        if name=='layer_create': return {'ok':True,'data':{'layer_id':10+ai_calls}}
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,repair_attempts=1,vision_review_every_batches=0).run(jobs.create('x','triforce','m'))
    assert job.status=='completed' and job.successful_mutations==2 and created==1 and job.session_id=='same'


def test_preview_failure_is_recoverable(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs')
    def ai_chat(**kwargs): return json.dumps({'goal':'x','outcome':'done','steps':[{'tool':'layer_create','arguments':{'name':'A'},'reason':'x'}]})
    def tool_call(name,args):
        if name=='session_create': return {'ok':True,'data':{'session_id':'s','layer_id':1}}
        if name=='layer_create': return {'ok':True,'data':{'layer_id':2}}
        if name=='preview_render': raise RuntimeError('transient stream cleanup')
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,preview_every=1,vision_review_every_batches=0).run(jobs.create('x','triforce','m'))
    assert job.status=='completed' and job.successful_mutations==1


def test_next_batch_receives_current_document_inventory(tmp_path: Path):
    jobs=JobManager(tmp_path/'jobs'); prompts=[]; layers=[{'layer_id':1,'name':'Background'}]
    def ai_chat(**kwargs):
        prompts.append(kwargs['message'])
        if len(prompts)==1:
            return json.dumps({'goal':'x','outcome':'continue','summary':'first','steps':[{'tool':'layer_create','arguments':{'name':'Known-Layer'},'reason':'x'}]})
        assert 'Known-Layer' in kwargs['message']
        assert '"layer_count": 2' in kwargs['message']
        return json.dumps({'goal':'x','outcome':'done','summary':'done','steps':[]})
    def tool_call(name,args):
        if name=='session_create': return {'ok':True,'data':{'session_id':'s','layer_id':1}}
        if name=='layer_create': layers.insert(0,{'layer_id':2,'name':'Known-Layer'}); return {'ok':True,'data':{'layer_id':2}}
        if name=='session_info': return {'ok':True,'data':{'width':100,'height':100,'layers':list(layers)}}
        return {'ok':True,'data':{}}
    job=PromptRunner(jobs,ai_chat,tool_call,vision_review_every_batches=0).run(jobs.create('x','triforce','m'))
    assert job.status=='completed' and len(prompts)==2
