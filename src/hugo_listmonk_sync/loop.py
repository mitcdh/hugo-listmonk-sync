"""Immediate-start fixed-interval process loop and signal handling."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from types import FrameType

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.reconcile import Synchronizer

logger = logging.getLogger(__name__)


class ServiceLoop:
    """Run synchronization immediately and after each completed interval."""

    def __init__(
        self,
        config: Config,
        synchronizer: Synchronizer,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._config = config
        self._synchronizer = synchronizer
        self.stop_event = stop_event or threading.Event()

    def run(self) -> None:
        """Run until configured one-shot completion or shutdown."""
        while not self.stop_event.is_set():
            self._run_cycle_safely()
            if self._config.run_once or self.stop_event.is_set():
                return
            logger.debug(
                "Waiting %d seconds before the next synchronization",
                self._config.poll_interval_seconds,
            )
            if self.stop_event.wait(self._config.poll_interval_seconds):
                return

    def _run_cycle_safely(self) -> None:
        try:
            self._synchronizer.run_cycle()
        except Exception:
            logger.exception(
                "Synchronization cycle failed; it will be retried next interval"
            )


def install_signal_handlers(
    stop_event: threading.Event,
) -> Callable[[], None]:
    """Install SIGTERM/SIGINT handlers and return a restoration callback."""
    handled_signals = (signal.SIGTERM, signal.SIGINT)
    previous = {signum: signal.getsignal(signum) for signum in handled_signals}

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received %s; shutting down", signal.Signals(signum).name)
        stop_event.set()

    for signum in handled_signals:
        signal.signal(signum, handle_signal)

    def restore() -> None:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    return restore
