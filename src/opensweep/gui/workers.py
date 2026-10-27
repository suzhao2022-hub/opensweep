"""Run blocking work on a thread pool and get the result back on the UI thread."""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

# Tasks stay referenced here until their last signal has been delivered on the UI thread.
# Without this, Python could free a task (and its signal object) while the worker thread is
# still emitting from it, which crashes the app or silently drops the result.
_alive: set["Task"] = set()


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, fn: Callable, args, kwargs, with_progress: bool):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        if with_progress:
            self.kwargs["progress"] = self.signals.progress.emit

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except BaseException as exc:  # report to the UI instead of dying silently
            traceback.print_exc()
            self._emit(self.signals.error, f"{type(exc).__name__}: {exc}")
        else:
            self._emit(self.signals.done, result)
        finally:
            self._emit(self.signals.finished)

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass  # the app is closing and the receiver is gone


def run_task(fn: Callable, *args, on_done: Callable | None = None, on_error: Callable | None = None,
             on_progress: Callable | None = None, **kwargs) -> Task:
    task = Task(fn, args, kwargs, with_progress=on_progress is not None)
    _alive.add(task)
    if on_done:
        task.signals.done.connect(on_done)
    task.signals.error.connect(on_error or _report_error)
    if on_progress:
        task.signals.progress.connect(on_progress)
    task.signals.finished.connect(lambda: _alive.discard(task))
    QThreadPool.globalInstance().start(task)
    return task


def _report_error(message: str) -> None:
    """Errors nobody asked to handle are shown instead of disappearing."""
    from opensweep.gui.errors import show_error
    show_error(message)
