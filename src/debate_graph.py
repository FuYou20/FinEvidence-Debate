"""LangGraph orchestration for the evidence-driven debate workflow.

Domain rules remain in ResearchDebateAgent and the Pydantic protocols.  This
module only makes execution state, branches, and bounded loops explicit.
"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .debate_agent import ResearchDebateAgent
from .debate_events import emit_event, validate_observer
from .debate_protocol import DebateConfig, DebateResult, DebateRoundRecord


class DebateGraphState(TypedDict, total=False):
    topic: str
    supporting_agent: ResearchDebateAgent
    opposing_agent: ResearchDebateAgent
    config: DebateConfig
    supporting_focus: list[str] | None
    opposing_focus: list[str] | None
    on_event: Any
    round_number: int
    stop_reason: str

    supporting_initial: Any
    opposing_initial: Any
    supporting_position: Any
    opposing_position: Any
    supporting_challenges: list[Any]
    opposing_challenges: list[Any]
    supporting_investigations: list[Any]
    opposing_investigations: list[Any]
    supporting_updates: list[Any]
    opposing_updates: list[Any]
    rounds: list[DebateRoundRecord]
    result: DebateResult


def _supporting_initial(state):
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="正方搜集证据并立论",
    )
    initial = state["supporting_agent"].build_initial_position(
        state["topic"],
        research_focus=state.get("supporting_focus"),
    )
    emit_event(
        state.get("on_event"),
        "position_ready",
        position=initial.position.model_dump(),
    )
    return {
        "supporting_initial": initial,
        "supporting_position": initial.position,
    }


def _opposing_initial(state):
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="反方搜集证据并立论",
    )
    initial = state["opposing_agent"].build_initial_position(
        state["topic"],
        research_focus=state.get("opposing_focus"),
    )
    emit_event(
        state.get("on_event"),
        "position_ready",
        position=initial.position.model_dump(),
    )
    return {
        "opposing_initial": initial,
        "opposing_position": initial.position,
    }


def _start_round(state):
    round_number = state["round_number"] + 1
    emit_event(
        state.get("on_event"),
        "round_started",
        round_number=round_number,
    )
    return {"round_number": round_number}


def _supporting_challenges(state):
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="正方审查反方观点",
    )
    challenges = state["supporting_agent"].create_challenges(
        state["opposing_position"],
        round_number=state["round_number"],
        max_challenges=(
            state["config"].max_challenges_per_agent_per_round
        ),
    )
    emit_event(
        state.get("on_event"),
        "challenges_ready",
        stance="support",
        challenges=[item.model_dump() for item in challenges],
    )
    return {"supporting_challenges": challenges}


def _opposing_challenges(state):
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="反方审查正方观点",
    )
    challenges = state["opposing_agent"].create_challenges(
        state["supporting_position"],
        round_number=state["round_number"],
        max_challenges=(
            state["config"].max_challenges_per_agent_per_round
        ),
    )
    emit_event(
        state.get("on_event"),
        "challenges_ready",
        stance="oppose",
        challenges=[item.model_dump() for item in challenges],
    )
    return {"opposing_challenges": challenges}


def _decide_after_challenges(state):
    no_challenges = (
        not state["supporting_challenges"]
        and not state["opposing_challenges"]
    )
    if state["config"].stop_when_no_challenges and no_challenges:
        return {"stop_reason": "no_new_challenges"}
    return {}


def _route_after_challenges(state):
    if state["stop_reason"] == "no_new_challenges":
        return "finalize"
    return "investigate"


def _investigate(state):
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="调查正方提出的质疑",
    )
    supporting = state["supporting_agent"].investigate_challenges(
        state["supporting_challenges"]
    )
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="调查反方提出的质疑",
    )
    opposing = state["opposing_agent"].investigate_challenges(
        state["opposing_challenges"]
    )
    return {
        "supporting_investigations": supporting,
        "opposing_investigations": opposing,
    }


def _update_positions(state):
    emit_event(
        state.get("on_event"),
        "stage_started",
        label="正方根据调查保留、修正或撤回观点",
    )
    supporting_position, supporting_updates = (
        state["supporting_agent"].respond_to_challenges(
            state["supporting_position"],
            state["opposing_investigations"],
            round_number=state["round_number"],
        )
    )
    emit_event(
        state.get("on_event"),
        "updates_ready",
        position=supporting_position.model_dump(),
        updates=[item.model_dump() for item in supporting_updates],
    )

    emit_event(
        state.get("on_event"),
        "stage_started",
        label="反方根据调查保留、修正或撤回观点",
    )
    opposing_position, opposing_updates = (
        state["opposing_agent"].respond_to_challenges(
            state["opposing_position"],
            state["supporting_investigations"],
            round_number=state["round_number"],
        )
    )
    emit_event(
        state.get("on_event"),
        "updates_ready",
        position=opposing_position.model_dump(),
        updates=[item.model_dump() for item in opposing_updates],
    )
    return {
        "supporting_position": supporting_position,
        "opposing_position": opposing_position,
        "supporting_updates": supporting_updates,
        "opposing_updates": opposing_updates,
    }


def _record_round(state):
    round_record = DebateRoundRecord(
        round_number=state["round_number"],
        supporting_challenges=state["supporting_challenges"],
        opposing_challenges=state["opposing_challenges"],
        supporting_investigations=state["supporting_investigations"],
        opposing_investigations=state["opposing_investigations"],
        supporting_updates=state["supporting_updates"],
        opposing_updates=state["opposing_updates"],
    )
    return {"rounds": [*state["rounds"], round_record]}


def _route_after_round(state):
    if state["round_number"] >= state["config"].max_rounds:
        return "finalize"
    return "next_round"


def _finalize(state):
    result = DebateResult(
        topic=state["topic"],
        initial_supporting_position=(
            state["supporting_initial"].position.model_copy(deep=True)
        ),
        initial_opposing_position=(
            state["opposing_initial"].position.model_copy(deep=True)
        ),
        supporting_position=state["supporting_position"],
        opposing_position=state["opposing_position"],
        initial_evidence=[
            state["supporting_initial"].evidence_bundle,
            state["opposing_initial"].evidence_bundle,
        ],
        rounds=state["rounds"],
        stop_reason=state["stop_reason"],
    )
    emit_event(
        state.get("on_event"),
        "debate_completed",
        stop_reason=result.stop_reason,
        rounds=len(result.rounds),
    )
    return {"result": result}


def build_debate_graph():
    """Build the reusable graph topology without binding runtime agents."""
    builder = StateGraph(DebateGraphState)
    builder.add_node("supporting_initial", _supporting_initial)
    builder.add_node("opposing_initial", _opposing_initial)
    builder.add_node("start_round", _start_round)
    builder.add_node("supporting_challenges", _supporting_challenges)
    builder.add_node("opposing_challenges", _opposing_challenges)
    builder.add_node("decide_after_challenges", _decide_after_challenges)
    builder.add_node("investigate", _investigate)
    builder.add_node("update_positions", _update_positions)
    builder.add_node("record_round", _record_round)
    builder.add_node("finalize", _finalize)

    builder.add_edge(START, "supporting_initial")
    builder.add_edge("supporting_initial", "opposing_initial")
    builder.add_edge("opposing_initial", "start_round")
    builder.add_edge("start_round", "supporting_challenges")
    builder.add_edge("supporting_challenges", "opposing_challenges")
    builder.add_edge("opposing_challenges", "decide_after_challenges")
    builder.add_conditional_edges(
        "decide_after_challenges",
        _route_after_challenges,
        {
            "investigate": "investigate",
            "finalize": "finalize",
        },
    )
    builder.add_edge("investigate", "update_positions")
    builder.add_edge("update_positions", "record_round")
    builder.add_conditional_edges(
        "record_round",
        _route_after_round,
        {
            "next_round": "start_round",
            "finalize": "finalize",
        },
    )
    builder.add_edge("finalize", END)
    return builder.compile()


DEBATE_GRAPH = build_debate_graph()


def run_debate_graph(
    topic,
    supporting_agent,
    opposing_agent,
    *,
    config=None,
    supporting_focus=None,
    opposing_focus=None,
    on_event=None,
):
    """Run the existing debate semantics through an explicit LangGraph."""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic 必须是非空字符串")
    if not isinstance(supporting_agent, ResearchDebateAgent):
        raise TypeError("supporting_agent 必须是 ResearchDebateAgent")
    if not isinstance(opposing_agent, ResearchDebateAgent):
        raise TypeError("opposing_agent 必须是 ResearchDebateAgent")
    if supporting_agent.stance != "support":
        raise ValueError("supporting_agent 的 stance 必须是 support")
    if opposing_agent.stance != "oppose":
        raise ValueError("opposing_agent 的 stance 必须是 oppose")
    if supporting_agent.agent_id == opposing_agent.agent_id:
        raise ValueError("正反双方的 agent_id 不能相同")

    normalized_topic = topic.strip()
    validated_config = DebateConfig.model_validate(config or {})
    validate_observer(on_event)
    emit_event(
        on_event,
        "debate_started",
        topic=normalized_topic,
        config=validated_config.model_dump(),
    )
    final_state = DEBATE_GRAPH.invoke(
        {
            "topic": normalized_topic,
            "supporting_agent": supporting_agent,
            "opposing_agent": opposing_agent,
            "config": validated_config,
            "supporting_focus": supporting_focus,
            "opposing_focus": opposing_focus,
            "on_event": on_event,
            "round_number": 0,
            "rounds": [],
            "stop_reason": "max_rounds",
        },
        config={"recursion_limit": 100},
    )
    return DebateResult.model_validate(final_state["result"])
