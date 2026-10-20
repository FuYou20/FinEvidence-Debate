from src.debate_agent import ResearchDebateAgent
from src.debate_graph import DEBATE_GRAPH, run_debate_graph
from src.debate_orchestrator import run_debate


def evidence_gateway(*, request):
    return {
        "request_id": request.request_id,
        "mode": request.mode,
        "status": "sufficient",
        "evidence": [{
            "evidence_id": f"E:{request.request_id}",
            "text": "用于测试 LangGraph 辩论流程的材料",
        }],
    }


def generate_arguments(*, stance, evidence, **_kwargs):
    return [{
        "claim": f"{stance} 方观点",
        "reasoning": "由证据支持",
        "evidence_ids": [evidence[0]["evidence_id"]],
    }]


def generate_challenge(*, opponent_position, **_kwargs):
    target = opponent_position["arguments"][0]
    return [{
        "challenge_type": "claim",
        "target_argument_id": target["argument_id"],
        "reason": "需要检查其他解释",
        "investigation_question": "寻找反例",
    }]


def generate_update(*, current_position, investigations, **_kwargs):
    target = current_position["arguments"][0]
    challenge = investigations[0]["challenge"]
    return [{
        "argument_id": target["argument_id"],
        "action": "maintain",
        "reason": "复核后保留",
        "evidence_ids": target["evidence_ids"],
        "response_to_challenge_ids": [challenge["challenge_id"]],
    }]


def make_agent(agent_id, stance, challenge_generator=generate_challenge):
    return ResearchDebateAgent(
        agent_id=agent_id,
        stance=stance,
        request_evidence=evidence_gateway,
        generate_arguments=generate_arguments,
        generate_challenges=challenge_generator,
        generate_updates=generate_update,
    )


def make_pair(challenge_generator=generate_challenge):
    return (
        make_agent("support-agent", "support", challenge_generator),
        make_agent("oppose-agent", "oppose", challenge_generator),
    )


def test_graph_exposes_explicit_debate_stages():
    nodes = set(DEBATE_GRAPH.get_graph().nodes)

    assert {
        "supporting_initial",
        "opposing_initial",
        "supporting_challenges",
        "opposing_challenges",
        "investigate",
        "update_positions",
        "record_round",
        "finalize",
    } <= nodes


def test_graph_matches_manual_orchestrator_for_bounded_rounds():
    config = {
        "max_rounds": 2,
        "max_challenges_per_agent_per_round": 1,
    }
    manual = run_debate("辩题", *make_pair(), config=config)
    graph = run_debate_graph("辩题", *make_pair(), config=config)

    assert graph.model_dump() == manual.model_dump()
    assert graph.stop_reason == "max_rounds"
    assert len(graph.rounds) == 2


def test_graph_matches_early_stop_and_event_contract():
    no_challenges = lambda **_kwargs: []
    manual_events = []
    graph_events = []
    manual = run_debate(
        "辩题",
        *make_pair(no_challenges),
        config={"max_rounds": 3},
        on_event=lambda event: manual_events.append(event["name"]),
    )
    graph = run_debate_graph(
        "辩题",
        *make_pair(no_challenges),
        config={"max_rounds": 3},
        on_event=lambda event: graph_events.append(event["name"]),
    )

    assert graph.model_dump() == manual.model_dump()
    assert graph.stop_reason == "no_new_challenges"
    assert graph_events == manual_events
