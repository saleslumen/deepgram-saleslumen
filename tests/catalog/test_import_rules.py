"""Package import boundaries."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CALL_ALLOWED = frozenset({"call", "catalog", "agents", "execution", "pipecat", "loguru"})
CALL_FORBIDDEN = frozenset({"voice", "httpx", "live"})
EXECUTION_ALLOWED = frozenset({"execution", "httpx", "loguru", "pydantic"})
EXECUTION_FORBIDDEN = frozenset({"call", "voice", "catalog", "agents", "pipecat", "live"})
CATALOG_ALLOWED = frozenset({"catalog", "yaml", "importlib", "pydantic"})
CATALOG_FORBIDDEN = frozenset({"live"})
LIVE_ALLOWED = frozenset({"live", "catalog", "execution", "pydantic", "fastapi", "starlette"})
LIVE_FORBIDDEN = frozenset({"voice", "call", "agents"})
VOICE_EXTRA = frozenset({"call", "config", "pipecat", "live", "execution"} | CATALOG_ALLOWED)
CALL_STDLIB = frozenset(
    {
        "typing",
        "dataclasses",
        "enum",
        "re",
        "collections",
        "abc",
        "json",
        "pathlib",
        "asyncio",
        "inspect",
        "types",
        "functools",
        "itertools",
        "uuid",
        "time",
        "math",
        "copy",
        "contextlib",
        "warnings",
        "weakref",
        "datetime",
        "logging",
        "traceback",
        "sys",
        "os",
        "builtins",
    }
)
EXECUTION_STDLIB = CALL_STDLIB | frozenset(
    {"hashlib", "random", "urllib", "io", "struct", "string", "textwrap", "hmac", "secrets"}
)
CATALOG_STDLIB = frozenset(
    {"typing", "dataclasses", "pathlib", "collections", "abc", "re", "urllib", "os", "inspect", "importlib", "functools", "json"}
)
LIVE_STDLIB = frozenset(
    {"typing", "pathlib", "collections", "abc", "asyncio", "re"}
)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _top_imports(path: Path) -> set[str]:
    return {name.split(".")[0] for name in _module_imports(path)}


def _stdlib_tops(allowed_roots: frozenset[str]) -> frozenset[str]:
    return allowed_roots


def _violations(
    package: str,
    allowed: frozenset[str],
    forbidden: frozenset[str],
    stdlib: frozenset[str],
) -> list[str]:
    base = ROOT / package
    violations: list[str] = []
    for path in sorted(base.rglob("*.py")):
        for module in _module_imports(path):
            top = module.split(".")[0]
            if top in forbidden:
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
            if top in stdlib or top in {"__future__"}:
                continue
            if top in allowed or module in allowed:
                continue
            if package == "call" and (module.startswith("pipecat.") or module.startswith("agents.")):
                continue
            if top not in allowed:
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    return violations


class ImportRulesTest(unittest.TestCase):
    def test_call_package_boundaries(self):
        self.assertEqual(_violations("call", CALL_ALLOWED, CALL_FORBIDDEN, CALL_STDLIB), [])

    def test_execution_package_boundaries(self):
        self.assertEqual(_violations("execution", EXECUTION_ALLOWED, EXECUTION_FORBIDDEN, EXECUTION_STDLIB), [])

    def test_catalog_package_boundaries(self):
        self.assertEqual(_violations("catalog", CATALOG_ALLOWED, CATALOG_FORBIDDEN, CATALOG_STDLIB), [])

    def test_voice_package_boundaries(self):
        allowed = frozenset(VOICE_EXTRA) | CATALOG_ALLOWED | {"loguru", "httpx", "voice", "mcp", "openai"}
        stdlib = CALL_STDLIB | frozenset({"yaml", "importlib", "urllib"})
        violations = _violations("voice", allowed, frozenset(), stdlib)
        self.assertEqual(violations, [])

    def test_live_package_boundaries(self):
        self.assertEqual(_violations("live", LIVE_ALLOWED, LIVE_FORBIDDEN, LIVE_STDLIB), [])

    def test_only_bot_imports_voice_at_root(self):
        for path in ROOT.glob("*.py"):
            tops = _top_imports(path)
            if path.name == "bot.py":
                self.assertIn("voice", tops)
            else:
                self.assertNotIn("voice", tops)

    def test_root_has_only_bot_and_config_modules(self):
        names = sorted(path.name for path in ROOT.glob("*.py"))
        self.assertEqual(names, ["bot.py", "config.py"])

    def test_execution_steps_observation_no_asyncio_httpx(self):
        forbidden = {"asyncio", "httpx"}
        for name in ("steps.py", "models.py"):
            path = ROOT / "execution" / name
            tops = _top_imports(path)
            hit = sorted(tops & forbidden)
            self.assertEqual(hit, [], f"{name} must not import {hit}")


if __name__ == "__main__":
    unittest.main()
