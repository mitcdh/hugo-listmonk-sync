from __future__ import annotations

import signal
import threading
from dataclasses import dataclass

from hugo_listmonk_sync.loop import ServiceLoop, install_signal_handlers


@dataclass
class StubSynchronizer:
    failures_remaining: int = 0
    calls: int = 0

    def run_cycle(self):
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("temporary failure")


class RecordingEvent:
    def __init__(self, wait_results):
        self.wait_results = iter(wait_results)
        self.waits = []
        self.set_called = False

    def is_set(self):
        return self.set_called

    def set(self):
        self.set_called = True

    def wait(self, timeout):
        self.waits.append(timeout)
        return next(self.wait_results)


def test_run_once_synchronizes_immediately_without_waiting(make_config):
    config = make_config(run_once=True)
    synchronizer = StubSynchronizer()
    event = RecordingEvent([])

    ServiceLoop(config, synchronizer, stop_event=event).run()

    assert synchronizer.calls == 1
    assert event.waits == []


def test_repeating_loop_waits_full_interval_after_each_completed_run(make_config):
    config = make_config(run_once=False, poll_interval_seconds=17)
    synchronizer = StubSynchronizer()
    event = RecordingEvent([False, True])

    ServiceLoop(config, synchronizer, stop_event=event).run()

    assert synchronizer.calls == 2
    assert event.waits == [17, 17]


def test_runtime_cycle_failure_is_retried_after_interval(make_config):
    config = make_config(run_once=False, poll_interval_seconds=8)
    synchronizer = StubSynchronizer(failures_remaining=1)
    event = RecordingEvent([False, True])

    ServiceLoop(config, synchronizer, stop_event=event).run()

    assert synchronizer.calls == 2
    assert event.waits == [8, 8]


def test_preexisting_shutdown_prevents_new_cycle(make_config):
    config = make_config(run_once=False)
    synchronizer = StubSynchronizer()
    event = RecordingEvent([])
    event.set()

    ServiceLoop(config, synchronizer, stop_event=event).run()

    assert synchronizer.calls == 0


def test_signal_handler_sets_shutdown_event_and_restores_handlers():
    event = threading.Event()
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)

    restore = install_signal_handlers(event)
    try:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert event.is_set()
    finally:
        restore()

    assert signal.getsignal(signal.SIGTERM) == old_term
    assert signal.getsignal(signal.SIGINT) == old_int
