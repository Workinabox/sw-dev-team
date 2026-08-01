# Environment

Every variable this package reads is resolved once, at startup, in
`src/wiab_team/config.py` — the same single-choke-point convention the backend
uses in `backend/src/config.rs`. There are no config files.

Validation happens before any work begins. `uv run wiab-team validate-config`
checks the environment without running anything.

There are **two modes**, and they read different things:

- `wiab-team work` — the long-lived team. Pulls issues from a board until
  stopped. Needs the **Team worker** section below; everything else is optional.
- `wiab-team run --input-file` — one issue from a payload file. Needs nothing
  from that section, because the payload carries the repo and the task.

In the container image the default is `work`. Setting `WIAB_TEAM_PAYLOAD` to an
existing file switches it to the one-shot `run` instead — see
`iac/images/team/entrypoint.sh`. That variable is read by the entrypoint, not by
this package.

## Model

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required unless the provider is `stub`. |
| `WIAB_TEAM_MODEL` | `claude-opus-4-8` | Default model for every role. |
| `WIAB_TEAM_EFFORT` | unset | Default effort: `low`, `medium`, `high`, `xhigh`, `max`. |
| `WIAB_TEAM_ARCHITECT_MODEL` / `_EFFORT` | falls back to the defaults | |
| `WIAB_TEAM_DEV_MODEL` / `_EFFORT` | falls back to the defaults | |
| `WIAB_TEAM_TESTER_MODEL` / `_EFFORT` | falls back to the defaults | |

`xhigh` is the recommended effort for coding and agentic work on this model
family. Leave it unset to take the API default (`high`).

## Runtime

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_TOOL_PROVIDER` | `claude_sdk` | `claude_sdk` or `stub`. |
| `WIAB_TEAM_WORKSPACE` | `/workspace` | Must be **writable** — worktrees live here. |
| `WIAB_TEAM_DEV_COUNT` | `3` | Default fan-out (1–8). A payload's `max_devs` overrides it. |
| `WIAB_TEAM_MAX_REPAIR_ROUNDS` | `2` | Retries after a failing test suite (0–10). |
| `WIAB_TEAM_MAX_CONFLICT_ROUNDS` | `1` | Retries after a merge conflict (0–5). |
| `WIAB_TEAM_CLAUDE_CLI` | unset | Path to the `claude` CLI if it is not on `PATH`. |
| `WIAB_TEAM_PROMPTS_DIR` | unset | Directory of `<name>.md` prompt overrides. See below. |

### Prompt overrides

Prompts get tuned constantly, and the packaged copies live inside the installed
package — so changing one in a container would otherwise mean rebuilding the
image. Point `WIAB_TEAM_PROMPTS_DIR` at a directory and any `<name>.md` in it
wins; anything missing falls back to the packaged default.

Recognised names: `architect`, `dev`, `tester_scaffold`, `tester_final`. A file
whose name isn't one of those is reported as a warning at startup — otherwise a
typo looks exactly like an edit that did nothing.

## Team worker

Read by `wiab-team work` only. The first five are **required** — a team with no
board to poll or no repo to clone can do nothing, and failing at startup beats a
container that sits there logging "board empty" against a URL nobody set.

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_API_URL` | — | Base URL of the backend, e.g. `https://wiab.example:8080`. |
| `WIAB_TEAM_TEAM_ID` | — | This team's `TM-<n>` id. Claims are recorded against it. |
| `WIAB_TEAM_BOARD_ID` | — | The `B-<n>` board to pull issues from. |
| `WIAB_TEAM_REPO_REMOTE` | — | Clone URL, `<api>/repos/R-<n>.git`. |
| `WIAB_TEAM_API_TOKEN` | — | Bearer token for the backend. |
| `WIAB_TEAM_BASE_BRANCH` | `main` | Branch each issue is cut from. |
| `WIAB_TEAM_POLL_INTERVAL_SECONDS` | `10` | Wait between polls (0.1–3600). Also how often a pause is noticed. |
| `WIAB_TEAM_STATUS_PORT` | `8081` | Read-only status endpoint. `0` turns it off. |
| `WIAB_TEAM_API_CA_PEM` | unset | The backend's TLS certificate, in PEM. See below. |

**The backend sets all of these itself** when it starts a team — see
`TeamApplicationService::worker_env` in the backend. You only set them by hand
to run a team outside the backend.

The status endpoint serves `/health` and `/status`; the latter reports what the
team is working on and for how long. It is a **debugging surface, not a control
path** — nothing there changes anything, and the backend cannot currently reach
a team container over HTTP anyway. Control is the board: the backend pauses a
team by changing its state and the team asks.

### Trusting the backend

`WIAB_TEAM_API_CA_PEM` carries the backend's own certificate. The backend
generates a self-signed one unless `WIAB_TLS_CERT`/`WIAB_TLS_KEY` are set, so
without this **nothing in the container trusts it and every request fails**.

Trusting exactly that certificate beats disabling verification: the team still
refuses to talk to anything else. It covers all three paths — the board client
and the forge are handed it directly, and it is written to
`<workspace>/backend-ca.pem` with `GIT_SSL_CAINFO` pointed at it, because git
reads a certificate from a file rather than being handed one.

## Delivery

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_GIT_TOKEN` | unset | Required for `push` and `pr`. See below. |

> Named for the agent-era design. For a team started by the backend it is the
> **same token** as `WIAB_TEAM_API_TOKEN` — one credential covers the queue and
> the code.

The **same token** serves both halves of delivery:

- **git push** — used as the HTTP Basic *password* (the username is ignored).
  That is exactly what `backend/crates/wiab-inf/src/git_http.rs` implements, and
  GitHub accepts the same shape for a PAT.
- **pull request** — sent as `Authorization: Bearer <token>` to the workinabox
  JSON API, or to the GitHub REST API.

The token is never written to `.git/config`; it is supplied per command and the
remote is immediately rewritten to its bare form.

## Observability

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |
| `WIAB_TEAM_LOG_JSON` | `1` | Set `0` for human-readable console output. |

On the Docker sandbox path there is no back-channel to the backend, so **stdout
is the observability surface** — keep it JSON in production. The status endpoint
above answers "what is it doing now?"; the log is what survives the container.

## Checkpointing

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_CHECKPOINT_DSN` | unset | Postgres DSN. Omit to run without checkpointing. |

LangGraph creates its own tables. They are confined to the `wiab_team` schema
via a `search_path` on the connection, so they never collide with the backend's
Flyway-style migrations in `public`. An `options` parameter you set yourself in
the DSN is respected and overrides this.

**Pausing needs this set.** A pause stops the run at the next node boundary and
resumes from the checkpoint; with no checkpointer there is nothing to resume
from, so `run_team` refuses a pause it cannot honour. Without a DSN a team still
works — it just cannot be paused mid-issue, only between issues.
