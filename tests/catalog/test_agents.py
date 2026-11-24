"""Agent discovery, tools, and pipeline routes."""

import inspect
import sys
import tempfile
import unittest
from pathlib import Path

from catalog.agents import END, AgentError, load_call
from catalog.load import load
from catalog.skills import Skill

ROOT = Path(__file__).resolve().parents[2]
SKILLS = (Skill(name="saleslumen", description="Explain Saleslumen.", body="Saleslumen."),)
INSTALLED_GREETING = "Hi, thanks for calling Saleslumen. What is the phone number on your account?"
_PIPELINE_LINES = """\
lines:
  greeting: Fixture greeting.
  check_in: Are you still there?
  goodbye: Thanks. Goodbye.
"""


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _agent(root: Path, name: str, spec: str, prompt: str = "Ask.\n") -> None:
    _write(root, "agents/__init__.py", "\n")
    _write(root, f"agents/{name}/__init__.py", "\n")
    _write(root, f"agents/{name}/agent.yaml", spec)
    _write(root, f"agents/{name}/PROMPT.md", prompt)


def _base(
    root: Path,
    verification: str = "tools:\n  - find_account\n",
    workflow: str | None = None,
    pipeline: str | None = None,
) -> None:
    if pipeline is None:
        pipeline = f"start: lone\nroutes: {{}}\n{_PIPELINE_LINES}"
    _write(root, "agents/PROMPT.md", "Shared voice role.\n")
    _agent(root, "lone", verification, "Agent prompt.\n")
    if workflow is not None:
        _agent(root, "workflow", workflow, "Workflow prompt.\n")
    _write(root, "agents/pipeline.yaml", pipeline)


def _purge_agents_modules() -> None:
    for key in list(sys.modules):
        if key == "agents" or key.startswith("agents."):
            del sys.modules[key]


def _load_call(root: Path, skills: tuple[Skill, ...]):
    entry = str(root.resolve())
    inserted = False
    if entry not in sys.path:
        sys.path.insert(0, entry)
        inserted = True
    if inserted:
        _purge_agents_modules()
    try:
        return load_call(root, skills)
    finally:
        if inserted and sys.path and sys.path[0] == entry:
            sys.path.pop(0)
            _purge_agents_modules()


class AgentCatalogTest(unittest.TestCase):
    def test_installed_catalog_loads_verification_tools_and_routes(self):
        catalog = load(ROOT)
        verification = catalog.agents["verification"]
        workflow = catalog.agents["workflow"]
        self.assertIn("A workflow result is only what a workflow line said.", catalog.shared)
        self.assertIn("Agreeing to a question is not the result.", catalog.shared)
        self.assertNotIn("A workflow result is only what a workflow line said.", verification.prompt)
        self.assertNotIn("Agreeing to a question is not the result.", workflow.prompt)
        self.assertIn("find_account", verification.prompt)
        self.assertEqual(len(verification.tools), 2)
        self.assertEqual(verification.tools[0].__name__, "find_account")
        self.assertEqual(verification.tools[1].__name__, "check_passcode")
        self.assertTrue(all(inspect.iscoroutinefunction(tool) for tool in verification.tools))
        self.assertEqual(workflow.grants[0].name, "refund")
        self.assertEqual(verification.skills[0].name, "saleslumen")
        self.assertEqual(workflow.skills[0].name, "saleslumen")
        self.assertEqual(catalog.pipeline.start, "verification")
        routes = catalog.pipeline.routes["verification"]
        self.assertEqual(routes["verified"], "workflow")
        self.assertEqual(routes["refused"], END)
        self.assertEqual(catalog.pipeline.lines.greeting, INSTALLED_GREETING)

    def test_each_agent_carries_its_grants(self):
        catalog = load(ROOT)
        self.assertEqual(catalog.agents["verification"].grants, ())
        self.assertEqual([grant.name for grant in catalog.agents["workflow"].grants], ["refund"])

    def test_verification_tools_module_is_agents_verification_tools(self):
        catalog = load(ROOT)
        find_account = catalog.agents["verification"].tools[0]
        self.assertEqual(find_account.__module__, "agents.verification.tools")

    def test_prompts_do_not_name_retired_tools(self):
        names = ("agents/PROMPT.md", "agents/verification/PROMPT.md", "agents/workflow/PROMPT.md")
        banned = ("check_workflow", "continue_workflow", "I speak")
        for relative in names:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for word in banned:
                self.assertNotIn(word, text, f"{relative} must not mention {word}")

    def test_discovery_order_follows_sorted_directory_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "agents/PROMPT.md", "Shared.\n")
            _agent(root, "bravo", "{}")
            _agent(root, "alpha", "{}")
            _write(root, "agents/pipeline.yaml", f"start: alpha\nroutes: {{}}\n{_PIPELINE_LINES}")
            agents, _pipeline = _load_call(root, SKILLS)
        self.assertEqual(tuple(agents.keys()), ("alpha", "bravo"))

    def test_missing_agent_yaml_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "agents/PROMPT.md", "Shared.\n")
            _write(root, "agents/lone/PROMPT.md", "Ask.\n")
            _write(root, "agents/pipeline.yaml", f"start: lone\nroutes: {{}}\n{_PIPELINE_LINES}")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("agent.yaml", str(caught.exception))

    def test_missing_prompt_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "agents/PROMPT.md", "Shared.\n")
            _write(root, "agents/lone/agent.yaml", "{}")
            _write(root, "agents/pipeline.yaml", f"start: lone\nroutes: {{}}\n{_PIPELINE_LINES}")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("PROMPT.md", str(caught.exception))

    def test_defaults_when_every_field_is_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "{}")
            agents, _pipeline = _load_call(root, SKILLS)
        agent = agents["lone"]
        self.assertEqual(agent.tools, ())
        self.assertEqual(agent.grants, ())
        self.assertEqual(agent.connectors, ())
        self.assertEqual(agent.skills, ())

    def test_unknown_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "voice: nope\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("agent.yaml: voice:", str(caught.exception))

    def test_unknown_tool_name_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "tools:\n  - missing\n")
            _write(root, "agents/lone/tools.py", "async def other(flow_manager):\n    return {}\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("missing", str(caught.exception))

    def test_sync_tool_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "tools:\n  - slow\n")
            _write(root, "agents/lone/tools.py", "def slow(flow_manager):\n    return {}\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("must be async", str(caught.exception))

    def test_missing_tools_module_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "tools:\n  - find_account\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("agents.lone.tools is missing", str(caught.exception))

    def test_unknown_route_target_fails(self):
        pipeline = f"start: lone\nroutes:\n  lone:\n    done: nobody\n{_PIPELINE_LINES}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, pipeline=pipeline)
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("must name an agent or end", str(caught.exception))

    def test_unknown_skill_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "skills:\n  - missing\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("no skill named missing", str(caught.exception))

    def test_unknown_workflow_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "workflows/refund/WORKFLOW.md", "---\nname: refund\ndescription: A charge.\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\n---\n")
            _base(root, "workflows:\n  - missing\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("no workflow named missing", str(caught.exception))

    def test_missing_start_agent_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, pipeline=f"start: missing\nroutes: {{}}\n{_PIPELINE_LINES}")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("start must name an agent", str(caught.exception))

    def test_underscore_and_hidden_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "agents/PROMPT.md", "Shared.\n")
            _agent(root, "lone", "{}")
            _write(root, "agents/_skip/note.txt", "skip\n")
            _write(root, "agents/.hidden/note.txt", "hidden\n")
            _write(root, "agents/pipeline.yaml", f"start: lone\nroutes: {{}}\n{_PIPELINE_LINES}")
            agents, _pipeline = _load_call(root, SKILLS)
        self.assertEqual(tuple(agents.keys()), ("lone",))

    def test_pipeline_lines_are_required(self):
        base = f"start: lone\nroutes: {{}}\n"
        cases = (
            (f"{base}lines:\n  greeting: Hi.\n  goodbye: Bye.\n", "pipeline.yaml: lines.check_in:"),
            (f"{base}lines:\n  greeting: Hi.\n  check_in: There?\n  goodbye: Bye.\n  extra: nope\n", "pipeline.yaml: lines.extra:"),
            (f"{base}lines:\n  greeting: '   '\n  check_in: There?\n  goodbye: Bye.\n", "pipeline.yaml: lines.greeting:"),
        )
        for yaml_text, needle in cases:
            with self.subTest(needle=needle):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _write(root, "agents/PROMPT.md", "Shared.\n")
                    _agent(root, "lone", "{}")
                    _write(root, "agents/pipeline.yaml", yaml_text)
                    with self.assertRaises(AgentError) as caught:
                        _load_call(root, SKILLS)
                self.assertIn(needle, str(caught.exception))

    def test_verified_and_refused_routes_on_installed_pipeline(self):
        catalog = load(ROOT)
        routes = catalog.pipeline.routes["verification"]
        self.assertEqual(routes["verified"], "workflow")
        self.assertEqual(routes["refused"], END)

    def test_an_empty_shared_prompt_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "agents/PROMPT.md", "   \n")
            _agent(root, "lone", "{}")
            _write(root, "skills/saleslumen/SKILL.md", "---\nname: saleslumen\ndescription: Explain.\n---\nBody.\n")
            _write(root, "agents/pipeline.yaml", f"start: lone\nroutes: {{}}\n{_PIPELINE_LINES}")
            with self.assertRaises(AgentError) as caught:
                load(root)
        self.assertIn("empty", str(caught.exception))

    def test_connector_url_must_be_http_or_https(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "connectors:\n  - name: mcp\n    url: ftp://files.local\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("agent.yaml: connectors.", str(caught.exception))

    def test_connector_names_must_be_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = "connectors:\n  - name: mcp\n    url: https://a.example\n  - name: mcp\n    url: https://b.example\n"
            _base(root, spec)
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("agent.yaml:", str(caught.exception))
        self.assertIn("connectors repeats", str(caught.exception))

    def test_tool_list_rejects_empty_and_repeated_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, 'tools:\n  - ""\n  - find_account\n  - find_account\n')
            _write(root, "agents/lone/tools.py", "async def find_account(flow_manager):\n    return {}\n")
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("agent.yaml: tools:", str(caught.exception))

    def test_route_entry_types_must_be_strings(self):
        pipeline = f"start: lone\nroutes:\n  lone:\n    done: 1\n{_PIPELINE_LINES}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, pipeline=pipeline)
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        self.assertIn("invalid route entry", str(caught.exception))

    def test_collects_multiple_problems(self):
        pipeline = f"start: missing\nroutes:\n  lone:\n    go: nowhere\n{_PIPELINE_LINES}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base(root, "tools:\n  - absent\n", pipeline=pipeline)
            with self.assertRaises(AgentError) as caught:
                _load_call(root, SKILLS)
        message = str(caught.exception)
        self.assertIn("start must name an agent", message)
        self.assertIn("must name an agent or end", message)


if __name__ == "__main__":
    unittest.main()
