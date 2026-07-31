"""Wire the team graph.

    bootstrap -> architect -+-> dev (one per work item) -+-> integrate -> tester_final
                            +-> tester_scaffold ---------+       |            |
                                                                 |            +-> deliver -> report
                                            conflict retry <-----+            |
                                            repair retry <--------------------+

Both retry paths lead back to ``dev`` and are bounded by config, so a run always
terminates.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from wiab_team.graph.context import RuntimeContext
from wiab_team.graph.nodes.architect import make_architect
from wiab_team.graph.nodes.bootstrap import make_bootstrap
from wiab_team.graph.nodes.deliver import make_deliver, make_report
from wiab_team.graph.nodes.dev import make_dev
from wiab_team.graph.nodes.integrate import make_integrate
from wiab_team.graph.nodes.tester import make_tester_final, make_tester_scaffold
from wiab_team.graph.routing import after_integrate, after_tests, fan_out
from wiab_team.graph.state import TeamState


def build_graph(ctx: RuntimeContext) -> Any:
    builder = StateGraph(TeamState)

    builder.add_node("bootstrap", make_bootstrap(ctx))
    builder.add_node("architect", make_architect(ctx))
    builder.add_node("dev", make_dev(ctx))
    builder.add_node("tester_scaffold", make_tester_scaffold(ctx))
    builder.add_node("integrate", make_integrate(ctx))
    builder.add_node("tester_final", make_tester_final(ctx))
    builder.add_node("deliver", make_deliver(ctx))
    builder.add_node("report", make_report(ctx))

    builder.add_edge(START, "bootstrap")
    builder.add_edge("bootstrap", "architect")

    # Fan out to one dev per work item, plus the tester when it can start early.
    builder.add_conditional_edges("architect", fan_out, ["dev", "tester_scaffold", END])

    # Both paths join here: integrate only runs once every branch is ready.
    builder.add_edge("dev", "integrate")
    builder.add_edge("tester_scaffold", "integrate")

    builder.add_conditional_edges("integrate", after_integrate(ctx.config), ["dev", "tester_final"])
    builder.add_conditional_edges("tester_final", after_tests(ctx.config), ["dev", "deliver"])

    builder.add_edge("deliver", "report")
    builder.add_edge("report", END)

    return builder


def compile_graph(ctx: RuntimeContext, *, checkpointer: Any | None = None) -> Any:
    return build_graph(ctx).compile(checkpointer=checkpointer)
