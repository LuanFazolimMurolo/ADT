"""Graceful process-signal integration for persistent ADT runtimes."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable


def install_stop_handlers(stop: Callable[[], None]) -> None:
    """Translate SIGINT/SIGTERM into one cooperative supervisor stop request."""

    loop = asyncio.get_running_loop()

    for process_signal in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        try:
            loop.add_signal_handler(
                process_signal,
                stop,
            )
        except (NotImplementedError, RuntimeError):
            # add_signal_handler is not available on every event-loop
            # implementation. Linux containers support it.
            continue
