#!/usr/bin/env python3
"""Apply the configured policy to Claude Code and Codex PermissionRequest events."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

SANDBOX_KEYWORD = re.compile(r"沙盒|sandbox", re.IGNORECASE)
LEVELS = {"ask", "retry_without_sandbox", "all"}
DEFAULT_LEVEL = "all"
# These tools use the permission prompt to collect the user's answer or plan
# approval; allowing them from a hook would skip the user entirely.
DEFAULT_SKIP_TOOLS = ["AskUserQuestion", "ExitPlanMode"]


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _plugin_dir(kind: str) -> Path | None:
    value = _env(f"CLAUDE_PLUGIN_{kind}", f"PLUGIN_{kind}")
    return Path(value) if value else None


def _config_path() -> Path | None:
    explicit = _env("ALWAYS_APPROVE_CONFIG")
    if explicit:
        return Path(explicit)
    for directory in (_plugin_dir("DATA"), _plugin_dir("ROOT")):
        if directory and (directory / "config.json").is_file():
            return directory / "config.json"
    return None


def _parse_level(value: object) -> str:
    if value not in LEVELS:
        raise ValueError(f"invalid level: {value!r}")
    return value


def _load_config() -> dict:
    path = _config_path()
    if path is None:
        return {"default_level": DEFAULT_LEVEL, "projects": {}, "skip_tools": DEFAULT_SKIP_TOOLS}
    with path.open(encoding="utf-8") as file:
        config = json.load(file)
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    default = _parse_level(config.get("default_level", DEFAULT_LEVEL))
    projects = config.get("projects", {})
    skip_tools = config.get("skip_tools", DEFAULT_SKIP_TOOLS)
    if not isinstance(projects, dict):
        raise ValueError("projects must be an object")
    for project in projects:
        if not Path(project).is_absolute():
            raise ValueError("project rules need absolute paths")
    projects = {project: _parse_level(level) for project, level in projects.items()}
    if not isinstance(skip_tools, list) or not all(isinstance(tool, str) for tool in skip_tools):
        raise ValueError("skip_tools must be a list of tool names")
    return {"default_level": default, "projects": projects, "skip_tools": skip_tools}


def _level(config: dict, cwd: object) -> str:
    override = _env("ALWAYS_APPROVE_LEVEL")
    if override is not None:
        return _parse_level(override)
    level = config["default_level"]
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return level
    current = Path(cwd).resolve()
    best_depth = -1
    for project, project_level in config["projects"].items():
        root = Path(project).resolve()
        if current == root or root in current.parents:
            depth = len(root.parts)
            if depth > best_depth:
                best_depth = depth
                level = project_level
    return level


def _is_sandbox_retry(payload: dict) -> bool:
    """Claude Code sets dangerouslyDisableSandbox; Codex explains the escalation in description."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    if tool_input.get("dangerouslyDisableSandbox") is True:
        return True
    description = tool_input.get("description")
    return isinstance(description, str) and SANDBOX_KEYWORD.search(description) is not None


def _log_approval(payload: dict, level: str) -> None:
    """Write a best-effort audit record without affecting the decision."""
    data_dir = _plugin_dir("DATA")
    if data_dir is None:
        return
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        record = {
            "time_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "level": level,
            "session_id": payload.get("session_id"),
            "tool_name": payload.get("tool_name"),
            "cwd": payload.get("cwd"),
            "command": tool_input.get("command"),
            "file_path": tool_input.get("file_path"),
            "description": tool_input.get("description"),
        }
        with (data_dir / "approvals.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "PermissionRequest":
            return 0
        config = _load_config()
        level = _level(config, payload.get("cwd"))
    except (OSError, ValueError, TypeError, RuntimeError):
        # Invalid input or configuration: leave the decision to the host.
        return 0

    if level == "ask" or payload.get("tool_name") in config["skip_tools"]:
        return 0
    if level == "retry_without_sandbox" and not _is_sandbox_retry(payload):
        return 0

    _log_approval(payload, level)
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}},
        sys.stdout,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
