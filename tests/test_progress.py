import time

from osm_polygon_image_tag.core.progress import ProgressReporter


def test_reporter_emits_heartbeat_with_last_stage() -> None:
    events: list[dict[str, object]] = []

    with ProgressReporter(events.append, heartbeat_seconds=0.01) as reporter:
        reporter.emit({"event": "metadata_manifest_scan_started", "manifest_count": 386})
        deadline = time.monotonic() + 1
        while not any(event["event"] == "heartbeat" for event in events):
            assert time.monotonic() < deadline
            time.sleep(0.005)

    heartbeat = next(event for event in events if event["event"] == "heartbeat")
    assert heartbeat["last_event"] == "metadata_manifest_scan_started"
    assert isinstance(heartbeat["elapsed_seconds"], int)
