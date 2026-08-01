"""Write the run's result document and human-readable report.

Both are written on every path, including failure — the backend must never have
to infer an outcome from an exit code.
"""

from __future__ import annotations

import json
from pathlib import Path

from wiab_team.models.result import RunStatus, TeamRunResult

RESULT_FILENAME = "result.json"
REPORT_FILENAME = "report.md"


def write(result: TeamRunResult, workspace: Path) -> tuple[Path, Path]:
    workspace.mkdir(parents=True, exist_ok=True)
    result_path = workspace / RESULT_FILENAME
    report_path = workspace / REPORT_FILENAME
    result_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(render(result), encoding="utf-8")
    return (result_path, report_path)


def render(result: TeamRunResult) -> str:
    icon = {
        RunStatus.SUCCEEDED: "✅",
        RunStatus.PARTIAL: "⚠️",
        RunStatus.FAILED: "❌",
        RunStatus.PAUSED: "⏸️",
    }[result.status]

    lines = [f"# Run `{result.run_id}` — {icon} {result.status.value}", ""]

    if result.error:
        lines += [f"**Error:** {result.error}", ""]
    if result.work_branch:
        lines += [f"**Branch:** `{result.work_branch}`", ""]
    if result.plan_summary:
        lines += ["## Plan", "", result.plan_summary, ""]

    if result.dev_results:
        lines += ["## Developers", ""]
        for dev in result.dev_results:
            state = "ok" if dev.succeeded else f"**failed** — {dev.error}"
            lines.append(f"### `{dev.work_item_id}` ({state})")
            lines.append("")
            if dev.summary:
                lines += [dev.summary, ""]
            if dev.files_changed:
                lines += [f"- `{path}`" for path in dev.files_changed] + [""]

    if result.integration:
        lines += ["## Integration", ""]
        merged = result.integration.merged_branches
        lines.append(f"Merged {len(merged)} branch(es) into `{result.integration.work_branch}`.")
        lines.append("")
        for conflict in result.integration.conflicts:
            lines.append(
                f"- ⚠️ unresolved conflict in `{conflict.work_item_id}`: "
                f"{', '.join(conflict.paths) or 'unknown paths'}"
            )
        if result.integration.conflicts:
            lines.append("")

    for report in result.test_reports:
        verdict = "passed" if report.passed else "failed"
        lines += [f"## Tests ({report.phase}) — {verdict}", ""]
        if report.command:
            lines += [f"`{report.command}`", ""]
        if report.output_tail:
            lines += ["```", report.output_tail.strip(), "```", ""]

    if result.delivery:
        lines += ["## Delivery", "", f"Strategy: `{result.delivery.strategy}`"]
        if result.delivery.pull_request_url:
            lines.append(f"Pull request: {result.delivery.pull_request_url}")
        if result.delivery.error:
            lines.append(f"Failed: {result.delivery.error}")
        lines.append("")

    if result.token_usage:
        lines += ["## Tokens", "", "| Role | In | Out |", "|---|---:|---:|"]
        lines += [
            f"| {role} | {usage.input_tokens} | {usage.output_tokens} |"
            for role, usage in sorted(result.token_usage.items())
        ]
        lines.append("")

    if result.events:
        lines += ["## Timeline", ""]
        lines += [f"- `{event.node}` — {event.message}" for event in result.events]
        lines.append("")

    return "\n".join(lines)
