# Always Approve

`always-approve` is one plugin for both Claude Code and Codex. It answers `PermissionRequest` hooks with a configurable policy. **By default it approves every request.** The plugin is listed in the `UFOdestiny` marketplace hosted in [ufoskill](https://github.com/UFOdestiny/ufoskill).

## Install

```bash
# Claude Code
claude plugin marketplace add UFOdestiny/ufoskill
claude plugin install always-approve@UFOdestiny

# Codex
codex plugin marketplace add UFOdestiny/ufoskill
codex plugin add always-approve@UFOdestiny
```

Update to the latest release:

```bash
# Claude Code
claude plugin marketplace update UFOdestiny
claude plugin update always-approve@UFOdestiny

# Codex
codex plugin marketplace upgrade UFOdestiny
codex plugin add always-approve@UFOdestiny
```

Start a new session after installing or updating. `/hooks` should list one `PermissionRequest` hook from the plugin. In Codex, review and trust the hook when prompted. To try it in Claude Code without installing, run `claude --plugin-dir /path/to/always-approve`.

## Policy levels

| Level | Hook behavior |
| --- | --- |
| `ask` | Return no decision; the host shows its normal prompt. |
| `retry_without_sandbox` | Approve only requests to run outside the sandbox: Claude Code Bash calls with `dangerouslyDisableSandbox: true`, or Codex requests whose `description` contains `沙盒` or `sandbox` (case insensitive). |
| `all` | Approve every request except tools in `skip_tools`. |

The bundled default is [`config.json`](config.json):

```json
{
  "default_level": "all",
  "projects": {},
  "skip_tools": ["AskUserQuestion", "ExitPlanMode"]
}
```

`skip_tools` lists tools the hook never answers. In Claude Code, `AskUserQuestion` and `ExitPlanMode` use the permission prompt for your answer or your plan review, so approving them from a hook would skip you. Set `"skip_tools": []` to approve those too.

To customize the policy, write `config.json` in the plugin's data directory, which the host exposes as `CLAUDE_PLUGIN_DATA` (Claude Code) or `PLUGIN_DATA` (Codex). You can also point `ALWAYS_APPROVE_CONFIG` at a JSON file:

```json
{
  "default_level": "ask",
  "projects": {
    "/absolute/path/to/project": "retry_without_sandbox",
    "/absolute/path/to/project/trusted-subproject": "all"
  }
}
```

Project rules cover the listed directory and its descendants, matched against the session's `cwd`. The most specific match wins. Paths must be absolute. A project checkout cannot supply its own policy.

Configuration order:

1. `ALWAYS_APPROVE_LEVEL` overrides the level for every project, for example `ALWAYS_APPROVE_LEVEL=ask claude`.
2. `ALWAYS_APPROVE_CONFIG` selects a config file explicitly.
3. `config.json` in the plugin data directory, when present.
4. The bundled `config.json`.

An invalid config, an invalid level, or malformed hook input makes the hook return no decision, so the host prompts as usual. Each automatic approval is appended to `approvals.jsonl` in the plugin data directory, with the tool, `cwd`, and command or file path.

## Scope

The hook runs only when the host would otherwise show a permission prompt. Rules evaluated before that point still apply. For example, a Claude Code deny rule blocks a call before it reaches this hook. In Codex, `codex exec` runs with approval policy `never` and raises no approval requests, so the hook only matters in interactive sessions.

`all` removes the manual approval step for every request that reaches this hook. Use it only where that is intended. Organization-managed policy can still disable plugin hooks.

## Uninstall

```bash
claude plugin uninstall always-approve@UFOdestiny
codex plugin remove always-approve@UFOdestiny
```

## Test

```bash
python3 -m unittest discover -s tests -v
claude plugin validate .
```
