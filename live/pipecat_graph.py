import re

from pydantic import BaseModel, ConfigDict, Field

from catalog.agents import END, Agent
from catalog.load import Catalog

_FROZEN = ConfigDict(frozen=True, extra="forbid")
_STEP_NOTICE = re.compile(r"^(?:step|notice)-\d+\Z")
WORKFLOW_STEP_ID = "workflow-step"
GLOBAL_NODE_ID = "global"


class GraphNode(BaseModel):
    model_config = _FROZEN
    id: str
    kind: str
    label: str
    caption: str
    icon: str


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    source: str = Field(alias="from", serialization_alias="from")
    to: str
    label: str
    role: str


class Graph(BaseModel):
    model_config = _FROZEN
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def flow_highlight_id(node_name: str) -> str:
    if _STEP_NOTICE.fullmatch(node_name) is not None:
        return WORKFLOW_STEP_ID
    return node_name


def _agent_caption(agent: Agent) -> str:
    parts: list[str] = []
    if agent.tools:
        parts.append(", ".join(tool.__name__ for tool in agent.tools))
    if agent.skills:
        parts.append(", ".join(skill.name for skill in agent.skills))
    if agent.grants:
        parts.append(", ".join(grant.name for grant in agent.grants))
    return ", ".join(parts)


def _included_agents(catalog: Catalog) -> tuple[str, ...]:
    start = catalog.pipeline.start
    targets: set[str] = set()
    for table in catalog.pipeline.routes.values():
        for target in table.values():
            if target != END:
                targets.add(target)
    ordered = [start]
    for name in sorted(targets):
        if name != start:
            ordered.append(name)
    return tuple(ordered)


def pipecat_graph(catalog: Catalog, global_names: tuple[str, ...]) -> Graph:
    agents = _included_agents(catalog)
    nodes: list[GraphNode] = []
    for name in agents:
        agent = catalog.agents[name]
        nodes.append(GraphNode(
            id=name,
            kind="agent",
            label=name,
            caption=_agent_caption(agent),
            icon="auto_awesome",
        ))
    has_grants = any(catalog.agents[name].grants for name in agents)
    if has_grants:
        nodes.append(GraphNode(
            id=WORKFLOW_STEP_ID,
            kind="workflow_step",
            label="Workflow step",
            caption="",
            icon="flag",
        ))
    nodes.append(GraphNode(id="end", kind="end", label="End", caption="", icon=""))
    nodes.append(GraphNode(
        id=GLOBAL_NODE_ID,
        kind="global",
        label="Global",
        caption=", ".join(global_names),
        icon="",
    ))
    edges: list[GraphEdge] = []
    for name in agents:
        routes = catalog.pipeline.routes.get(name, {})
        for outcome, target in routes.items():
            edges.append(GraphEdge(source=name, to=target, label=outcome, role="next"))
    if has_grants:
        for name in agents:
            if catalog.agents[name].grants:
                edges.append(GraphEdge(source=name, to=WORKFLOW_STEP_ID, label="start_workflow / answer_step", role="next"))
    return Graph(nodes=tuple(nodes), edges=tuple(edges))