import time

from osm_polygon_image_tag.core.progress import ProgressReporter


def test_reporter_uses_default_heartbeat_interval() -> None:
    reporter = ProgressReporter(lambda _event: None)

    assert reporter._heartbeat_seconds == 30.0


def test_reporter_emits_heartbeat_with_last_stage() -> None:
    events: list[dict[str, object]] = []

    with ProgressReporter(events.append, heartbeat_seconds=0.01) as reporter:
        reporter.emit({"event": "metadata_manifest_scan_started", "manifest_count": 386})
        deadline = time.monotonic() + 1
        while not any(
            event.get("event") == "heartbeat"
            and event.get("last_event") == "metadata_manifest_scan_started"
            for event in events
        ):
            assert time.monotonic() < deadline
            time.sleep(0.005)

    heartbeat = next(
        event
        for event in events
        if event.get("event") == "heartbeat"
        and event.get("last_event") == "metadata_manifest_scan_started"
    )
    assert heartbeat["last_event"] == "metadata_manifest_scan_started"
    assert isinstance(heartbeat["elapsed_seconds"], int)


def test_heartbeat_retains_latest_polygon_and_asset_positions() -> None:
    events: list[dict[str, object]] = []

    with ProgressReporter(events.append, heartbeat_seconds=0.01) as reporter:
        reporter.emit(
            {
                "event": "pbf_started",
                "pbf_index": 10,
                "pbf_count": 386,
            }
        )
        reporter.emit(
            {
                "event": "asset_shard_started",
                "asset_index": 4,
                "asset_count": 217,
            }
        )
        deadline = time.monotonic() + 1
        while not any(
            event.get("event") == "heartbeat"
            and event.get("pbf_index") == 10
            and event.get("asset_index") == 4
            for event in events
        ):
            assert time.monotonic() < deadline
            time.sleep(0.005)

    heartbeat = next(
        event
        for event in events
        if event.get("event") == "heartbeat"
        and event.get("pbf_index") == 10
        and event.get("asset_index") == 4
    )
    assert heartbeat["pbf_index"] == 10
    assert heartbeat["pbf_count"] == 386
    assert heartbeat["asset_index"] == 4
    assert heartbeat["asset_count"] == 217
