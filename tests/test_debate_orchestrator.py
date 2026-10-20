from src.debate_agent import ResearchDebateAgent
from src.debate_orchestrator import run_debate


def evidence_gateway(*, request):
    return {
        "request_id": request.request_id,
        "mode": request.mode,
        "status": "sufficient",
        "evidence": [
            {
                "evidence_id": f"E:{request.request_id}",
                "text": "用于测试辩论流程的可追溯材料",
            }
        ],
    }


def generate_arguments(*, stance, evidence, **_kwargs):
    return [
        {
            "claim": f"{stance} 方的初始观点",
            "reasoning": "该观点由当前证据支持",
            "evidence_ids": [evidence[0]["evidence_id"]],
        }
    ]


def generate_one_claim_challenge(*, opponent_position, **_kwargs):
    target = opponent_position["arguments"][0]
    return [
        {
            "challenge_type": "claim",
            "target_argument_id": target["argument_id"],
            "reason": "对方的推理还存在其他解释",
            "investigation_question": "寻找能够检验该推理的材料",
        }
    ]


def generate_maintain_update(
    *,
    current_position,
    investigations,
    **_kwargs,
):
    target = current_position["arguments"][0]
    challenge = investigations[0]["challenge"]
    return [
        {
            "argument_id": target["argument_id"],
            "action": "maintain",
            "reason": "检查后仍保留原观点",
            "evidence_ids": target["evidence_ids"],
            "response_to_challenge_ids": [challenge["challenge_id"]],
        }
    ]


def make_agent(agent_id, stance, *, generate_challenges):
    return ResearchDebateAgent(
        agent_id=agent_id,
        stance=stance,
        request_evidence=evidence_gateway,
        generate_arguments=generate_arguments,
        generate_challenges=generate_challenges,
        generate_updates=generate_maintain_update,
    )


def test_orchestrator_respects_max_rounds():
    supporting_agent = make_agent(
        "support-agent",
        "support",
        generate_challenges=generate_one_claim_challenge,
    )
    opposing_agent = make_agent(
        "oppose-agent",
        "oppose",
        generate_challenges=generate_one_claim_challenge,
    )

    result = run_debate(
        "传音的非洲市场优势是否具有持续性",
        supporting_agent,
        opposing_agent,
        config={
            "max_rounds": 2,
            "max_challenges_per_agent_per_round": 1,
        },
    )

    assert result.stop_reason == "max_rounds"
    assert len(result.rounds) == 2
    assert result.supporting_position.round_number == 2
    assert result.opposing_position.round_number == 2
    assert result.supporting_position.arguments[0].version == 3
    assert result.opposing_position.arguments[0].version == 3
    assert result.initial_supporting_position.arguments[0].version == 1
    assert result.initial_opposing_position.arguments[0].version == 1
    assert all(
        len(round_record.supporting_challenges) == 1
        for round_record in result.rounds
    )


def test_orchestrator_stops_early_when_neither_side_has_challenges():
    no_challenges = lambda **_kwargs: []
    supporting_agent = make_agent(
        "support-agent",
        "support",
        generate_challenges=no_challenges,
    )
    opposing_agent = make_agent(
        "oppose-agent",
        "oppose",
        generate_challenges=no_challenges,
    )

    result = run_debate(
        "辩题",
        supporting_agent,
        opposing_agent,
        config={"max_rounds": 3},
    )

    assert result.stop_reason == "no_new_challenges"
    assert result.rounds == []
    assert result.supporting_position.round_number == 0
    assert result.opposing_position.round_number == 0


def test_display_observer_receives_copies_without_changing_debate_state():
    events = []

    def on_event(event):
        events.append(event["name"])
        if event["name"] == "position_ready":
            event["position"]["arguments"][0]["claim"] = "展示层改动"

    result = run_debate(
        "辩题",
        make_agent("support-agent", "support", generate_challenges=lambda **_kwargs: []),
        make_agent("oppose-agent", "oppose", generate_challenges=lambda **_kwargs: []),
        on_event=on_event,
    )

    assert events[0] == "debate_started"
    assert events[-1] == "debate_completed"
    assert result.supporting_position.arguments[0].claim == "support 方的初始观点"
    assert result.opposing_position.arguments[0].claim == "oppose 方的初始观点"
