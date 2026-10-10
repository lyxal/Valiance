"""Repeatable local measurements for the Stage 0 evidence matrix."""

import asyncio
import json
import platform
import statistics
import time
import tracemalloc
from importlib.metadata import version

from .app import FeasibilityApp
from .worker import SessionProbe, wait_for


async def _editor_memory() -> dict[str, int]:
    """Measure Python allocations for the mounted two-document harness."""
    tracemalloc.start()
    app = FeasibilityApp(with_worker=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.source_editor.load_text("1 2 +\n" * 1000)
        await pilot.pause()
        current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"two_editors_current_bytes": current, "two_editors_peak_bytes": peak}


def measure() -> dict:
    """Measure spawn, persistent submission, output saturation and both stops."""
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "textual": version("textual"),
    }
    probe = SessionProbe()
    try:
        started = time.perf_counter()
        probe.start()
        wait_for(probe, "ready")
        result["spawn_ready_ms"] = round((time.perf_counter() - started) * 1000, 2)
        durations = []
        for _ in range(5):
            started = time.perf_counter()
            probe.submit("1")
            wait_for(probe, "finished")
            durations.append((time.perf_counter() - started) * 1000)
        result["persistent_submission_median_ms"] = round(
            statistics.median(durations), 2
        )
        probe.submit('"' + "x" * 400_000 + '" println')
        # Wait on control alone so stdout intentionally saturates.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if probe.events.poll(0.05):
                event = probe.events.recv()
                if event.kind == "finished":
                    probe.busy = False
                    break
                if event.kind == "failed":
                    raise RuntimeError("output probe failed")
        else:
            raise TimeoutError("output saturation blocked control")
        result["output_chunks_dropped"] = probe.dropped.value
        probe.poll()
        probe.submit("0 while (< 1) => end")
        wait_for(probe, "started")
        time.sleep(0.1)
        started = time.perf_counter()
        result["vm_stop_disposition"] = probe.close()
        result["vm_stop_ms"] = round((time.perf_counter() - started) * 1000, 2)
    finally:
        probe.close()
    probe = SessionProbe()
    try:
        probe.start()
        wait_for(probe, "ready")
        probe.submit(operation="native-block")
        wait_for(probe, "started")
        time.sleep(0.1)
        started = time.perf_counter()
        result["native_stop_disposition"] = probe.close()
        result["native_stop_ms"] = round((time.perf_counter() - started) * 1000, 2)
    finally:
        probe.close()
    result.update(asyncio.run(_editor_memory()))
    return result


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
