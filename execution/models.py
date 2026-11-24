"""Pydantic wire models validated at the Workflows client boundary."""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator


class State(StrEnum):
    STATE_QUEUED = "STATE_QUEUED"
    STATE_ACTIVE = "STATE_ACTIVE"
    STATE_WAITING = "STATE_WAITING"
    STATE_SUCCEEDED = "STATE_SUCCEEDED"
    STATE_FAILED = "STATE_FAILED"
    STATE_CANCELLED = "STATE_CANCELLED"
    STATE_REJECTED = "STATE_REJECTED"


class WaitKind(StrEnum):
    KIND_TIMER = "KIND_TIMER"
    KIND_SIGNAL = "KIND_SIGNAL"
    KIND_CHILD = "KIND_CHILD"


TERMINAL_STATES = {State.STATE_SUCCEEDED, State.STATE_FAILED, State.STATE_CANCELLED, State.STATE_REJECTED}
_GRPC_CODES = ("OK", "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS", "UNAUTHENTICATED")


def grpc_name(code: int | None, status: str | None) -> str:
    if isinstance(code, int) and not isinstance(code, bool) and 0 <= code < len(_GRPC_CODES):
        return _GRPC_CODES[code]
    return (status or "").strip()


class Wait(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)
    kind: WaitKind
    step_path: str = Field(default="", validation_alias="stepPath")
    sequence: int
    signal: str = ""
    target_step_run: str = ""


class Observation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)
    name: str = Field(min_length=1)
    uid: str = Field(min_length=1)
    revision: str = ""
    state: State
    version: int = Field(ge=1, validation_alias="observationVersion")
    waits: tuple[Wait, ...] = ()
    result: Any = None
    @model_validator(mode="after")
    def complete(self) -> Self:
        built = tuple(item.model_copy(update={"target_step_run": f"{self.name}/stepRuns/{item.sequence}"}) for item in self.waits)
        return self.model_copy(update={"waits": built})


class WorkflowDisplay(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    display_name: str | None = Field(default=None, validation_alias="displayName")


class ExecutionResource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1)
    uid: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    execution_id: str = ""
    @model_validator(mode="after")
    def matches_workflow(self, info: ValidationInfo) -> Self:
        workflow_id = info.context.get("workflow_id") if info.context else None
        if not isinstance(workflow_id, str) or workflow_id == "":
            return self
        exec_prefix = f"workflows/{workflow_id}/executions/"
        if not self.name.startswith(exec_prefix):
            raise ValueError("execution name is not for this workflow")
        execution_id = self.name.removeprefix(exec_prefix)
        if execution_id == "" or "/" in execution_id:
            raise ValueError("execution name is not for this workflow")
        rev_prefix = f"workflows/{workflow_id}/revisions/"
        revision_id = self.revision.removeprefix(rev_prefix)
        if not self.revision.startswith(rev_prefix) or revision_id == "" or "/" in revision_id:
            raise ValueError("execution revision is not for this workflow")
        return self.model_copy(update={"execution_id": execution_id})


class RevisionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    definition: "RevisionDefinition"


class AwaitSignalNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    signal: str


class BranchNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    steps: list["StepNode"] = Field(default_factory=list)


class ParallelNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    branches: list[BranchNode] = Field(default_factory=list)


class ForEachNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    steps: list["StepNode"] = Field(default_factory=list)


class StepNode(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str
    label: str | None = None
    await_signal: AwaitSignalNode | None = Field(default=None, validation_alias="awaitSignal")
    parallel: ParallelNode | None = None
    for_each: ForEachNode | None = Field(default=None, validation_alias="for_each")


class RevisionDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    signals: dict[str, str] = Field(default_factory=dict)
    steps: list[StepNode] = Field(default_factory=list)


StepNode.model_rebuild()
BranchNode.model_rebuild()
RevisionEnvelope.model_rebuild()


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    type_url: str = Field(default="", validation_alias="@type")
    reason: str = ""
    metadata: dict[str, str] = {}


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: int | None = None
    status: str | None = None
    message: str = ""
    details: list[ErrorInfo] = []


class StatusEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    error: StatusBody | None = None
    code: int | None = None
    status: str | None = None
    message: str = ""
    details: list[ErrorInfo] = []
    def fields(self, text: str) -> tuple[str, str, str, dict[str, str]]:
        body = self.error if self.error is not None else StatusBody(code=self.code, status=self.status, message=self.message, details=self.details)
        message = (body.message or text)[:300]
        info = next((item for item in body.details if item.type_url.endswith("google.rpc.ErrorInfo")), None)
        if info is None:
            return grpc_name(body.code, body.status), message, "", {}
        return grpc_name(body.code, body.status), message, info.reason, dict(info.metadata)
