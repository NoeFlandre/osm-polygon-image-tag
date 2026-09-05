"""Terminal rendering kept separate from pipeline and progress contracts."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from typing import TextIO

from rich.console import Console
from tqdm import tqdm

HumanProgress = Callable[[dict[str, object]], None]
HumanError = Callable[[str], None]

_SECRET_QUERY = re.compile(
    r"(?i)(access_token|api_key|apikey|key|token|secret|signature)=([^&\s]+)"
)


def _redact(message: str) -> str:
    return _SECRET_QUERY.sub(r"\1=REDACTED", message)


class ConsoleRenderer:
    """Render stable JSON for automation and restrained UI for operators."""

    def __init__(
        self,
        *,
        log_format: str = "auto",
        stderr: TextIO | None = None,
        human_progress: HumanProgress | None = None,
        human_error: HumanError | None = None,
    ) -> None:
        self._stderr = stderr or sys.stderr
        self._human = _is_human(log_format, self._stderr)
        self._console = Console(file=self._stderr, stderr=True)
        self._bar: tqdm[object] | None = None
        self._human_progress = human_progress or self._render_progress
        self._human_error = human_error or self._render_error

    def progress(self, event: dict[str, object]) -> None:
        if self._human:
            self._human_progress(event)
            return
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        print(f"progress {payload}", file=self._stderr, flush=True)

    def error(self, message: str) -> None:
        safe = _redact(message)
        if self._human:
            self._human_error(safe)
            return
        print(f"error: {safe}", file=self._stderr, flush=True)

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    def _render_error(self, message: str) -> None:
        self._console.print(f"[bold red]error:[/bold red] {message}")

    def _render_progress(self, event: dict[str, object]) -> None:
        name = str(event.get("event", "progress"))
        if self._handle_bar_event(name, event):
            return
        self._console.print(f"[cyan]{name}[/cyan]{_event_details(event)}")

    def _handle_bar_event(self, name: str, event: dict[str, object]) -> bool:
        if name == "asset_backfill_started":
            self._start_asset_bar(event)
            return True
        if _is_asset_completion(name, self._bar is not None):
            assert self._bar is not None
            self._bar.update(1)
            return True
        if _is_asset_finish(name, self._bar is not None):
            self.close()
            return True
        return False

    def _start_asset_bar(self, event: dict[str, object]) -> None:
        count = event.get("asset_count", 0)
        self._bar = tqdm(
            total=count if isinstance(count, int) else 0,
            desc="Image assets",
            file=self._stderr,
            dynamic_ncols=True,
        )


def _is_asset_completion(name: str, has_bar: bool) -> bool:
    return name == "asset_shard_completed" and has_bar


def _is_asset_finish(name: str, has_bar: bool) -> bool:
    return name == "asset_backfill_completed" and has_bar


def _is_human(log_format: str, stderr: TextIO) -> bool:
    return log_format == "human" or (log_format == "auto" and stderr.isatty())


def _event_details(event: dict[str, object]) -> str:
    details = " ".join(f"{key}={value}" for key, value in sorted(event.items()) if key != "event")
    return f" {details}" if details else ""
