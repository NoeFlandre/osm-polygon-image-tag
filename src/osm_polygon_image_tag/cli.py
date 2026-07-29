import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.errors import ImageTagPipelineError
from osm_polygon_image_tag.orchestrator import (
    RunSummary,
    StopToken,
    VerifySummary,
    graceful_stop_signals,
    run_all,
    verify_all,
)
from osm_polygon_image_tag.preflight import PreflightReport, run_preflight
from osm_polygon_image_tag.reporting import MetadataResult, generate_metadata


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
    return parser


def _run_with_signals(paths: PipelinePaths) -> RunSummary:
    token = StopToken()
    with graceful_stop_signals(token):
        return run_all(paths, stop_token=token)


def run(
    argv: Sequence[str] | None = None,
    *,
    execute_preflight: Callable[[PipelinePaths], PreflightReport] = run_preflight,
    execute_run: Callable[[PipelinePaths], RunSummary] = _run_with_signals,
    execute_verify: Callable[[PipelinePaths], VerifySummary] = verify_all,
    execute_metadata: Callable[[Path], MetadataResult] = generate_metadata,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = PipelinePaths.build(
            source_root=arguments.source_root,
            data_root=arguments.data_root,
        )
        if arguments.command == "preflight":
            report: PreflightReport | RunSummary | VerifySummary | MetadataResult = (
                execute_preflight(paths)
            )
        elif arguments.command == "run":
            report = execute_run(paths)
        elif arguments.command == "verify":
            report = execute_verify(paths)
        else:
            report = execute_metadata(paths.data_root)
    except ImageTagPipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0
