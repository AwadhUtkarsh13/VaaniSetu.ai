"""Compiled LangGraph StateGraph with an InMemorySaver checkpointer (keyed by thread_id).

START -> intent_router -> POLICY_QA ----> hybrid_rag_retriever -> grounded_answer -> END
                       -> OUT_OF_SCOPE -> out_of_scope -> END
                       -> ELIGIBILITY / GRIEVANCE -> slot_filler
                             slot_filler -> (missing slot) END            [asks 1 question]
                                         -> (complete) human_confirmation_gate [interrupt()]
                             gate -> CONFIRM -> payload_generator -> END
                                  -> EDIT    -> slot_filler
                                  -> CANCEL  -> END
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from src.agent.llm import AgentLLM
from src.agent.nodes import AgentNodes
from src.agent.state import AgentState
from src.rag.retriever import HybridRetriever


def build_graph(
    llm: AgentLLM,
    retriever: HybridRetriever,
    checkpointer: BaseCheckpointSaver | None = None,
    top_k: int = 8,
) -> CompiledStateGraph:
    nodes = AgentNodes(llm, retriever, top_k=top_k)
    g = StateGraph(AgentState)
    g.add_node("intent_router", nodes.intent_router)
    g.add_node("hybrid_rag_retriever", nodes.hybrid_rag_retriever)
    g.add_node("grounded_answer", nodes.grounded_answer)
    g.add_node("out_of_scope", nodes.out_of_scope)
    g.add_node("slot_filler", nodes.slot_filler)
    g.add_node("human_confirmation_gate", nodes.human_confirmation_gate)
    g.add_node("payload_generator", nodes.payload_generator)

    g.add_edge(START, "intent_router")
    g.add_conditional_edges(
        "intent_router",
        nodes.route_after_router,
        ["hybrid_rag_retriever", "slot_filler", "out_of_scope"],
    )
    g.add_edge("hybrid_rag_retriever", "grounded_answer")
    g.add_edge("grounded_answer", END)
    g.add_edge("out_of_scope", END)
    g.add_conditional_edges("slot_filler", nodes.route_after_slots, ["human_confirmation_gate", END])
    g.add_edge("payload_generator", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


@dataclass
class TurnOutcome:
    state: dict[str, Any]
    response: str  # English response for this turn
    awaiting_confirmation: bool
    resumed: bool  # this turn answered a confirmation interrupt


NodeCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


async def run_turn(
    graph: CompiledStateGraph,
    thread_id: str,
    redacted_text: str,
    on_node: NodeCallback | None = None,
) -> TurnOutcome:
    """Run one (already redacted) user turn, resuming a pending confirmation if there is one.

    `on_node(name, update)` is awaited after each node finishes (used for progress messages).
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    resumed = bool(snapshot.interrupts)
    if resumed:
        graph_input: Any = Command(resume=redacted_text)
    else:
        graph_input = {"messages": [HumanMessage(redacted_text)], "user_message": redacted_text}
    async for chunk in graph.astream(graph_input, config, stream_mode="updates"):
        if on_node is None:
            continue
        for node, update in chunk.items():
            if not node.startswith("__"):
                await on_node(node, update if isinstance(update, dict) else {})
    snapshot = await graph.aget_state(config)
    if snapshot.interrupts:
        prompt = snapshot.interrupts[0].value["prompt"]
        return TurnOutcome(dict(snapshot.values), prompt, True, resumed)
    return TurnOutcome(dict(snapshot.values), snapshot.values.get("response", ""), False, resumed)
