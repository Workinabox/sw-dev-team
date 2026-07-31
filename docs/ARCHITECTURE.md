# Architecture

Why the pieces are shaped the way they are. For *what* they are, see the README.

## The graph holds data; the context holds everything else

`TeamState` contains only serializable values. The tool provider, worktree
manager, and delivery strategy live in a `RuntimeContext` that the node factories
close over.

Two things follow. The checkpointer never has to persist a live object. And every
node can be tested by handing it a different context — which is why the whole
graph runs in CI against a real git repository with no API key and no network.

The rule that keeps this true: **nothing under `graph/nodes/` imports
`claude_agent_sdk` or `httpx`.** Model access and network access both go through
a seam.

One deliberate exception, worth knowing about: `tester.py` spawns subprocesses
directly, because running the repository's test suite is the tester's actual job
and the command comes from the architect's plan rather than from a provider. So
the guarantee is "no model and no network in a node", not "no side effects".
Every node still runs in tests without credentials; the tester node just needs a
real working directory to run a command in.

## Reducers are not decoration

Three dev nodes write to the same state keys concurrently. Without a reducer,
LangGraph raises `InvalidUpdateError`. So `dev_results`, `events`, `conflicts`,
and `test_reports` accumulate with `operator.add`, and `dev_branches` and
`token_usage` merge with named functions.

The fields written by exactly one node — `plan`, `integration`, `delivery`,
`status` — deliberately have **no** reducer. A concurrent write there is a bug,
and the default error surfaces it instead of silently picking a winner.

## Worktrees, not a shared checkout

Three agents editing one working tree would collide on the index. Each developer
gets `git worktree add` on its own branch, and integration merges them back.

Dev branches are `wiab/<run>-dev-N`, not `wiab/<run>/dev-N`: the latter would
require `refs/heads/wiab/<run>` to be both a ref and a directory, which git
rejects outright. Worktrees are created serially in the architect node, because
concurrent `git worktree add` calls race on the repository's index lock.

## Conflicts are handed back with the markers in place

When a merge conflicts, the merge is aborted so the work branch is never left
half-merged. The developer is then re-run, and its commits are rebased onto the
current work branch — and if *that* conflicts, **the rebase is deliberately left
open**, markers and all, so the agent can see both sides and reconcile them.

Aborting the rebase instead would hand the developer back the very commit that
conflicts, and no amount of retrying could succeed. This was a real bug, caught
by the end-to-end tests.

Before the rebase is continued, the files are checked for conflict markers. Git
will happily commit `<<<<<<<` into the branch; an unresolved conflict reported
honestly is far better than markers reaching the work branch.

## Integration is cumulative

A retry round that produces no new commits merges nothing. If `integration` were
overwritten each round rather than accumulated, that empty round would erase the
record of work that had already landed, and the run would report `failed` while
the work sat on the branch. Another bug the tests caught; there is a regression
test named for it.

## A run always produces a result document

`result.json` and `report.md` are written on every path, including an unhandled
crash. The backend must never have to infer an outcome from an exit code.

`status` has three values, and `partial` carries real weight: work landed on the
branch, but something fell short — tests still failing after the repair budget,
an unresolved conflict, or a failed delivery. Treating `partial` as a failure
throws away a usable branch.

## Bounded loops

Both retry paths — conflict and repair — lead back to `dev`, and both are capped
by config. Combined with the LangGraph recursion limit, a run terminates whatever
the agents do.

## Push is git; only pull requests need a forge

Pushing works identically against GitHub and workinabox's smart-HTTP transport,
so the `Forge` protocol covers pull request creation only.

The workinabox adapter is the one place that assumes anything about that backend
API, and it degrades honestly: a 404 is reported as "this backend predates the
PullRequest feature; the branch was pushed and can be merged manually" rather
than a bare "not found".

## Credentials never touch disk

A token is embedded in a remote URL for the duration of a single git command.
Immediately after cloning, the remote is rewritten to its bare form, so nothing
sensitive lands in `.git/config` where it would outlive the command. There is a
test asserting exactly that.

## The provider seam

`ToolProvider` is one method. The default implementation wraps the Claude Agent
SDK — which brings the file, search, and shell tools, the agent loop, and context
management, so the graph only has to say *what* each agent should do.

A provider never raises for an agent-level failure; it returns
`succeeded=False`. Raising is reserved for the harness itself being broken. That
distinction is what lets one developer fail without sinking the run.

Note the SDK drives the Node `claude` CLI as a subprocess. The sandbox image
therefore needs Node and `@anthropic-ai/claude-code`, not just Python.
