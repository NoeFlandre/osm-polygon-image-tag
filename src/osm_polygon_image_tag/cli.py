import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from osm_polygon_image_tag.config import PipelinePaths
from osm_polygon_image_tag.errors import ImageTagPipelineError
from osm_polygon_image_tag.preflight import PreflightReport, run_preflight


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osm-polygon-image-tag")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--source-root", type=Path, required=True)
    preflight.add_argument("--data-root", type=Path, required=True)
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    execute_preflight: Callable[[PipelinePaths], PreflightReport] = run_preflight,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = PipelinePaths.build(
            source_root=arguments.source_root,
            data_root=arguments.data_root,
        )
        report = execute_preflight(paths)
    except ImageTagPipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0
