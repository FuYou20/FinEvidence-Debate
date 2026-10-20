import pytest

from errors import InvalidCitationError
from src.debate_agent import ResearchDebateAgent
from src.debate_protocol import (
    ChallengeInvestigation,
    DebateArgument,
    DebateChallenge,
    DebatePosition,
)
from src.evidence_protocol import EvidenceBundle


def make_gateway(captured=None):
    def request_evidence(*, request):
        if captured is not None:
            captured.append(request.model_copy(deep=True))
        if request.mode == "stance_research":
            evidence = [
                {
                    "evidence_id": "E-stance",
                    "text": "与调查方向相关的材料",
                }
            ]
        else:
            evidence = [
                {
                    "evidence_id": request.evidence_ids[0],
                    "text": "被查证的原始材料",
                },
                {
                    "evidence_id": "E-independent",
                    "text": "独立来源材料",
                },
            ]
        return {
            "request_id": request.request_id,
            "mode": request.mode,
            "status": "sufficient",
            "evidence": evidence,
        }

    return request_evidence


def make_agent(**overrides):
    dependencies = {
        "agent_id": "support-agent",
        "stance": "support",
        "request_evidence": make_gateway(),
        "generate_arguments": lambda **_kwargs: [],
        "generate_challenges": lambda **_kwargs: [],
        "generate_updates": lambda **_kwargs: [],
    }
    dependencies.update(overrides)
    return ResearchDebateAgent(**dependencies)


def test_build_initial_position_assigns_ids_and_preserves_citations():
    agent = make_agent(
        generate_arguments=lambda **_kwargs: [
            {
                "claim": "本土化运营形成竞争壁垒",
                "reasoning": "本土产品和渠道提高复制成本",
                "evidence_ids": ["E-stance"],
            }
        ]
    )

    result = agent.build_initial_position(
        "传音的非洲市场优势是否持续",
        research_focus=["本土化"],
    )

    assert result.position.stance == "support"
    assert result.position.round_number == 0
    assert result.position.arguments[0].argument_id == (
        "support-agent:argument:1"
    )
    assert result.position.arguments[0].evidence_ids == ["E-stance"]
    assert result.evidence_bundle.status == "sufficient"


def test_initial_position_rejects_model_invented_evidence_id():
    agent = make_agent(
        generate_arguments=lambda **_kwargs: [
            {
                "claim": "观点",
                "reasoning": "推理",
                "evidence_ids": ["UNKNOWN"],
            }
        ]
    )

    with pytest.raises(InvalidCitationError, match="UNKNOWN"):
        agent.build_initial_position("辩题")


def test_challenge_type_routes_to_correct_evidence_request_mode():
    captured_requests = []
    opponent_position = DebatePosition(
        agent_id="oppose-agent",
        stance="oppose",
        round_number=0,
        arguments=[
            DebateArgument(
                argument_id="OPP-A1",
                claim="渠道优势不可持续",
                reasoning="线上渠道削弱原有壁垒",
                evidence_ids=["E-old"],
            )
        ],
    )
    agent = make_agent(
        request_evidence=make_gateway(captured_requests),
        generate_challenges=lambda **_kwargs: [
            {
                "challenge_type": "claim",
                "target_argument_id": "OPP-A1",
                "reason": "该推理忽略线下渠道深度",
                "investigation_question": "线下渠道是否仍有壁垒",
            },
            {
                "challenge_type": "evidence",
                "target_argument_id": "OPP-A1",
                "target_evidence_ids": ["E-old"],
                "reason": "引用材料可能过时",
                "investigation_question": "检查E-old来源和时效性",
            },
        ],
    )

    challenges = agent.create_challenges(
        opponent_position,
        round_number=1,
        max_challenges=2,
    )
    investigations = agent.investigate_challenges(challenges)

    assert [request.mode for request in captured_requests] == [
        "stance_research",
        "verify_evidence",
    ]
    assert captured_requests[0].direction == "support"
    assert captured_requests[0].context_evidence_ids == ["E-old"]
    assert captured_requests[1].evidence_ids == ["E-old"]
    assert len(investigations) == 2


def make_investigation(challenge_id, argument_id, evidence_id):
    challenge = DebateChallenge(
        challenge_id=challenge_id,
        challenger_id="oppose-agent",
        round_number=1,
        challenge_type="claim",
        target_argument_id=argument_id,
        reason="对方提出质疑",
        investigation_question="调查该质疑",
    )
    bundle = EvidenceBundle(
        request_id=f"request-{challenge_id}",
        mode="stance_research",
        status="sufficient",
        evidence=[
            {
                "evidence_id": evidence_id,
                "text": "质疑所使用的新材料",
            }
        ],
    )
    return ChallengeInvestigation(
        challenge=challenge,
        evidence_bundle=bundle,
    )


def test_response_can_maintain_revise_and_withdraw_arguments():
    position = DebatePosition(
        agent_id="support-agent",
        stance="support",
        round_number=0,
        arguments=[
            DebateArgument(
                argument_id="A1",
                claim="观点一",
                reasoning="推理一",
                evidence_ids=["E-old-1"],
            ),
            DebateArgument(
                argument_id="A2",
                claim="观点二",
                reasoning="推理二",
                evidence_ids=["E-old-2"],
            ),
            DebateArgument(
                argument_id="A3",
                claim="观点三",
                reasoning="推理三",
                evidence_ids=["E-old-3"],
            ),
        ],
    )
    investigations = [
        make_investigation("C1", "A1", "E-new-1"),
        make_investigation("C2", "A2", "E-new-2"),
        make_investigation("C3", "A3", "E-new-3"),
    ]
    agent = make_agent(
        generate_updates=lambda **_kwargs: [
            {
                "argument_id": "A1",
                "action": "maintain",
                "reason": "新增材料继续支持原观点",
                "evidence_ids": ["E-old-1", "E-new-1"],
                "response_to_challenge_ids": ["C1"],
            },
            {
                "argument_id": "A2",
                "action": "revise",
                "reason": "原结论时间范围过宽",
                "revised_claim": "修正后的观点二",
                "revised_reasoning": "只对已有年份作出结论",
                "evidence_ids": ["E-new-2"],
                "response_to_challenge_ids": ["C2"],
            },
            {
                "argument_id": "A3",
                "action": "withdraw",
                "reason": "核心证据无法追溯",
                "evidence_ids": [],
                "response_to_challenge_ids": ["C3"],
            },
        ]
    )

    next_position, updates = agent.respond_to_challenges(
        position,
        investigations,
        round_number=1,
    )

    by_id = {
        argument.argument_id: argument
        for argument in next_position.arguments
    }
    assert by_id["A1"].last_action == "maintain"
    assert by_id["A1"].evidence_ids == ["E-old-1", "E-new-1"]
    assert by_id["A2"].last_action == "revise"
    assert by_id["A2"].claim == "修正后的观点二"
    assert by_id["A3"].last_action == "withdraw"
    assert by_id["A3"].status == "withdrawn"
    assert all(argument.version == 2 for argument in by_id.values())
    assert len(updates) == 3
