import pytest

from searcharis.models import WorkflowState
from searcharis.state_machine import InvalidTransitionError, assert_transition


def test_primary_transition_received_to_auditing_is_allowed():
    assert_transition(WorkflowState.RECEIVED, WorkflowState.AUDITING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowState.DECIDED, WorkflowState.RESOLVED),
        (WorkflowState.RECEIVED, WorkflowState.ACTIONED),
        (WorkflowState.FAILED_TERMINAL, WorkflowState.ACTIONED),
    ],
)
def test_invalid_transitions_are_rejected(current, target):
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)
