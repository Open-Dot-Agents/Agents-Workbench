"""Paths and field contracts shared by MCP projection tests."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MCPProjectionFixtures:
    """Locations and accepted field names for checked-in adapter experiments."""

    canonical_path: Path
    copilot_path: Path
    codex_path: Path
    opencode_path: Path
    duplicate_keys_path: Path
    non_object_path: Path
    canonical_server_fields: frozenset[str]
    codex_server_fields: frozenset[str]
    opencode_server_fields: frozenset[str]


def load_fixtures(root: Path) -> MCPProjectionFixtures:
    fixture_directory = Path(__file__).parent
    return MCPProjectionFixtures(
        canonical_path=root / ".agents" / "tools" / "mcp.json",
        copilot_path=root / ".github" / "mcp.json",
        codex_path=root / ".codex" / "config.toml",
        opencode_path=root / ".opencode" / "opencode.json",
        duplicate_keys_path=fixture_directory / "invalid_duplicate_keys.json",
        non_object_path=fixture_directory / "invalid_non_object.json",
        canonical_server_fields=frozenset({"type", "command", "args", "env"}),
        codex_server_fields=frozenset(
            {
                "command",
                "args",
                "env",
                "startup_timeout_sec",
                "tool_timeout_sec",
                "default_tools_approval_mode",
            }
        ),
        opencode_server_fields=frozenset({"type", "command", "enabled", "environment"}),
    )
