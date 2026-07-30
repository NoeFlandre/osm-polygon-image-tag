import io

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
