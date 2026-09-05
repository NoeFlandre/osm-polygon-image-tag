"""Public Typer command line interface with a stable test injection boundary."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import click

from osm_polygon_image_tag.artifacts.publication import (
    EXPECTED_REPO,
    PublicationResult,
    publish_dataset,
)
from osm_polygon_image_tag.artifacts.reporting import MetadataResult, generate_metadata
from osm_polygon_image_tag.assets.cache import ResolutionCache
from osm_polygon_image_tag.cli_commands import app
from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.core.progress import ProgressReporter
from osm_polygon_image_tag.integrations.huggingface import HuggingFaceHub
from osm_polygon_image_tag.resolvers.registry import ResolverRegistry
from osm_polygon_image_tag.runtime.console import ConsoleRenderer
from osm_polygon_image_tag.runtime.enrichment import EnrichmentWorker
from osm_polygon_image_tag.runtime.orchestrator import (
    StopToken,
    graceful_stop_signals,
    run_all,
    verify_all,
)
from osm_polygon_image_tag.runtime.preflight import PreflightReport, run_preflight
from osm_polygon_image_tag.runtime.results import RunSummary, VerifySummary

Report = PreflightReport | RunSummary | VerifySummary | MetadataResult | PublicationResult
_renderer: ContextVar[ConsoleRenderer | None] = ContextVar("renderer", default=None)


def _emit_progress(event: dict[str, object]) -> None:
    renderer = _renderer.get()
    if renderer is None:
        renderer = ConsoleRenderer(log_format="json")
    renderer.progress(event)


def _build_enrichment_worker(
    paths: PipelinePaths,
    token: StopToken,
    progress: Callable[[dict[str, object]], None],
) -> EnrichmentWorker:
    return EnrichmentWorker(
        paths.data_root,
        cache_factory=ResolutionCache.open,
        registry_factory=lambda: ResolverRegistry.build(
            environment=os.environ,
            progress=progress,
        ),
        stop_requested=lambda: token.requested,
        progress=progress,
    )


def _execute_metadata_with_checkpoint(data_root: Path, checkpoint_root: Path) -> MetadataResult:
    return generate_metadata(data_root, asset_checkpoint_root=checkpoint_root)


def _run_with_signals(paths: PipelinePaths) -> RunSummary:
    token = StopToken()
    with ProgressReporter(_emit_progress) as reporter, graceful_stop_signals(token):
        return run_all(
            paths,
            stop_token=token,
            metadata_builder=lambda root: generate_metadata(root, progress=reporter.emit),
            progress=reporter.emit,
            enrichment_worker=_build_enrichment_worker(paths, token, reporter.emit),
        )


def _publish(paths: PipelinePaths, confirmation: str) -> PublicationResult:
    _emit_progress({"event": "publication_started"})
    result = publish_dataset(
        paths.data_root,
        confirm_repo=confirmation,
        hub=HuggingFaceHub(),
    )
    _emit_progress({"event": "publication_completed", **result.to_dict()})
    return result


def _run_and_publish(paths: PipelinePaths, confirmation: str) -> RunSummary:
    token = StopToken()
    hub = HuggingFaceHub()

    def publisher(root: Path) -> PublicationResult:
        return publish_dataset(root, confirm_repo=confirmation, hub=hub)

    with ProgressReporter(_emit_progress) as reporter, graceful_stop_signals(token):
        return run_all(
            paths,
            stop_token=token,
            metadata_builder=lambda root: generate_metadata(root, progress=reporter.emit),
            publisher=publisher,
            progress=reporter.emit,
            enrichment_worker=_build_enrichment_worker(paths, token, reporter.emit),
        )


@dataclass(slots=True)
class _Runtime:
    execute_preflight: Callable[[PipelinePaths], PreflightReport]
    execute_run: Callable[[PipelinePaths], RunSummary]
    execute_verify: Callable[[PipelinePaths], VerifySummary]
    execute_metadata: Callable[[Path], MetadataResult]
    execute_metadata_with_checkpoint: Callable[[Path, Path], MetadataResult]
    execute_publish: Callable[[PipelinePaths, str], PublicationResult]
    execute_run_publish: Callable[[PipelinePaths, str], RunSummary]
    renderer: ConsoleRenderer | None = None

    def dispatch(
        self,
        command: str,
        source_root: Path,
        data_root: Path,
        confirmation: str | None,
        log_format: str,
        asset_checkpoint_root: Path | None = None,
    ) -> None:
        self.renderer = ConsoleRenderer(log_format=log_format)
        token = _renderer.set(self.renderer)
        try:
            paths = PipelinePaths.build(source_root=source_root, data_root=data_root)
            _validate_confirmation(confirmation)
            report = self._dispatch_command(command, paths, confirmation, asset_checkpoint_root)
            print(json.dumps(report.to_dict(), sort_keys=True))
        finally:
            _renderer.reset(token)

    def _dispatch_command(
        self,
        command: str,
        paths: PipelinePaths,
        confirmation: str | None,
        asset_checkpoint_root: Path | None,
    ) -> Report:
        if command == "rebuild-metadata":
            return _execute_metadata_command(self, paths, asset_checkpoint_root)
        if command in {"preflight", "run", "verify"}:
            return {
                "preflight": self.execute_preflight,
                "run": self.execute_run,
                "verify": self.execute_verify,
            }[command](paths)
        return {
            "publish": self.execute_publish,
            "run-and-publish": self.execute_run_publish,
        }[command](paths, confirmation or "")


def _validate_confirmation(confirmation: str | None) -> None:
    if confirmation is not None and confirmation != EXPECTED_REPO:
        raise ImageTagPipelineError(f"repository confirmation must equal {EXPECTED_REPO}")


def _execute_metadata_command(
    runtime: _Runtime, paths: PipelinePaths, checkpoint_root: Path | None
) -> MetadataResult:
    if checkpoint_root is None:
        return runtime.execute_metadata(paths.data_root)
    return runtime.execute_metadata_with_checkpoint(paths.data_root, checkpoint_root)


def run(
    argv: Sequence[str] | None = None,
    *,
    execute_preflight: Callable[[PipelinePaths], PreflightReport] = run_preflight,
    execute_run: Callable[[PipelinePaths], RunSummary] = _run_with_signals,
    execute_verify: Callable[[PipelinePaths], VerifySummary] = verify_all,
    execute_metadata: Callable[[Path], MetadataResult] = generate_metadata,
    execute_metadata_with_checkpoint: Callable[[Path, Path], MetadataResult]
    | None = _execute_metadata_with_checkpoint,
    execute_publish: Callable[[PipelinePaths, str], PublicationResult] = _publish,
    execute_run_publish: Callable[[PipelinePaths, str], RunSummary] = _run_and_publish,
) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    runtime = _Runtime(
        execute_preflight,
        execute_run,
        execute_verify,
        execute_metadata,
        execute_metadata_with_checkpoint or _execute_metadata_with_checkpoint,
        execute_publish,
        execute_run_publish,
    )
    try:
        return _invoke_runtime(arguments, runtime)
    finally:
        if runtime.renderer is not None:
            runtime.renderer.close()


def _invoke_runtime(arguments: Sequence[str], runtime: _Runtime) -> int:
    try:
        _invoke_app(arguments, runtime)
    except click.exceptions.Exit as error:
        raise SystemExit(error.exit_code) from error
    except click.ClickException as error:
        error.show(file=sys.stderr)
        return 2
    except ImageTagPipelineError as error:
        _render_pipeline_error(runtime, error)
        return 2
    return 0


def _invoke_app(arguments: Sequence[str], runtime: _Runtime) -> None:
    app(
        args=list(arguments),
        prog_name="osm-polygon-image-tag",
        standalone_mode=False,
        obj=runtime,
    )
    if "--help" in arguments:
        raise SystemExit(0)


def _render_pipeline_error(runtime: _Runtime, error: ImageTagPipelineError) -> None:
    renderer = runtime.renderer or ConsoleRenderer(log_format="json")
    renderer.error(str(error))
