import threading
import time
from collections.abc import Callable
from types import TracebackType

Progress = Callable[[dict[str, object]], None]


class ProgressReporter:
    def __init__(self, sink: Progress, *, heartbeat_seconds: float = 30.0) -> None:
        self._sink = sink
        self._heartbeat_seconds = heartbeat_seconds
        self._started = time.monotonic()
        self._last_event = "starting"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ProgressReporter":
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="progress-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def emit(self, event: dict[str, object]) -> None:
        self._last_event = str(event["event"])
        self._sink(event)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            self._sink(
                {
                    "event": "heartbeat",
                    "last_event": self._last_event,
                    "elapsed_seconds": int(time.monotonic() - self._started),
                }
            )
