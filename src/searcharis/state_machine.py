from searcharis.models import WorkflowState


class InvalidTransitionError(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.RECEIVED: {
        WorkflowState.AUDITING,
        WorkflowState.FAILED_RETRYABLE,
        WorkflowState.FAILED_TERMINAL,
    },
    WorkflowState.AUDITING: {
        WorkflowState.DECIDED,
        WorkflowState.FAILED_RETRYABLE,
        WorkflowState.NEEDS_REVIEW,
    },
    WorkflowState.DECIDED: {
        WorkflowState.ACTIONED,
        WorkflowState.VERIFYING,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.FAILED_RETRYABLE,
    },
    WorkflowState.ACTIONED: {
        WorkflowState.VERIFYING,
        WorkflowState.RESOLVED,
        WorkflowState.FAILED_RETRYABLE,
    },
    WorkflowState.VERIFYING: {
        WorkflowState.ACTIONED,
        WorkflowState.RESOLVED,
        WorkflowState.FAILED_RETRYABLE,
        WorkflowState.NEEDS_REVIEW,
    },
    WorkflowState.FAILED_RETRYABLE: {
        WorkflowState.AUDITING,
        WorkflowState.VERIFYING,
        WorkflowState.FAILED_TERMINAL,
    },
    WorkflowState.NEEDS_REVIEW: {
        WorkflowState.AUDITING,
        WorkflowState.VERIFYING,
    },
    WorkflowState.RESOLVED: set(),
    WorkflowState.FAILED_TERMINAL: set(),
}


def assert_transition(current: WorkflowState, target: WorkflowState) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"invalid workflow transition: {current} -> {target}")
