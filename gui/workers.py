from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class WorkerSignals(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(Exception)
    progress = pyqtSignal(int)


class RunnableTask(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        # Provide a progress callback only if the function supports it
        try:
            sig = inspect.signature(fn)
            if "progress_callback" in sig.parameters and "progress_callback" not in self.kwargs:
                self.kwargs["progress_callback"] = self.signals.progress.emit
        except Exception:
            # If inspection fails, do not inject progress callback
            pass

    def run(self):
        try:

            def _callable_name(c: Callable[..., Any]) -> str:
                # Try simple function/method name
                name = getattr(c, "__name__", None)
                if name:
                    return name
                # Support functools.partial
                func = getattr(c, "func", None)
                if func is not None:
                    base = getattr(func, "__name__", func.__class__.__name__)
                    return f"partial({base})"
                # Fallback to class name or repr
                return getattr(c, "__class__", type(c)).__name__

            logging.getLogger(__name__).debug(f"Starting background task {_callable_name(self.fn)}")
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            logging.getLogger(__name__).exception("Background task failed")
            self.signals.error.emit(e)


def run_in_background(
    pool: QThreadPool,
    fn: Callable[..., Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[Exception], None] | None = None,
    on_progress: Callable[[int], None] | None = None,
    *args,
    **kwargs,
):
    task = RunnableTask(fn, *args, **kwargs)
    if on_done is not None:
        task.signals.finished.connect(on_done)
    if on_error is not None:
        task.signals.error.connect(on_error)
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    pool.start(task)
