"""Per-worker progress and cancellation, without changing model prompts."""
from contextvars import ContextVar

observer = ContextVar('task_observer', default=None)
task_id = ContextVar('task_id', default=None)


class TaskCancelled(Exception):
    pass


def progress(stage=None):
    callback = observer.get()
    if callback:
        callback(stage)


def clinical_case(case):
    return {k:v for k,v in case.items() if k not in ('days','budget','shipping','discount','as_of')}
