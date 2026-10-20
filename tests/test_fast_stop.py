"""Fermare un'attività dev'essere quasi immediato: ogni fase controlla spesso se l'utente ha annullato."""

import sys
import threading
import time

import numpy as np
import pytest
from conftest import make_jpeg

from mosaic.ai import analyze
from mosaic.ai.face_worker import FacesCancelled, run_faces
from mosaic.index.indexer import index_roots
from mosaic.jobs.events import EventBus
from mosaic.jobs.worker import JobRunner
from mosaic.video import transcode


def test_indexing_stops_inside_a_big_folder_without_marking_files_missing(ctx, library):
    for i in range(6):
        make_jpeg(library / f"a{i}.jpg", color=(i * 30, 40, 40))
    index_roots(ctx)
    for i in range(6, 40):
        make_jpeg(library / f"a{i}.jpg", color=(i * 5, 90, 90))
    cancel = threading.Event()
    calls = {"n": 0}

    import mosaic.index.indexer as indexer
    original = indexer._process_group

    def counting(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            cancel.set()  # l'utente annulla a metà della cartella
        return original(*args, **kwargs)

    indexer._process_group = counting
    try:
        stats = index_roots(ctx, rescan=True, cancel=cancel)
    finally:
        indexer._process_group = original
    assert stats.cancelled and calls["n"] == 3
    assert ctx.db.scalar("SELECT COUNT(*) FROM files WHERE missing=1") == 0


def test_analysis_embeds_in_small_steps_and_stops_between_them():
    class Control:
        cancel = threading.Event()

    class FakeSiglip:
        calls = 0

        def embed_images(self, images):
            FakeSiglip.calls += 1
            Control.cancel.set()  # annullato mentre gira il primo blocco
            return np.ones((len(images), 4), dtype=np.float32)

    assert analyze._embed(FakeSiglip(), [object()] * 8, Control) is None
    assert FakeSiglip.calls == 1  # gli altri tre blocchi non partono
    Control.cancel.clear()
    FakeSiglip.calls = 0
    out = analyze._embed(type("S", (), {"embed_images": lambda self, imgs: np.ones((len(imgs), 4))})(), [object()] * 5, None)
    assert out.shape == (5, 4)


def test_face_grouping_rolls_back_when_cancelled(ctx):
    with pytest.raises(FacesCancelled):
        run_faces(ctx, lambda: True)
    assert ctx.db.scalar("SELECT COUNT(*) FROM faces") == 0


def test_video_conversion_is_killed_right_away():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    with pytest.raises(InterruptedError):
        transcode._run([sys.executable, "-c", "import time; time.sleep(30)"], 60, cancel)
    assert time.monotonic() - start < 3


def test_cancelling_a_phase_shows_stopping_and_restart_queues_it_again(ctx):
    bus = EventBus()
    q = bus.subscribe()
    runner = JobRunner(ctx, {"analyze": lambda *a: None, "index": lambda *a: None}, bus)
    first = runner.submit("analyze", {})
    running = runner.queue.claim()
    runner._cancels[running.id] = threading.Event()  # come se stesse girando
    while not q.empty():
        q.get_nowait()
    new = runner.restart_type("analyze")
    event = q.get(timeout=1)
    assert event["id"] == first and event["message"] == "In arresto…" and runner._cancels[first].is_set()
    assert new != first and runner.queue.get(new)["state"] == "queued"
    with pytest.raises(ValueError):
        runner.restart_type("poster")
