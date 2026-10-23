# Security Policy

## Supported versions

Only the latest release line of `argon-orchestrator` (2.x) receives security fixes. Please update to the newest release before reporting, and see [CHANGELOG.md](./CHANGELOG.md) for what changed.

## Reporting a vulnerability

Do not open a public issue for a vulnerability.

Use GitHub private vulnerability reporting: open the **Security** tab of [ivanusto/argon-orchestrator](https://github.com/ivanusto/argon-orchestrator), choose **Report a vulnerability** (Security Advisories), and describe the problem, the agy version, the plugin version and steps to reproduce.

## Hook permission semantics

Verified live on agy 1.4.6 (2026-10-23):

- A hook answer of `{}` is treated as a **deny**.
- `{"decision":"allow"}` means "no objection". agy still shows its own permission prompt for commands and file writes.
- The plugin therefore returns an explicit allow when it has no opinion, and **never bypasses agy permission prompts**.
- `AGY_ARGON_AUTO_ALLOW=1` is the only path that grants `permissionOverrides`, and only for tools that stay inside the workspace. Paths outside the workspace, privilege escalation, root deletes, force pushes and shell-piped downloads are answered `ask`. It is off by default.
- **Scout read-only layer.** A `flash-lite-scout` subagent cannot write files or run mutating commands, whatever its prompt says.
- **The optional judge sends prompts out.** It is off by default. When `AGY_ARGON_JUDGE_URL` (or the fallback `MITOSIS_JUDGE_URL`) is set, low-confidence routing decisions send the task prompt to that URL. The bearer token goes only over https or to a loopback or private-network host unless `AGY_ARGON_JUDGE_ALLOW_HTTP_KEY=1`. Point it only at an endpoint you trust.
- **State files** are written atomically under a fail-open flock; a lock timeout never blocks the agent.
