"""LEARNER-OWNED. The same agent, built with LangGraph. It must meet the SAME behavioural requirements as ``agent/loop.py``.

What the framework gives us: a state object, named nodes, edges (including conditional ones) and streaming of what each node did.
What it does NOT give us, and what stays visible here and in the shared modules: the tool schemas, the dispatch of tool calls
(``agent/tools.py``), the message flow (messages are plain OpenAI-style dicts in the state), the repair counter, the caps, and the
interpretation of check results (``agent/loop.py`` helpers ``after_tools`` / ``when_model_stops``). LangGraph's ``recursion_limit``
counts super-steps and raises ``GraphRecursionError``; it is set well above what the course step cap can reach and is only a
backstop. The course caps (step cap, repair cap) are enforced in our own nodes so that stop reasons are identical in both versions.

Graph:   START -> model --(tool calls)--> tools --(continue)--> model
                    |                       |
                    |--(no tool calls)--> no_tools --(continue)--> model
                    '--(stop)--> END        '--(stop)--> END
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer

from ..agent.context import RunContext
from ..agent.loop import (BuildOutcome, BuildTask, after_tools, finish, initial_messages, model_turn, run_tool_calls, when_model_stops)
from ..agent.tools import BuildState
from ..supplied.model_transport import TransportError


class GraphState(TypedDict):
    messages: Annotated[list, operator.add]     # OpenAI-style dicts; new messages are appended by the reducer
    turns: int
    calls: list                                  # tool calls requested by the last model turn
    stop: str | None
    error: str | None
    bs: Any                                      # our BuildState dataclass (repairs, last trusted report, ...), shared with tools


def build_graph(ctx: RunContext):
    limits = ctx.limits

    def model(state: GraphState) -> dict:
        if state["turns"] >= limits.max_model_turns:
            return {"stop": "step_cap", "calls": []}
        writer = get_stream_writer()
        turn = state["turns"] + 1
        try:
            reply = model_turn(ctx, state["messages"], turn, on_text=lambda d: writer({"type": "model_text", "turn": turn, "delta": d}))
        except TransportError as e:
            return {"stop": "model_unavailable" if e.retryable else "model_error", "error": e.message, "calls": [], "turns": turn}
        return {"messages": [reply.assistant_message()], "turns": turn, "calls": reply.tool_calls}

    def tools(state: GraphState) -> dict:
        new: list[dict] = []
        run_tool_calls(ctx, state["bs"], new, state["calls"])
        return {"messages": new, "stop": after_tools(ctx, state["bs"]), "calls": []}

    def no_tools(state: GraphState) -> dict:
        new: list[dict] = []
        stop = when_model_stops(ctx, state["bs"], new)
        return {"messages": new, "stop": stop}

    def after_model(state: GraphState) -> str:
        if state["stop"]:
            return END
        return "tools" if state["calls"] else "no_tools"

    def after_node(state: GraphState) -> str:
        return END if state["stop"] else "model"

    g = StateGraph(GraphState)
    g.add_node("model", model)
    g.add_node("tools", tools)
    g.add_node("no_tools", no_tools)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", after_model, {"tools": "tools", "no_tools": "no_tools", END: END})
    g.add_conditional_edges("tools", after_node, {"model": "model", END: END})
    g.add_conditional_edges("no_tools", after_node, {"model": "model", END: END})
    return g.compile()


def run_build(ctx: RunContext, task: BuildTask) -> BuildOutcome:
    bs = BuildState(roles=task.roles)
    graph = build_graph(ctx)
    init: GraphState = {"messages": initial_messages(ctx, task), "turns": 0, "calls": [], "stop": None, "error": None, "bs": bs}
    ctx.tracer.event("build_start", kind=task.kind, impl="langgraph", max_model_turns=ctx.limits.max_model_turns,
                     max_repairs=ctx.limits.max_repairs)
    final = dict(init)
    try:
        for mode, chunk in graph.stream(init, config={"recursion_limit": 4 * ctx.limits.max_model_turns + 12},
                                        stream_mode=["updates", "custom"]):
            if mode == "custom":                       # token deltas written by the model node
                ctx.tracer.live(chunk["type"], turn=chunk["turn"], delta=chunk["delta"])
            else:                                      # one entry per node that ran
                for node, upd in chunk.items():
                    ctx.tracer.event("graph_node", node=node, keys=sorted(upd or {}))
                    for k, v in (upd or {}).items():
                        final[k] = (final.get(k, []) + v) if k == "messages" else v
    except GraphRecursionError:
        final["stop"] = "framework_backstop"
        ctx.tracer.event("graph_recursion_limit")
    return finish(ctx, bs, task, final["stop"] or "model_stopped", final["turns"], final["error"])
