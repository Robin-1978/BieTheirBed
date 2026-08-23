"""Task repository domain errors."""

from knoa_platform.tasks.models import TaskPreflightResult


class TaskNotFoundError(LookupError):
    pass


class TaskIdempotencyConflictError(RuntimeError):
    pass


class TaskTransitionError(RuntimeError):
    pass


class TaskAlreadyActiveError(TaskTransitionError):
    pass


class TaskPreflightBlockedError(TaskTransitionError):
    def __init__(self, result: TaskPreflightResult) -> None:
        super().__init__("Task launch preflight is blocked")
        self.result = result


class TaskCapacityError(RuntimeError):
    pass
