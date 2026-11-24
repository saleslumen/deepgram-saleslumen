import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from catalog.errors import format_errors, frontmatter

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FROZEN = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True, populate_by_name=True)

class SkillError(Exception):
    pass

class Skill(BaseModel):
    model_config = _FROZEN
    name: str
    description: str
    body: str = ""
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = Field(default=None, validation_alias="allowed-tools")
    metadata: dict[str, str] | None = None
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
    @field_validator("compatibility")
    @classmethod
    def compatibility_text(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 500:
            raise ValueError("compatibility must be at most 500 characters")
        return value
    @field_validator("metadata")
    @classmethod
    def metadata_strings(cls, value: dict | None) -> dict[str, str] | None:
        if value is None:
            return None
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise ValueError("metadata must be a mapping of strings")
        return value
    @model_validator(mode="after")
    def directory_name(self, info) -> Self:
        directory = info.context.get("directory") if info.context else None
        if directory is not None and self.name != directory:
            raise ValueError(f"name {self.name} does not match the directory {directory}")
        return self

def load_skills(root: Path) -> tuple[Skill, ...]:
    if not root.is_dir():
        raise SkillError(f"{root}: : is not a skills directory")
    skills: list[Skill] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            raise SkillError(f"{child}: : has no SKILL.md")
        skills.append(_parse(skill_file))
    return tuple(skills)

def skill_prompt(skills: tuple[Skill, ...]) -> str:
    lines = ["When the caller asks the question a skill describes, call that skill by name and answer with it. Do not speak the description. Do not offer to read a skill."]
    if not skills:
        lines.append("No skills are installed.")
    else:
        lines.extend(f"{skill.name}: {' '.join(skill.description.split())}" for skill in skills)
    return "\n".join(lines)

def selected_skills(skills: tuple[Skill, ...], names: tuple[str, ...]) -> tuple[Skill, ...]:
    found = {skill.name: skill for skill in skills}
    missing = [name for name in names if name not in found]
    if missing:
        raise SkillError(f"agent: skills: no skill named {', '.join(missing)}")
    return tuple(found[name] for name in names)

def _parse(path: Path) -> Skill:
    where = str(path)
    try:
        header, body = frontmatter(path.read_text(encoding="utf-8"), where, SkillError)
        loaded = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        raise SkillError(f"{where}: : frontmatter is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise SkillError(f"{where}: : frontmatter must be a mapping")
    try:
        skill = Skill.model_validate(loaded, context={"directory": path.parent.name})
    except ValidationError as exc:
        raise SkillError("\n".join(format_errors(where, exc))) from exc
    return skill.model_copy(update={"body": body.strip()})