"""Bounded synchronous calls without non-daemon executor shutdown hangs.

POSIX main-thread calls use a temporary signal timer where available. The
portable fallback uses a daemon thread; a timed-out call may continue remotely.
In either case the adapter is quarantined and must not be reused or cleaned up.
This is a caller-side deadline, not a transactional rollback or an OS sandbox.
"""
from __future__ import annotations

import queue
import signal
import threading
from collections.abc import Callable
from time import monotonic
from typing import TypeVar

from memorycheck.errors import AdapterConfigurationError, OperationTimeoutError

T = TypeVar("T")


class _DeadlineInterrupt(BaseException):
    pass


def quarantined(adapter: object) -> bool:
    return bool(getattr(adapter, "_memorycheck_quarantined", False))


class Deadlines:
    def __init__(self, adapter: object, mode: str = "auto"):
        self.adapter, self.mode = adapter, mode

    def _expired(self, operation: str, budget: float) -> OperationTimeoutError:
        setattr(self.adapter, "_memorycheck_quarantined", True)
        return OperationTimeoutError(
            f"{operation} exceeded its {budget:.3g}s deadline. Invariant was not evaluated; "
            "adapter quarantined because a write may still be in flight."
        )

    def call(self, action: Callable[[], T], *, operation: str, timeout: float,
             contract_deadline: float | None = None) -> T:
        if quarantined(self.adapter):
            raise OperationTimeoutError("Adapter is quarantined after a timeout; create a fresh adapter")
        budget = timeout if contract_deadline is None else min(timeout, contract_deadline - monotonic())
        if budget <= 0:
            # No new operation has started, so no in-flight write needs quarantining.
            raise OperationTimeoutError("Contract deadline expired before the next operation. Invariant was not evaluated.")
        available = (hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread())
        active_timer = available and signal.getitimer(signal.ITIMER_REAL)[0] > 0
        use_signal = self.mode != "thread" and available and not active_timer
        if self.mode == "signal" and not use_signal:
            raise AdapterConfigurationError("Signal deadlines need a POSIX main thread with no active SIGALRM timer; use timeout_mode='thread'")
        start = monotonic()
        if use_signal:
            old_handler = signal.getsignal(signal.SIGALRM)
            def interrupt(_signum, _frame):
                raise _DeadlineInterrupt()
            signal.signal(signal.SIGALRM, interrupt)
            signal.setitimer(signal.ITIMER_REAL, budget)
            try:
                result = action()
            except _DeadlineInterrupt:
                raise self._expired(operation, budget) from None
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_handler)
        else:
            completed: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
            def invoke() -> None:
                try:
                    completed.put((True, action()))
                except BaseException as exc:
                    completed.put((False, exc))
            threading.Thread(target=invoke, name="memorycheck-operation", daemon=True).start()
            try:
                ok, result = completed.get(timeout=budget)
            except queue.Empty:
                raise self._expired(operation, budget) from None
            if not ok:
                raise result  # type: ignore[misc]
        if monotonic() - start > budget:
            raise self._expired(operation, budget)
        return result  # type: ignore[return-value]


def close_adapter(adapter: object, *, timeout: float = 10.0, mode: str = "auto") -> bool:
    """Do not race a pending call with close(); return False when quarantined."""
    if quarantined(adapter):
        return False
    Deadlines(adapter, mode).call(adapter.close, operation="close", timeout=timeout)  # type: ignore[attr-defined]
    return True
