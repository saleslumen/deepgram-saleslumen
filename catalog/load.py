from dataclasses import dataclass
from pathlib import Path

from catalog.agents import Agent, Pipeline, load_call, read_shared
from catalog.skills import Skill, load_skills

@dataclass(frozen=True)
class Catalog:
    agents: dict[str, Agent]
    pipeline: Pipeline
    skills: tuple[Skill, ...]
    shared: str

def load(root: Path) -> Catalog:
    skills = load_skills(root / "skills")
    shared = read_shared(root)
    agents, pipeline = load_call(root, skills)
    return Catalog(agents=agents, pipeline=pipeline, skills=skills, shared=shared)