"""Task repository domain errors."""


class TaskNotFoundError(LookupError):
    pass


class TaskIdempotencyConflictError(RuntimeError):
    pass


class TaskTransitionError(RuntimeError):
    pass


class TaskAlreadyActiveError(TaskTransitionError):
    pass


class TaskCapacityError(RuntimeError):
    pass
