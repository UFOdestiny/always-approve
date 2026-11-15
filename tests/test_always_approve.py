import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "hooks/always_approve.py"
ENV_NAMES = (
    "ALWAYS_APPROVE_LEVEL", "ALWAYS_APPROVE_CONFIG",
    "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "PLUGIN_ROOT", "PLUGIN_DATA",
)


def claude_request(tool_name="Bash", cwd="/tmp/project", disable_sandbox=False):
    tool_input = {"command": "rm -rf build", "description": "Remove build output"}
    if disable_sandbox:
        tool_input["dangerouslyDisableSandbox"] = True
    return {
        "session_id": "s1",
        "hook_event_name": "PermissionRequest",
        "tool_name": tool_name,
        "cwd": cwd,
        "tool_input": tool_input,
    }


def codex_request(description="retry without sandbox", cwd="/tmp/project"):
    return {
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "cwd": cwd,
        "tool_input": {"description": description},
    }


class ApprovalPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if k not in ENV_NAMES}
        self.env.update(CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_PLUGIN_DATA=str(self.data))

    def run_hook(self, payload):
        result = subprocess.run(
            ["python3", str(HOOK)],
            input=json.dumps(payload), text=True, capture_output=True, env=self.env, check=True,
        )
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout else None

    def write_config(self, config):
        (self.data / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def assertAllowed(self, output):
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PermissionRequest")
        self.assertEqual(output["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_default_approves_every_tool(self):
        for tool in ("Bash", "Edit", "Write", "WebFetch", "mcp__server__tool"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.run_hook(claude_request(tool)))
        self.assertAllowed(self.run_hook(codex_request("run privileged command")))

    def test_codex_environment_names(self):
        self.env = {k: v for k, v in self.env.items() if not k.startswith("CLAUDE_PLUGIN_")}
        self.env.update(PLUGIN_ROOT=str(PLUGIN), PLUGIN_DATA=str(self.data))
        self.assertAllowed(self.run_hook(codex_request()))
        self.write_config({"default_level": "ask"})
        self.assertIsNone(self.run_hook(codex_request()))
        self.assertTrue((self.data / "approvals.jsonl").is_file())

    def test_interactive_tools_are_left_to_the_user(self):
        for tool in ("AskUserQuestion", "ExitPlanMode"):
            with self.subTest(tool=tool):
                self.assertIsNone(self.run_hook(claude_request(tool)))
        self.write_config({"default_level": "all", "skip_tools": []})
        self.assertAllowed(self.run_hook(claude_request("ExitPlanMode")))

    def test_sandbox_retry_level(self):
        self.env["ALWAYS_APPROVE_LEVEL"] = "retry_without_sandbox"
        self.assertAllowed(self.run_hook(claude_request(disable_sandbox=True)))
        self.assertIsNone(self.run_hook(claude_request()))
        for description in ("请在沙盒外重试该命令", "沙盒执行失败", "Sandbox blocked the command"):
            with self.subTest(description=description):
                self.assertAllowed(self.run_hook(codex_request(description)))
        for description in ("执行失败，请重试", "run privileged command"):
            with self.subTest(description=description):
                self.assertIsNone(self.run_hook(codex_request(description)))

    def test_all_and_ask_levels(self):
        payload = claude_request()
        payload.pop("tool_input")
        self.env["ALWAYS_APPROVE_LEVEL"] = "all"
        self.assertAllowed(self.run_hook(payload))
        self.env["ALWAYS_APPROVE_LEVEL"] = "ask"
        self.assertIsNone(self.run_hook(claude_request()))
        self.assertIsNone(self.run_hook(codex_request()))

    def test_project_rules_and_environment_override(self):
        self.write_config({
            "default_level": "ask",
            "projects": {
                "/tmp/project": "retry_without_sandbox",
                "/tmp/project/trusted": "all",
            },
        })
        self.assertIsNone(self.run_hook(claude_request(cwd="/tmp/elsewhere")))
        self.assertAllowed(self.run_hook(claude_request(cwd="/tmp/project/child", disable_sandbox=True)))
        self.assertIsNone(self.run_hook(claude_request(cwd="/tmp/project/child")))
        self.assertIsNone(self.run_hook(claude_request(cwd="/tmp/project-neighbor", disable_sandbox=True)))
        self.assertAllowed(self.run_hook(claude_request(cwd="/tmp/project/trusted/child")))
        self.env["ALWAYS_APPROVE_LEVEL"] = "ask"
        self.assertIsNone(self.run_hook(claude_request(cwd="/tmp/project/trusted")))

    def test_explicit_config_path(self):
        explicit = self.data / "explicit.json"
        explicit.write_text(json.dumps({"default_level": "ask"}), encoding="utf-8")
        self.env["ALWAYS_APPROVE_CONFIG"] = str(explicit)
        self.assertIsNone(self.run_hook(claude_request()))

    def test_invalid_input_returns_no_decision(self):
        self.write_config({"default_level": "all", "projects": {"relative/path": "all"}})
        self.assertIsNone(self.run_hook(claude_request()))
        self.write_config({"default_level": "all", "skip_tools": "Bash"})
        self.assertIsNone(self.run_hook(claude_request()))
        self.write_config({"default_level": "codex"})
        self.assertIsNone(self.run_hook(claude_request()))
        (self.data / "config.json").unlink()
        self.env["ALWAYS_APPROVE_LEVEL"] = "yes"
        self.assertIsNone(self.run_hook(claude_request()))
        result = subprocess.run(
            ["python3", str(HOOK)], input="not json", text=True, capture_output=True, env=self.env, check=True,
        )
        self.assertEqual(result.stdout, "")

    def test_other_events_are_ignored(self):
        payload = claude_request()
        payload["hook_event_name"] = "PreToolUse"
        self.assertIsNone(self.run_hook(payload))

    def test_approvals_are_logged(self):
        self.run_hook(claude_request())
        self.run_hook(claude_request("AskUserQuestion"))
        records = [
            json.loads(line)
            for line in (self.data / "approvals.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "Bash")
        self.assertEqual(records[0]["command"], "rm -rf build")


if __name__ == "__main__":
    unittest.main()
