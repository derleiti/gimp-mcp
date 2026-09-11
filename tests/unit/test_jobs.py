import json
from pathlib import Path

from gimp_mcp.jobs import JobManager


def test_job_lifecycle_and_recovery(tmp_path: Path):
    jobs = JobManager(tmp_path)
    job = jobs.create("draw a bear", "triforce", "model-x")
    assert job.status == "queued"
    jobs.start(job)
    jobs.pause(job.job_id)
    assert job.status == "paused"

    recovered = JobManager(tmp_path).get(job.job_id)
    assert recovered.status == "paused"
    assert recovered.prompt == "draw a bear"

    jobs.resume(job.job_id)
    assert job.status == "running"
    jobs.cancel(job.job_id)
    assert job.status == "cancelled"
    assert json.loads((tmp_path / f"{job.job_id}.json").read_text())["status"] == "cancelled"
