import threading
import time

import pytest

from mosaic.jobs.queue import JobQueue
from mosaic.jobs.worker import JobRunner


def test_enqueue_claim_finish(ctx):
    q = JobQueue(ctx.db)
    jid = q.enqueue("index", {"a": 1})
    job = q.claim()
    assert job.id == jid and job.payload == {"a": 1} and job.attempts == 1
    assert q.claim() is None
    q.finish(jid, "done")
    assert q.get(jid)["state"] == "done" and q.get(jid)["progress"] == 1


def test_unique_enqueue_reuses_queued_job(ctx):
    q = JobQueue(ctx.db)
    assert q.enqueue("x", {"k": 1}, unique=True) == q.enqueue("x", {"k": 1}, unique=True)
    assert q.enqueue("x", {"k": 2}, unique=True) != q.enqueue("x", {"k": 1}, unique=True)


def test_running_jobs_return_to_queue_after_crash(ctx):
    q = JobQueue(ctx.db)
    q.enqueue("x")
    q.claim()
    assert q.reset_running() == 1
    assert q.claim() is not None


def test_cancel_only_queued(ctx):
    q = JobQueue(ctx.db)
    jid = q.enqueue("x")
    assert q.cancel(jid) is True
    assert q.claim() is None


def test_runner_executes_and_publishes_events(ctx):
    done = []
    runner = JobRunner(ctx, {"echo": lambda c, job, ctl: done.append(job.payload["n"])}, workers=2)
    events = runner.bus.subscribe()
    runner.start()
    try:
        for n in range(3):
            runner.submit("echo", {"n": n})
        assert runner.wait_idle(10)
    finally:
        runner.stop()
    assert sorted(done) == [0, 1, 2]
    states = {e["state"] for e in iter(lambda: events.get_nowait() if not events.empty() else None, None)}
    assert {"queued", "done"} <= states


def test_failed_job_is_retried_then_marked_failed(ctx):
    calls = []

    def boom(c, job, ctl):
        calls.append(job.id)
        raise RuntimeError("no")

    runner = JobRunner(ctx, {"boom": boom}, workers=1)
    runner.start()
    try:
        jid = runner.submit("boom")
        assert runner.wait_idle(10)
    finally:
        runner.stop()
    assert len(calls) == 2
    assert runner.queue.get(jid)["state"] == "failed"
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors") == 2


def test_heavy_jobs_are_mutually_exclusive(ctx):
    active, peak = 0, 0
    lock = threading.Lock()

    def heavy(c, job, ctl):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1

    runner = JobRunner(ctx, {"analyze": heavy, "faces": heavy}, workers=3)
    runner.start()
    try:
        for t in ("analyze", "faces", "analyze"):
            runner.submit(t)
        assert runner.wait_idle(10)
    finally:
        runner.stop()
    assert peak == 1


def test_unknown_job_type_rejected(ctx):
    runner = JobRunner(ctx, {}, workers=1)
    try:
        runner.submit("nope")
    except ValueError:
        return
    raise AssertionError("atteso ValueError")


def test_progress_records_done_and_total_and_publishes_them(ctx):
    import threading

    from mosaic.jobs.events import EventBus
    from mosaic.jobs.queue import JobQueue
    from mosaic.jobs.worker import JobControl

    queue = JobQueue(ctx.db)
    jid = queue.enqueue("process", {"ids": [1, 2, 3]})
    job = queue.claim()
    bus = EventBus()
    q = bus.subscribe()
    JobControl(queue, job, bus, threading.Event()).progress(0.5, "2/4 foto", 2, 4)
    row = queue.get(jid)
    assert (row["done"], row["total"], row["progress"]) == (2, 4, 0.5)
    event = q.get(timeout=1)
    assert (event["done"], event["total"], event["progress"]) == (2, 4, 0.5)
    JobControl(queue, job, bus, threading.Event()).progress(0.75, "solo testo")
    assert (queue.get(jid)["done"], queue.get(jid)["total"]) == (2, 4)  # senza conteggi restano quelli di prima


def test_queued_event_carries_the_total_and_a_stage_can_be_cancelled_whole(ctx):
    from mosaic.jobs.events import EventBus
    from mosaic.jobs.worker import JobRunner

    bus = EventBus()
    q = bus.subscribe()
    runner = JobRunner(ctx, {"process": lambda *a: None, "index": lambda *a: None}, bus)
    ids = [runner.submit("process", {"ids": [1, 2, 3]}) for _ in range(3)]
    assert q.get(timeout=1)["total"] == 3
    runner.submit("index", {})
    assert runner.cancel_type("process") == 3
    assert {runner.queue.get(i)["state"] for i in ids} == {"cancelled"}
    assert runner.queue.get(ids[-1] + 1)["state"] == "queued"  # l'altra fase resta


def test_job_list_keeps_every_active_job(ctx):
    from mosaic.jobs.queue import JobQueue

    queue = JobQueue(ctx.db)
    for _ in range(80):
        queue.enqueue("process", {"ids": [1]})
    rows = queue.list(10)
    assert len(rows) == 80 and all(r["state"] == "queued" for r in rows)


class _Submitted:
    def __init__(self) -> None:
        self.types: list[str] = []

    def submit(self, job_type, payload=None, unique=False):  # type: ignore[no-untyped-def]
        self.types.append(job_type)


@pytest.mark.parametrize(("mode", "expected"), [("on-demand", "rename"), ("never", "rename"), ("newly-indexed", "caption")])
def test_descriptions_join_the_pipeline_only_when_automatic(ctx, monkeypatch, mode, expected):
    from dataclasses import replace

    from mosaic.jobs import handlers

    monkeypatch.setattr("mosaic.llm.backends.captioning_available", lambda c: True)
    ctx.cfg = replace(ctx.cfg, llm=replace(ctx.cfg.llm, mode=mode))
    ctl = _Submitted()
    handlers._after_moments(ctx, ctl)
    assert ctl.types == [expected]


def test_descriptions_are_off_the_pipeline_by_default(ctx):
    assert ctx.cfg.llm.mode == "on-demand"
