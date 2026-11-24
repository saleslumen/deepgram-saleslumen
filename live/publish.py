"""Map execution steps to live feed events."""

from execution.steps import Awaiting, Running, Step, Succeeded

from live.events import WorkflowStepEvent


def workflow_step_event(call_id: str, execution_id: str, version: int, step: Step, workflow: str) -> WorkflowStepEvent:
    if isinstance(step, Running):
        return WorkflowStepEvent(call_id=call_id, execution_id=execution_id, version=version, state="running", workflow=workflow)
    if isinstance(step, Awaiting):
        return WorkflowStepEvent(
            call_id=call_id,
            execution_id=execution_id,
            version=version,
            state="awaiting",
            workflow=workflow,
            step_path=step.step_path,
            sequence=step.sequence,
        )
    if isinstance(step, Succeeded):
        return WorkflowStepEvent(call_id=call_id, execution_id=execution_id, version=version, state="succeeded", workflow=workflow)
    return WorkflowStepEvent(call_id=call_id, execution_id=execution_id, version=version, state="failed", workflow=workflow)