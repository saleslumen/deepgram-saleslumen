import json
import os
import re
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from catalog.errors import format_errors, frontmatter

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_WORKFLOW_ID = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
DEFAULT_ID = {"SALESLUMEN_WORKFLOW_ID": "hello-voice"}
_FROZEN = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True, populate_by_name=True)


class ManifestError(Exception):
    pass


class Grant(BaseModel):
    model_config = _FROZEN
    name: str
    description: str
    id_env: str = Field(default="", validation_alias="workflow-id-env")
    workflow_id: str = ""
    @field_validator("name")
    @classmethod
    def name_pattern(cls, value: str) -> str:
        if NAME.fullmatch(value) is None or len(value) > 64:
            raise ValueError("name must be 1 to 64 lowercase letters, numbers, and single hyphens")
        return value
    @field_validator("description")
    @classmethod
    def description_length(cls, value: str) -> str:
        if value == "" or len(value) > 1024:
            raise ValueError("description must be 1 to 1024 characters")
        return value
    @field_validator("id_env")
    @classmethod
    def env_key_pattern(cls, value: str) -> str:
        if value and ENV_KEY.fullmatch(value) is None:
            raise ValueError("workflow-id-env must be an environment name")
        return value
    @field_validator("workflow_id")
    @classmethod
    def workflow_id_pattern(cls, value: str) -> str:
        if value and _WORKFLOW_ID.fullmatch(value) is None:
            raise ValueError("workflow id is invalid")
        return value
    @model_validator(mode="after")
    def directory_name(self, info) -> Self:
        directory = info.context.get("directory") if info.context else None
        if directory is not None and self.name != directory:
            raise ValueError("name does not match the directory")
        return self


def load_manifests(root: Path) -> tuple[Grant, ...]:
    if not root.is_dir():
        return ()
    found: list[Grant] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        path = child / "WORKFLOW.md"
        if not path.is_file():
            raise ManifestError(f"{child.name}: : has no WORKFLOW.md")
        found.append(_parse(path))
    names = [item.name for item in found]
    if len(names) != len(set(names)):
        raise ManifestError("workflows: : workflow names must be unique")
    return tuple(found)


def granted(manifests: tuple[Grant, ...], names: tuple[str, ...]) -> tuple[Grant, ...]:
    found = {item.name: item for item in manifests}
    missing = [name for name in names if name not in found]
    if missing:
        raise ManifestError(f"agent: workflows: no workflow named {', '.join(missing)}")
    return tuple(_resolve(found[name]) for name in names)


def _parse(path: Path) -> Grant:
    where = path.parent.name
    try:
        header, _body = frontmatter(path.read_text(encoding="utf-8"), where, ManifestError)
        loaded = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{where}: : frontmatter is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise ManifestError(f"{where}: : frontmatter must be a mapping")
    try:
        return Grant.model_validate(loaded, context={"directory": where})
    except ValidationError as exc:
        raise ManifestError("\n".join(format_errors(where, exc))) from exc


def revision_steps(root: Path, name: str) -> list:
    path = root / "workflows" / name / "revision.json"
    where = f"workflows/{name}/revision.json"
    if not path.is_file():
        raise ManifestError(f"{where}: file is missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{where}: invalid JSON") from exc
    if not isinstance(document, dict):
        raise ManifestError(f"{where}: must be an object")
    definition = document.get("definition")
    if not isinstance(definition, dict):
        raise ManifestError(f"{where}: definition must be an object")
    steps = definition.get("steps")
    if not isinstance(steps, list):
        raise ManifestError(f"{where}: definition.steps must be a list")
    return steps


def _resolve(item: Grant) -> Grant:
    raw = os.environ.get(item.id_env, "")
    value = raw.strip() or DEFAULT_ID.get(item.id_env, "")
    if _WORKFLOW_ID.fullmatch(value) is None:
        raise ManifestError(f"{item.id_env}: workflow_id: workflow id is invalid")
    return item.model_copy(update={"workflow_id": value})