"""Declarative Typer command surface, separate from execution wiring."""

from enum import Enum
from pathlib import Path
from typing import Annotated, Protocol, runtime_checkable

import typer


class LogFormat(str, Enum):
    AUTO = "auto"
    JSON = "json"
    HUMAN = "human"


@runtime_checkable
class CommandRuntime(Protocol):
    def dispatch(
        self,
        command: str,
        source_root: Path,
        data_root: Path,
        confirmation: str | None,
        log_format: str,
        asset_checkpoint_root: Path | None = None,
    ) -> None: ...


app = typer.Typer(add_completion=False, rich_markup_mode=None)


def _dispatch(
    context: typer.Context,
    command: str,
    source_root: Path,
    data_root: Path,
    log_format: LogFormat,
    confirmation: str | None = None,
    asset_checkpoint_root: Path | None = None,
) -> None:
    runtime = context.obj
    if not isinstance(runtime, CommandRuntime):
        raise RuntimeError("CLI runtime was not configured")
    runtime.dispatch(
        command,
        source_root,
        data_root,
        confirmation,
        log_format.value,
        asset_checkpoint_root,
    )


@app.command()
def preflight(
    context: typer.Context,
    source_root: Annotated[Path, typer.Option("--source-root")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    log_format: Annotated[LogFormat, typer.Option("--log-format")] = LogFormat.AUTO,
) -> None:
    _dispatch(context, "preflight", source_root, data_root, log_format)


@app.command("run")
def run_command(
    context: typer.Context,
    source_root: Annotated[Path, typer.Option("--source-root")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    log_format: Annotated[LogFormat, typer.Option("--log-format")] = LogFormat.AUTO,
) -> None:
    _dispatch(context, "run", source_root, data_root, log_format)


@app.command()
def verify(
    context: typer.Context,
    source_root: Annotated[Path, typer.Option("--source-root")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    log_format: Annotated[LogFormat, typer.Option("--log-format")] = LogFormat.AUTO,
) -> None:
    _dispatch(context, "verify", source_root, data_root, log_format)


@app.command("rebuild-metadata")
def rebuild_metadata(
    context: typer.Context,
    source_root: Annotated[Path, typer.Option("--source-root")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    asset_checkpoint_root: Annotated[
        Path | None,
        typer.Option(
            "--asset-checkpoint-root",
            help="Local directory for the resumable image-dedup SQLite checkpoint.",
        ),
    ] = None,
    log_format: Annotated[LogFormat, typer.Option("--log-format")] = LogFormat.AUTO,
) -> None:
    _dispatch(
        context,
        "rebuild-metadata",
        source_root,
        data_root,
        log_format,
        asset_checkpoint_root=asset_checkpoint_root,
    )


@app.command()
def publish(
    context: typer.Context,
    source_root: Annotated[Path, typer.Option("--source-root")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    confirm_repo: Annotated[str, typer.Option("--confirm-repo")],
    log_format: Annotated[LogFormat, typer.Option("--log-format")] = LogFormat.AUTO,
) -> None:
    _dispatch(context, "publish", source_root, data_root, log_format, confirm_repo)


@app.command("run-and-publish")
def run_and_publish(
    context: typer.Context,
    source_root: Annotated[Path, typer.Option("--source-root")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    confirm_repo: Annotated[str, typer.Option("--confirm-repo")],
    log_format: Annotated[LogFormat, typer.Option("--log-format")] = LogFormat.AUTO,
) -> None:
    _dispatch(context, "run-and-publish", source_root, data_root, log_format, confirm_repo)
