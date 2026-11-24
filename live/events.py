"""Typed live-call events for the localhost call view."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

StepState = Literal["running", "awaiting", "succeeded", "failed"]
TranscriptRole = Literal["user", "assistant"]


class CallStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["call_started"] = "call_started"
    call_id: str


class CallEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["call_ended"] = "call_ended"
    call_id: str


class TranscriptEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["transcript"] = "transcript"
    call_id: str
    role: TranscriptRole
    text: str


class FlowNodeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["flow_node"] = "flow_node"
    call_id: str
    node: str
    graph_node: str
    function: str | None = None


class WorkflowStepEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["workflow_step"] = "workflow_step"
    call_id: str
    execution_id: str
    version: int
    state: StepState
    workflow: str
    step_path: str = ""
    sequence: int | None = None
    @model_validator(mode="after")
    def awaiting_fields(self) -> Self:
        if self.state == "awaiting":
            if self.step_path == "":
                raise ValueError("step_path required when state is awaiting")
            if self.sequence is None:
                raise ValueError("sequence required when state is awaiting")
        return self


LiveEvent = CallStarted | CallEnded | TranscriptEvent | FlowNodeEvent | WorkflowStepEvent