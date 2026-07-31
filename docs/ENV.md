# Environment

Every variable this package reads is resolved once, at startup, in
`src/wiab_team/config.py` — the same single-choke-point convention the backend
uses in `backend/src/config.rs`. There are no config files.

Validation happens before any work begins. `uv run wiab-team validate-config`
checks the environment without running anything.

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

## Delivery

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_GIT_TOKEN` | unset | Required for `push` and `pr`. See below. |

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
is the observability surface** — keep it JSON in production.

## Checkpointing

| Variable | Default | Notes |
|---|---|---|
| `WIAB_TEAM_CHECKPOINT_DSN` | unset | Postgres DSN. Omit to run without checkpointing. |

LangGraph creates its own tables. They are confined to the `wiab_team` schema
via a `search_path` on the connection, so they never collide with the backend's
Flyway-style migrations in `public`. An `options` parameter you set yourself in
the DSN is respected and overrides this.
