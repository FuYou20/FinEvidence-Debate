import pytest

from src.debate_judge import DebateJudge


def make_score(value):
    return {
        "evidence_quality": value,
        "reasoning_quality": value,
        "coverage": value,
        "rebuttal_quality": value,
        "uncertainty_handling": value,
    }


def make_position(agent_id, stance, argument_id, evidence_id):
    return {
        "agent_id": agent_id,
        "stance": stance,
        "round_number": 0,
        "arguments": [
            {
                "argument_id": argument_id,
                "claim": f"{stance} 观点",
                "reasoning": "由证据支撑",
                "evidence_ids": [evidence_id],
            }
        ],
    }


def make_evidence_bundle(request_id, mode="stance_research"):
    return {
        "request_id": request_id,
        "mode": mode,
        "status": "sufficient",
        "evidence": [
            {
                "evidence_id": f"E:{request_id}",
                "text": "可追溯测试证据",
            }
        ],
    }


def make_debate_result():
    supporting = make_position(
        "support-agent",
        "support",
        "A-support",
        "E1",
    )
    opposing = make_position(
        "oppose-agent",
        "oppose",
        "A-oppose",
        "E2",
    )
    return {
        "topic": "竞争优势是否可持续",
        "initial_supporting_position": supporting,
        "initial_opposing_position": opposing,
        "supporting_position": supporting,
        "opposing_position": opposing,
        "initial_evidence": [
            make_evidence_bundle("support-initial"),
            make_evidence_bundle("oppose-initial"),
        ],
        "rounds": [],
        "stop_reason": "no_new_challenges",
    }


def make_final_verdict(**_kwargs):
    return {
        "verdict": "mixed",
        "support_score": make_score(3),
        "oppose_score": make_score(3),
        "argument_assessments": [],
        "decisive_reasons": ["双方分别覆盖了不同风险和优势"],
        "unresolved_issues": [],
        "summary": "双方均提供了部分有效论证",
    }


def make_preliminary(*, verification_requests=None):
    return {
        "verdict_draft": make_final_verdict(),
        "verification_requests": verification_requests or [],
    }


def test_judge_skips_verification_when_preliminary_has_no_requests():
    evidence_calls = []
    final_calls = []

    def request_evidence(**kwargs):
        evidence_calls.append(kwargs)
        raise AssertionError("不应调用证据 Agent")

    judge = DebateJudge(
        judge_id="judge-agent",
        request_evidence=request_evidence,
        generate_preliminary_judgment=lambda **_kwargs: make_preliminary(),
        generate_final_verdict=lambda **kwargs: final_calls.append(kwargs),
    )

    verdict = judge.judge(make_debate_result())

    assert verdict.verdict == "mixed"
    assert verdict.verification_results == []
    assert evidence_calls == []
    assert final_calls == []


def test_judge_verifies_only_bounded_number_of_decisive_questions():
    evidence_calls = []
    final_calls = []

    def request_evidence(*, request):
        evidence_calls.append(request)
        return make_evidence_bundle(
            request.request_id,
            mode="verify_evidence",
        )

    def generate_final_verdict(**kwargs):
        final_calls.append(kwargs)
        return make_final_verdict()

    requests = [
        {
            "evidence_ids": [f"E{position}"],
            "verification_goal": f"核实问题 {position}",
            "reason": "可能改变裁决",
        }
        for position in range(1, 4)
    ]
    judge = DebateJudge(
        judge_id="judge-agent",
        request_evidence=request_evidence,
        generate_preliminary_judgment=lambda **_kwargs: make_preliminary(
            verification_requests=requests,
        ),
        generate_final_verdict=generate_final_verdict,
    )

    verdict = judge.judge(
        make_debate_result(),
        max_verification_requests=2,
    )

    assert len(evidence_calls) == 2
    assert all(call.mode == "verify_evidence" for call in evidence_calls)
    assert evidence_calls[0].requester == "judge-agent"
    assert len(verdict.verification_results) == 2
    assert len(final_calls[0]["verification_results"]) == 2


def test_judge_rejects_mismatched_evidence_bundle_request_id():
    def request_evidence(*, request):
        return make_evidence_bundle(
            "wrong-request-id",
            mode="verify_evidence",
        )

    judge = DebateJudge(
        judge_id="judge-agent",
        request_evidence=request_evidence,
        generate_preliminary_judgment=lambda **_kwargs: make_preliminary(
            verification_requests=[
                {
                    "evidence_ids": ["E1"],
                    "verification_goal": "核实来源",
                    "reason": "会影响裁决",
                }
            ],
        ),
        generate_final_verdict=make_final_verdict,
    )

    with pytest.raises(ValueError, match="request_id"):
        judge.judge(make_debate_result())


@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_judge_rejects_invalid_verification_limit(value):
    judge = DebateJudge(
        judge_id="judge-agent",
        request_evidence=lambda **_kwargs: None,
        generate_preliminary_judgment=lambda **_kwargs: {},
        generate_final_verdict=make_final_verdict,
    )

    with pytest.raises(ValueError, match="非负整数"):
        judge.judge(
            make_debate_result(),
            max_verification_requests=value,
        )
