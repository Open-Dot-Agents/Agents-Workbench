#!/usr/bin/env python3
"""Validate the canonical MCP configuration and its checked-in projections."""

import json
import tomllib
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SERVER_FIELDS = {"type", "command", "args", "env"}
CODEX_SERVER_FIELDS = {
    "command",
    "args",
    "env",
    "startup_timeout_sec",
    "tool_timeout_sec",
    "default_tools_approval_mode",
}
OPENCODE_SERVER_FIELDS = {"type", "command", "enabled", "environment"}


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file, object_pairs_hook=reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        value = tomllib.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a TOML table")
    return value


class MCPProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical_document = load_json(ROOT / ".agents" / "tools" / "mcp.json")
        cls.canonical = cls.canonical_document["mcpServers"]
        cls.copilot_document = load_json(ROOT / ".github" / "mcp.json")
        cls.codex_document = load_toml(ROOT / ".codex" / "config.toml")
        cls.opencode_document = load_json(ROOT / ".opencode" / "opencode.json")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            json.loads('{"mcpServers": {}, "mcpServers": {}}', object_pairs_hook=reject_duplicate_keys)

    def test_canonical_schema(self) -> None:
        self.assertEqual(set(self.canonical_document), {"mcpServers"})
        self.assertIsInstance(self.canonical, dict)
        self.assertTrue(self.canonical, "canonical configuration must define at least one MCP server")

        for name, server in self.canonical.items():
            with self.subTest(server=name):
                self.assertIsInstance(name, str)
                self.assertTrue(name, "server name cannot be empty")
                self.assertIsInstance(server, dict)
                self.assertEqual(
                    set(server) - CANONICAL_SERVER_FIELDS,
                    set(),
                    "canonical server contains unsupported fields",
                )
                self.assertEqual(server.get("type"), "stdio")
                self.assertIsInstance(server.get("command"), str)
                self.assertTrue(server["command"], "stdio server command cannot be empty")
                self.assertIsInstance(server.get("args", []), list)
                self.assertTrue(all(isinstance(argument, str) for argument in server.get("args", [])))
                self.assertIsInstance(server.get("env", {}), dict)
                self.assertTrue(
                    all(
                        isinstance(key, str) and isinstance(value, str)
                        for key, value in server.get("env", {}).items()
                    )
                )

    def test_copilot_projection_matches_canonical(self) -> None:
        self.assertEqual(set(self.copilot_document), {"mcpServers"})
        copilot = self.copilot_document["mcpServers"]
        self.assertEqual(set(copilot), set(self.canonical))
        for name in self.canonical:
            with self.subTest(server=name):
                self.assertEqual(copilot[name], self.canonical[name])

    def test_codex_projection_matches_canonical(self) -> None:
        self.assertEqual(set(self.codex_document), {"mcp_servers"})
        codex = self.codex_document["mcp_servers"]
        self.assertEqual(set(codex), set(self.canonical))
        for name, canonical_server in self.canonical.items():
            with self.subTest(server=name):
                adapter = codex[name]
                self.assertIsInstance(adapter, dict)
                self.assertEqual(
                    set(adapter) - CODEX_SERVER_FIELDS,
                    set(),
                    "Codex server contains unsupported fields",
                )
                self.assertEqual(adapter["command"], canonical_server["command"])
                self.assertEqual(adapter.get("args", []), canonical_server.get("args", []))
                self.assertEqual(adapter.get("env", {}), canonical_server.get("env", {}))
                for timeout in ("startup_timeout_sec", "tool_timeout_sec"):
                    if timeout in adapter:
                        self.assertIsInstance(adapter[timeout], int)
                        self.assertGreater(adapter[timeout], 0)
                if "default_tools_approval_mode" in adapter:
                    self.assertIsInstance(adapter["default_tools_approval_mode"], str)
                    self.assertTrue(adapter["default_tools_approval_mode"])

    def test_opencode_projection_matches_canonical(self) -> None:
        self.assertEqual(set(self.opencode_document), {"$schema", "mcp"})
        self.assertEqual(self.opencode_document["$schema"], "https://opencode.ai/config.json")
        opencode = self.opencode_document["mcp"]
        self.assertEqual(set(opencode), set(self.canonical))
        for name, canonical_server in self.canonical.items():
            with self.subTest(server=name):
                adapter = opencode[name]
                self.assertIsInstance(adapter, dict)
                self.assertEqual(
                    set(adapter) - OPENCODE_SERVER_FIELDS,
                    set(),
                    "OpenCode server contains unsupported fields",
                )
                self.assertEqual(adapter["type"], "local")
                self.assertIs(adapter["enabled"], True)
                self.assertEqual(
                    adapter["command"],
                    [canonical_server["command"], *canonical_server.get("args", [])],
                )
                self.assertEqual(adapter.get("environment", {}), canonical_server.get("env", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
