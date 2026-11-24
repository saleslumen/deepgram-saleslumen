import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import AfterValidator, AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from catalog.errors import format_errors
from catalog.skills import Skill, selected_skills
from catalog.workflows import Grant, ManifestError, granted, load_manifests

END = "end"
_FROZEN = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
_http = TypeAdapter(AnyHttpUrl)

def _unique_names(value: list[str]) -> list[str]:
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("empty or repeated entry")
        stripped = item.strip()
        if stripped == "" or stripped in items:
            raise ValueError("empty or repeated entry")
        items.append(stripped)
    return items

UniqueNames = Annotated[list[str], AfterValidator(_unique_names)]

class AgentError(Exception):
    pass

class Connector(BaseModel):
    model_config = _FROZEN
    name: str = Field(min_length=1)
    url: str = ""
    @field_validator("url", mode="before")
    @classmethod
    def http_url(cls, value: Any) -> str:
        if not isinstance(value, str) or value.strip() == "":
            raise ValueError("url must be text")
        try:
            return str(_http.validate_python(value.strip()))
        except ValidationError:
            raise ValueError("connector url must be an http(s) URL") from None

class Lines(BaseModel):
    model_config = _FROZEN
    greeting: str = Field(min_length=1)
    check_in: str = Field(min_length=1)
    goodbye: str = Field(min_length=1)

class Pipeline(BaseModel):
    model_config = _FROZEN
    start: str = ""
    routes: dict[str, dict[str, str]] = Field(default_factory=dict)
    lines: Lines
    @field_validator("routes")
    @classmethod
    def route_entries(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for _agent, table in value.items():
            if not isinstance(table, dict):
                raise ValueError("route entry must name an agent and a mapping")
            for outcome, target in table.items():
                if not isinstance(outcome, str) or not isinstance(target, str):
                    raise ValueError("invalid route entry")
        return value

class _AgentSpec(BaseModel):
    model_config = _FROZEN
    tools: UniqueNames = Field(default_factory=list)
    skills: UniqueNames = Field(default_factory=list)
    connectors: list[Connector] = Field(default_factory=list)
    workflows: UniqueNames = Field(default_factory=list)
    @field_validator("tools", "skills", "workflows", "connectors", mode="before")
    @classmethod
    def default_empty(cls, value: Any) -> Any:
        return [] if value is None else value
    @model_validator(mode="after")
    def unique_connectors(self, info) -> Self:
        seen: set[str] = set()
        for entry in self.connectors:
            if entry.name in seen:
                agent = info.context.get("agent", "") if info.context else ""
                raise ValueError(f"{agent} connectors repeats {entry.name}")
            seen.add(entry.name)
        return self

@dataclass(frozen=True)
class Agent:
    name: str
    prompt: str
    tools: tuple[Callable, ...] = ()
    grants: tuple[Grant, ...] = ()
    skills: tuple[Skill, ...] = ()
    connectors: tuple[Connector, ...] = ()

def read_shared(root: Path) -> str:
    return _read(root / "agents" / "PROMPT.md")

def load_call(root: Path, skills: tuple[Skill, ...]) -> tuple[dict[str, Agent], Pipeline]:
    errors: list[str] = []
    agents_dir = root / "agents"
    if not agents_dir.is_dir():
        raise AgentError(f"{agents_dir} is missing")
    manifests: tuple[Grant, ...] = ()
    try:
        manifests = load_manifests(root / "workflows")
    except ManifestError as exc:
        errors.append(str(exc))
    found: dict[str, Agent] = {}
    for child in sorted(agents_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.name.startswith("_"):
            continue
        name = child.name
        yaml_path = child / "agent.yaml"
        prompt_path = child / "PROMPT.md"
        if not yaml_path.is_file():
            errors.append(f"{yaml_path} is missing")
            continue
        if not prompt_path.is_file():
            errors.append(f"{prompt_path} is missing")
            continue
        try:
            raw = _mapping(yaml_path)
        except AgentError as exc:
            errors.append(str(exc))
            continue
        spec: _AgentSpec | None = None
        try:
            spec = _AgentSpec.model_validate(raw, context={"agent": name})
        except ValidationError as exc:
            errors.extend(format_errors(str(yaml_path), exc))
        try:
            body = _read(prompt_path)
        except AgentError as exc:
            errors.append(str(exc))
            continue
        tool_names = tuple(spec.tools) if spec is not None else ()
        workflow_names = tuple(spec.workflows) if spec is not None else ()
        skill_names = tuple(spec.skills) if spec is not None else ()
        connectors = tuple(spec.connectors) if spec is not None else ()
        tools = _resolve_tools(name, tool_names, errors)
        resolved_skills: tuple[Skill, ...] = ()
        if skill_names:
            try:
                resolved_skills = selected_skills(skills, skill_names)
            except Exception as exc:
                errors.append(str(exc))
        resolved_grants: tuple[Grant, ...] = ()
        if workflow_names:
            try:
                resolved_grants = granted(manifests, workflow_names)
            except ManifestError as exc:
                errors.append(str(exc))
        found[name] = Agent(
            name=name,
            prompt=body,
            tools=tools,
            grants=resolved_grants,
            connectors=connectors,
            skills=resolved_skills,
        )
    if not found and not errors:
        errors.append(f"{agents_dir} has no agents")
    pipeline = _pipeline(root / "agents" / "pipeline.yaml", found, errors)
    if errors:
        raise AgentError("\n".join(errors))
    return found, pipeline

def _resolve_tools(agent: str, names: tuple[str, ...], errors: list[str]) -> tuple[Callable, ...]:
    if not names:
        return ()
    module_name = f"agents.{agent}.tools"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        errors.append(f"{module_name} is missing")
        return ()
    except Exception as exc:
        errors.append(f"{module_name} cannot be loaded: {exc}")
        return ()
    resolved: list[Callable] = []
    for name in names:
        function = getattr(module, name, None)
        if function is None:
            errors.append(f"{module_name} has no tool {name}")
            continue
        if not inspect.iscoroutinefunction(function):
            errors.append(f"{name} must be async")
            continue
        resolved.append(function)
    return tuple(resolved)

def _pipeline(path: Path, agents: dict[str, Agent], errors: list[str]) -> Pipeline:
    blank = Pipeline(start="", routes={}, lines=Lines(greeting=".", check_in=".", goodbye="."))
    if not path.is_file():
        errors.append(f"{path} is missing")
        return blank
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{path} is invalid YAML: {exc}")
        return blank
    if not isinstance(loaded, dict):
        errors.append(f"{path} must be a mapping")
        return blank
    spec: Pipeline | None = None
    try:
        spec = Pipeline.model_validate(loaded)
    except ValidationError as exc:
        errors.extend(format_errors(str(path), exc))
    start = spec.start if spec is not None else (loaded.get("start") if isinstance(loaded.get("start"), str) else "")
    if not isinstance(loaded.get("start"), str) or start not in agents:
        errors.append(f"{path} start must name an agent")
    parsed: dict[str, dict[str, str]] = {}
    routes = spec.routes if spec is not None else (loaded.get("routes") if isinstance(loaded.get("routes"), dict) else None)
    if isinstance(routes, dict):
        for agent, table in routes.items():
            if not isinstance(agent, str) or agent not in agents or not isinstance(table, dict):
                errors.append(f"{path} route {agent} must name an agent and a mapping")
                continue
            parsed[agent] = {}
            for outcome, target in table.items():
                if not isinstance(outcome, str) or not isinstance(target, str):
                    errors.append(f"{path} {agent} has an invalid route entry")
                    continue
                if target != END and target not in agents:
                    errors.append(f"{path} {agent} {outcome} must name an agent or end")
                parsed[agent][outcome] = target
    elif loaded.get("routes") is not None:
        errors.append(f"{path} routes must be a mapping")
    lines = spec.lines if spec is not None else blank.lines
    return Pipeline(start=start, routes=parsed, lines=lines)

def _mapping(path: Path) -> dict:
    if not path.is_file():
        raise AgentError(f"{path} is missing")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AgentError(f"{path} is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise AgentError(f"{path} must be a mapping")
    return loaded

def _read(path: Path) -> str:
    if not path.is_file():
        raise AgentError(f"{path} is missing")
    text = path.read_text(encoding="utf-8").strip()
    if text == "":
        raise AgentError(f"{path} is empty")
    return text