import io

import pytest

from osm_polygon_image_tag.runtime import console as console_module
from osm_polygon_image_tag.runtime.console import ConsoleRenderer


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_non_terminal_progress_is_canonical_json_without_ansi() -> None:
    stream = io.StringIO()
    renderer = ConsoleRenderer(stderr=stream)

    renderer.progress({"source_pbf": "a.osm.pbf", "event": "pbf_started"})

    assert stream.getvalue() == ('progress {"event":"pbf_started","source_pbf":"a.osm.pbf"}\n')
    assert "\x1b[" not in stream.getvalue()


def test_json_mode_disables_human_rendering_on_a_terminal() -> None:
    stream = _Terminal()
    renderer = ConsoleRenderer(log_format="json", stderr=stream)

    renderer.progress({"event": "asset_shard_completed", "asset_index": 1})

    assert stream.getvalue() == ('progress {"asset_index":1,"event":"asset_shard_completed"}\n')
    assert "\x1b[" not in stream.getvalue()


def test_terminal_human_mode_uses_injected_renderers() -> None:
    events: list[dict[str, object]] = []
    errors: list[str] = []
    renderer = ConsoleRenderer(
        stderr=_Terminal(),
        human_progress=events.append,
        human_error=errors.append,
    )

    renderer.progress({"event": "asset_reference_progress", "completed": 2, "total": 3})
    renderer.error("provider failed")

    assert events == [{"event": "asset_reference_progress", "completed": 2, "total": 3}]
    assert errors == ["provider failed"]


def test_error_redacts_secret_query_values() -> None:
    stream = io.StringIO()
    renderer = ConsoleRenderer(stderr=stream)

    renderer.error("failed https://example.test/x?access_token=secret&width=10")

    assert "secret" not in stream.getvalue()
    assert "access_token=REDACTED" in stream.getvalue()


def test_human_error_uses_default_rich_renderer() -> None:
    stream = io.StringIO()
    renderer = ConsoleRenderer(log_format="human", stderr=stream)

    renderer.error("provider failed")

    assert "error:" in stream.getvalue()
    assert "provider failed" in stream.getvalue()


def test_human_progress_formats_events_without_a_progress_bar() -> None:
    stream = io.StringIO()
    renderer = ConsoleRenderer(log_format="human", stderr=stream)

    renderer.progress({"event": "asset_shard_completed", "asset_index": 2, "shard": "a.parquet"})

    assert "asset_shard_completed" in stream.getvalue()
    assert "asset_index=2" in stream.getvalue()
    assert "shard=a.parquet" in stream.getvalue()


def test_human_progress_bar_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBar:
        def __init__(self, **kwargs: object) -> None:
            self.total = kwargs["total"]
            self.updated = 0
            self.closed = False
            bars.append(self)

        def update(self, amount: int) -> None:
            self.updated += amount

        def close(self) -> None:
            self.closed = True

    bars: list[FakeBar] = []

    monkeypatch.setattr(console_module, "tqdm", FakeBar)
    renderer = ConsoleRenderer(log_format="human", stderr=io.StringIO())

    renderer.progress({"event": "asset_backfill_started", "asset_count": 3})
    renderer.progress({"event": "asset_shard_completed"})
    renderer.progress({"event": "asset_backfill_completed"})
    renderer.close()

    assert len(bars) == 1
    bar = bars[0]
    assert bar.total == 3
    assert bar.updated == 1
    assert bar.closed
    assert renderer._bar is None


def test_human_progress_bar_uses_zero_total_for_non_integer_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    totals: list[object] = []

    class FakeBar:
        def __init__(self, **kwargs: object) -> None:
            totals.append(kwargs["total"])

        def close(self) -> None:
            return None

    monkeypatch.setattr(console_module, "tqdm", FakeBar)
    renderer = ConsoleRenderer(log_format="human", stderr=io.StringIO())

    renderer.progress({"event": "asset_backfill_started", "asset_count": "unknown"})

    assert totals == [0]
