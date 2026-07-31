# sw-dev-team

A software development team of agents delivering features.

An architect plans a task and splits it across three developers and a tester.
Each developer works in its own git worktree, so they run concurrently; their
branches are merged, verified, and delivered as commits, a pushed branch, or a
pull request.

This is the **alternative agent runtime** for workinabox. It runs inside the
same sandbox (Firecracker microVM or Docker container) where the Rust
`wiab-agent` runs today; which one is used is a backend config switch, with the
Rust path remaining the default.

## Quick start

```sh
make sync                       # uv sync --all-extras --dev
make check                      # ruff + mypy + pytest
make coverage                   # full report, gates the core modules at 98%
make build                      # wheel + sdist into dist/
```

Every one of those is exactly what CI runs, so a green `make check` locally means
a green PR.

Run a team against a repository, with no API key and no model calls:

```sh
uv run wiab-team run \
  --provider stub \
  --input-file payload.json \
  --workspace /tmp/run
```

That produces a real merged branch and a valid `result.json`. Drop `--provider
stub` and set `ANTHROPIC_API_KEY` to run it for real.

```sh
uv run wiab-team validate-config     # check the environment, run nothing
uv run wiab-team schema input        # JSON Schema for the payload
uv run wiab-team schema result       # JSON Schema for the result document
```

## The graph

```
bootstrap -> architect -+-> dev (one per work item) -+-> integrate -> tester_final -> deliver -> report
                        +-> tester_scaffold ---------+      |              |
                                                   conflict retry     repair retry
                                                            +----> dev <---+
```

- **architect** reads the repo, splits the work, and decides whether the tester
  can start writing tests before the code exists. It creates the worktrees.
- **dev** runs once per work item, concurrently, each in its own worktree.
- **integrate** merges the dev branches. A conflict leaves the work branch
  untouched and sends the developer back with the conflict markers in front of
  it, so it can reconcile both sides rather than re-raising the same conflict.
- **tester_final** runs the suite. A failure sends the developers back.
- Both retry loops are bounded by config, so a run always terminates.

## Releasing

The version comes from the git tag — there is no version literal to bump, and the
wheel can never disagree with the tag.

```sh
git tag -a v0.2.0 -m "Release v0.2.0" && git push origin v0.2.0
```

That publishes a wheel, an sdist, and their `.sha256` files to a GitHub release
with generated notes. Assets keep their canonical Python filenames rather than
the `<name>-v<version>-<target>` convention the other repos use, because `pip`
and `uv` parse the wheel name to resolve an install.

`iac/images/team/build.sh` installs a released wheel by default, so the container
images are built from a pinned artifact. Set `TEAM_SRC` to build from a local
checkout instead.

## Documentation

| Document | Contents |
|---|---|
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | The input payload and result document — the contract with the Rust backend. |
| [`docs/ENV.md`](docs/ENV.md) | Every environment variable. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Why the pieces are shaped the way they are. |

## Layout

```
src/wiab_team/
  models/      payload, plan, and result schemas (pydantic)
  graph/       state, nodes, routing, prompts
  tools/       ToolProvider protocol; Claude Agent SDK and stub implementations
  vcs/         git wrapper and worktree lifecycle
  forge/       GitHub and workinabox pull requests
  delivery/    local / push / pr strategies, and the result artifacts
  checkpoint/  LangGraph Postgres checkpointing
```

Graph nodes never import the SDK or HTTP directly — they receive a
`ToolProvider`, `WorktreeManager`, and `DeliveryStrategy`. That is what lets the
entire graph be tested against a real git repository with no API key. (The
tester node does spawn subprocesses, since running the suite is its job; see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).)

## Prompts

The five prompts in `src/wiab_team/graph/prompts/` are plain markdown. To change
one without rebuilding the image, point `WIAB_TEAM_PROMPTS_DIR` at a directory
containing `<name>.md` overrides — anything you don't override falls back to the
packaged default. A misspelt filename is reported at startup rather than
silently ignored.

## Who does what

| Role | Owns |
|---|---|
| Architect | Splits the work, decides the test command, decides whether the tester starts early. Writes no code. |
| Developers | Their slice, **including its unit tests**. Each in its own worktree. |
| Tester | Integration testing of the merged whole against the acceptance criteria. Does not patch developers' unit tests — reports what's broken. |
