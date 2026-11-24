import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from execution.models import BranchNode, Observation, RevisionDefinition, StepNode

UNSUPPORTED = "This workflow cannot be handled by this voice client."
FAILED_LINE = "The workflow failed."
_SAFE = {
    "STATE_FAILED": FAILED_LINE,
    "STATE_CANCELLED": "The workflow was cancelled.",
    "STATE_REJECTED": "The workflow was rejected.",
}
_TYPES = {"string": "string", "number": "number", "integer": "integer", "bool": "boolean"}
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INDEX = re.compile(r"^(.+)\[(\d+)\]$")


class StepError(Exception):
    pass


class AnswerKind(Enum):
    NOTICE = "notice"
    QUESTION = "question"


@dataclass(frozen=True)
class Argument:
    name: str
    json_type: str


@dataclass(frozen=True)
class _Step:
    path: str
    label: str
    signal: str
    arguments: tuple[Argument, ...]


@dataclass(frozen=True)
class Index:
    steps: tuple[_Step, ...]

    def resolve(self, step_path: str, signal: str) -> _Step:
        matches = [step for step in self.steps if _same_path(step.path, step_path)]
        if len(matches) != 1:
            raise StepError("await signal path does not match")
        step = matches[0]
        if step.signal != signal:
            raise StepError("await signal does not match the wait")
        return step


@dataclass(frozen=True)
class Running:
    pass


@dataclass(frozen=True)
class Awaiting:
    label: str
    signal: str
    sequence: int
    step_path: str
    arguments: tuple[Argument, ...]


@dataclass(frozen=True)
class Succeeded:
    result: Any


@dataclass(frozen=True)
class Failed:
    message: str


Step = Running | Awaiting | Succeeded | Failed


def answer_kind(arguments: tuple[Argument, ...]) -> AnswerKind:
    if len(arguments) == 0:
        return AnswerKind.NOTICE
    return AnswerKind.QUESTION


def compile_index(definition: RevisionDefinition) -> Index:
    found: list[_Step] = []
    _walk(definition.steps, "", definition.signals, found)
    if len({step.path for step in found}) != len(found):
        raise StepError("await signal path is ambiguous")
    return Index(tuple(found))


def project(index: Index | None, observation: Observation) -> Step:
    if observation.state == "STATE_SUCCEEDED":
        return Succeeded(observation.result)
    if observation.state in _SAFE:
        return Failed(_SAFE[observation.state])
    if index is None:
        return Failed(UNSUPPORTED)
    signals = [wait for wait in observation.waits if wait.kind == "KIND_SIGNAL"]
    if not signals:
        return Running()
    chosen = min(signals, key=lambda wait: (wait.sequence, wait.step_path, wait.signal))
    try:
        step = index.resolve(chosen.step_path, chosen.signal)
    except StepError:
        return Failed(UNSUPPORTED)
    return Awaiting(step.label, step.signal, chosen.sequence, chosen.step_path, step.arguments)


def spoken_line(item: Step) -> str | None:
    if isinstance(item, Running):
        return None
    if isinstance(item, Awaiting):
        return _finish(item.label)
    if isinstance(item, Succeeded):
        values = _result_strings(item.result)
        if not values:
            return None
        return " ".join(_clause(value) for value in values)
    return _finish(item.message)


def parse_fields(schema: str) -> tuple[Argument, ...]:
    text = schema.strip()
    if not text.startswith("object{") or not text.endswith("}"):
        raise StepError(UNSUPPORTED)
    body = text[len("object{") : -1].strip()
    if body == "":
        return ()
    if any(mark in body for mark in "?<>{}[]"):
        raise StepError(UNSUPPORTED)
    fields: list[Argument] = []
    for part in body.split(","):
        name, separator, type_name = part.partition(":")
        name = name.strip()
        type_name = type_name.strip()
        if separator != ":" or _FIELD.fullmatch(name) is None or type_name not in _TYPES:
            raise StepError(UNSUPPORTED)
        fields.append(Argument(name, _TYPES[type_name]))
    if len({field.name for field in fields}) != len(fields):
        raise StepError(UNSUPPORTED)
    return tuple(fields)


def _walk(steps: list[StepNode], frame: str, signals: dict[str, str], found: list[_Step]) -> None:
    for step in steps:
        step_id = step.id
        if not isinstance(step_id, str) or step_id == "":
            raise StepError("a step must have an id")
        if step.parallel is not None:
            _walk_parallel(step.parallel.branches, frame, step_id, signals, found)
            continue
        if step.for_each is not None:
            _walk(step.for_each.steps, _join(frame, f"{step_id}[]"), signals, found)
            continue
        if step.await_signal is None:
            continue
        signal = step.await_signal.signal
        if not isinstance(signal, str):
            raise StepError("an await must name a signal")
        if signal not in signals or not isinstance(signals[signal], str):
            raise StepError("await signal is not declared")
        label = str(step.label or "").strip()
        if label == "":
            raise StepError("await signal step has no label")
        found.append(_Step(_join(frame, step_id), label, signal, parse_fields(signals[signal])))


def _walk_parallel(branches: list[BranchNode], frame: str, step_id: str, signals: dict[str, str], found: list[_Step]) -> None:
    for branch in branches:
        branch_id = branch.id
        if not isinstance(branch_id, str) or branch_id == "":
            raise StepError("a branch must have an id")
        _walk(branch.steps, _join(frame, f"{step_id}.{branch_id}"), signals, found)


def _clause(value: str) -> str:
    text = value.strip()
    if text.endswith((".", "!", "?")):
        return text
    return f"{text}."


def _finish(text: str) -> str | None:
    body = text.strip()
    if body == "":
        return None
    if body.endswith((".", "!", "?")):
        return body
    return f"{body}."


def _result_strings(result: Any) -> list[str]:
    if isinstance(result, str):
        return [result] if result.strip() else []
    if isinstance(result, dict):
        return [value for value in result.values() if isinstance(value, str) and value.strip()]
    return []


def _join(parent: str, child: str) -> str:
    if parent == "":
        return child
    return f"{parent}/{child}"


def _same_path(template: str, runtime: str) -> bool:
    left, right = template.split("/"), runtime.split("/")
    if len(left) != len(right):
        return False
    for item, live in zip(left, right):
        if item == live:
            continue
        matched = _INDEX.fullmatch(live)
        if item.endswith("[]") and matched is not None and matched.group(1) == item[:-2]:
            continue
        return False
    return True
