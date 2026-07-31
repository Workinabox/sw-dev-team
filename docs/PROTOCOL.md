# Protocol

The contract between the workinabox backend (Rust) and this package. Two documents:
an **input payload** the backend produces, and a **result document** this package
always writes.

How the payload is *delivered* is deliberately not fixed yet — today it is a file
path passed to the CLI. The schemas below are stable regardless of transport, so
the transport can be decided later without reworking either side.

Both schemas are versioned independently. Generate them mechanically:

```
uv run wiab-team schema input
uv run wiab-team schema result
```

Unknown fields are **rejected** on both sides (`extra="forbid"`). A field added
here without a matching change on the other side fails loudly rather than being
silently dropped.

## Input payload — `schema_version: 1`

```json
{
  "schema_version": 1,
  "run_id": "run-2026-07-31-abc123",
  "task": {
    "external_id": "W-42",
    "title": "Add rate limiting to the public API",
    "description": "Long-form context for the architect.",
    "acceptance_criteria": ["429 after 100 req/min", "limit is configurable"]
  },
  "repo": {
    "remote": "https://wiab.example.com/repos/R-3.git",
    "base_branch": "main",
    "forge": "workinabox",
    "workinabox_repo_id": "R-3",
    "workinabox_api_url": "https://wiab.example.com"
  },
  "delivery": "pr",
  "max_devs": 3
}
```

| Field | Notes |
|---|---|
| `run_id` | Backend-generated, unique. Used as the checkpointer thread id and in branch names. |
| `task.external_id` | Opaque to this package. The backend's identifier for the unit of work. |
| `repo.remote` | Any git URL the sandbox can reach. See credentials below. |
| `repo.forge` | `github` \| `workinabox` \| `none`. Determines how a PR is opened. |
| `delivery` | `local` \| `push` \| `pr`. Anything but `local` requires a forge. |
| `max_devs` | Cap on parallel dev agents (1–8). The architect may plan fewer, never more. |

Forge-conditional fields, validated at parse time:

- `forge: "workinabox"` requires `workinabox_repo_id` (the `R-<n>` the PR routes key on) and `workinabox_api_url`.
- `forge: "github"` requires `github_repo` (`"owner/name"`).

## Result document — `schema_version: 1`

Written to `<workspace>/result.json`, alongside a human-readable `report.md`.
**Written on every path, including failure** — the backend never has to infer an
outcome from an exit code.

```json
{
  "schema_version": 1,
  "run_id": "run-2026-07-31-abc123",
  "status": "succeeded",
  "work_branch": "wiab/run-2026-07-31-abc123",
  "plan_summary": "Split into limiter core, middleware wiring, and config.",
  "dev_results": [
    {
      "work_item_id": "w1",
      "branch": "wiab/run-2026-07-31-abc123/dev-1",
      "summary": "Added the token bucket limiter.",
      "commits": [{ "sha": "a1b2c3d", "message": "Add token bucket limiter" }],
      "files_changed": ["src/limiter.rs"],
      "succeeded": true,
      "error": null
    }
  ],
  "integration": {
    "work_branch": "wiab/run-2026-07-31-abc123",
    "merged_branches": ["wiab/run-2026-07-31-abc123/dev-1"],
    "conflicts": []
  },
  "test_reports": [
    { "phase": "final", "command": "cargo test", "passed": true, "exit_code": 0, "output_tail": "..." }
  ],
  "delivery": {
    "strategy": "pr",
    "branch": "wiab/run-2026-07-31-abc123",
    "pushed": true,
    "pull_request_url": "https://wiab.example.com/pull-requests/PR-7",
    "pull_request_id": "PR-7",
    "error": null
  },
  "token_usage": { "architect": { "input_tokens": 1200, "output_tokens": 800 } },
  "events": [{ "node": "architect", "message": "planned 3 work items" }],
  "error": null
}
```

### `status`

| Value | Meaning |
|---|---|
| `succeeded` | Work landed on the branch, the final test pass was green, and delivery completed. |
| `partial` | Work landed, but something fell short — tests still failing after the repair budget, or an unresolved merge conflict. The branch is real and inspectable. |
| `failed` | No usable branch was produced. `error` explains why. |

`partial` exists so the backend can distinguish "the agents produced something a
human should look at" from "this run produced nothing". Treating `partial` as a
failure throws away usable work.

### Branch naming

- Work branch: `wiab/<run_id>` — the one that gets pushed and PR'd
- Dev branch: `wiab/<run_id>-dev-<n>` — internal to the run

The dev suffix is a dash, not a slash: `wiab/<run_id>/dev-1` would require
`refs/heads/wiab/<run_id>` to be both a ref and a directory, which git rejects.

Every branch a run creates is under the `wiab/` prefix, so a run's output can be
identified and cleaned up without a manifest.

## Credentials

Injected by the backend as environment variables at sandbox launch. Nothing is
baked into the image, and nothing sensitive appears in either document above.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Model access. |
| `WIAB_TEAM_GIT_TOKEN` | Push/PR credential. For a workinabox remote this is the access token used as the **HTTP Basic password** (the mechanism `git_http.rs` already implements — the username is ignored). For GitHub it is a PAT. |

See `docs/ENV.md` for the full environment variable table.
