import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from osm_polygon_image_tag.core.config import PipelinePaths
from osm_polygon_image_tag.core.errors import ImageTagPipelineError
from osm_polygon_image_tag.runtime.orchestrator import (
    RunSummary,
    StopToken,
    VerifySummary,
    graceful_stop_signals,
    run_all,
    verify_all,
)
from osm_polygon_image_tag.runtime.preflight import PreflightReport, run_preflight
from osm_polygon_image_tag.core.progress import ProgressReporter
from osm_polygon_image_tag.artifacts.publication import (
    EXPECTED_REPO,
    PublicationResult,
    publish_dataset,
)
from osm_polygon_image_tag.integrations.huggingface import HuggingFaceHub
from osm_polygon_image_tag.artifacts.reporting import MetadataResult, generate_metadata


def _emit_progress(event: dict[str, object]) -> None:
    print(
        f"progress {json.dumps(event, sort_keys=True, separators=(',', ':'))}",
        file=sys.stderr,
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osm-polygon-image-tag")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--source-root", type=Path, required=True)
    preflight.add_argument("--data-root", type=Path, required=True)
    local_run = commands.add_parser("run")
    local_run.add_argument("--source-root", type=Path, required=True)
    local_run.add_argument("--data-root", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--source-root", type=Path, required=True)
    verify.add_argument("--data-root", type=Path, required=True)
    metadata = commands.add_parser("rebuild-metadata")
    metadata.add_argument("--source-root", type=Path, required=True)
    metadata.add_argument("--data-root", type=Path, required=True)
    for command in ("publish", "run-and-publish"):
        publication = commands.add_parser(command)
        publication.add_argument("--source-root", type=Path, required=True)
        publication.add_argument("--data-root", type=Path, required=True)
        publication.add_argument("--confirm-repo", required=True)
    return parser


def _run_with_signals(paths: PipelinePaths) -> RunSummary:
    token = StopToken()
    with ProgressReporter(_emit_progress) as reporter, graceful_stop_signals(token):
        return run_all(
            paths,
            stop_token=token,
            metadata_builder=lambda root: generate_metadata(root, progress=reporter.emit),
            progress=reporter.emit,
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
        )


def run(
    argv: Sequence[str] | None = None,
    *,
    execute_preflight: Callable[[PipelinePaths], PreflightReport] = run_preflight,
    execute_run: Callable[[PipelinePaths], RunSummary] = _run_with_signals,
    execute_verify: Callable[[PipelinePaths], VerifySummary] = verify_all,
    execute_metadata: Callable[[Path], MetadataResult] = generate_metadata,
    execute_publish: Callable[[PipelinePaths, str], PublicationResult] = _publish,
    execute_run_publish: Callable[[PipelinePaths, str], RunSummary] = _run_and_publish,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = PipelinePaths.build(
            source_root=arguments.source_root,
            data_root=arguments.data_root,
        )
        if arguments.command in {"publish", "run-and-publish"} and (
            arguments.confirm_repo != EXPECTED_REPO
        ):
            raise ImageTagPipelineError(f"repository confirmation must equal {EXPECTED_REPO}")
        if arguments.command == "preflight":
            report: (
                PreflightReport | RunSummary | VerifySummary | MetadataResult | PublicationResult
            ) = execute_preflight(paths)
        elif arguments.command == "run":
            report = execute_run(paths)
        elif arguments.command == "verify":
            report = execute_verify(paths)
        elif arguments.command == "rebuild-metadata":
            report = execute_metadata(paths.data_root)
        elif arguments.command == "publish":
            report = execute_publish(paths, arguments.confirm_repo)
        else:
            report = execute_run_publish(paths, arguments.confirm_repo)
    except ImageTagPipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0
