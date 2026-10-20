import pytest
from pydantic import ValidationError

from src.debate_protocol import (
    ArgumentDraft,
    ArgumentUpdateDraft,
    ChallengeDraft,
    DebateArgument,
    DebatePosition,
)


def test_argument_draft_normalizes_and_deduplicates_evidence_ids():
    draft = ArgumentDraft(
        claim="  本土化运营构成竞争壁垒  ",
        reasoning="  长期渠道和产品适配提高复制成本  ",
        evidence_ids=[" E1 ", "E1", " E2"],
    )

    assert draft.claim == "本土化运营构成竞争壁垒"
    assert draft.evidence_ids == ["E1", "E2"]


def test_evidence_challenge_requires_target_evidence_ids():
    with pytest.raises(ValidationError, match="target_evidence_ids"):
        ChallengeDraft(
            challenge_type="evidence",
            target_argument_id="A1",
            reason="证据时间过旧",
            investigation_question="检查原始来源和时效性",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "argument_id": "A1",
            "action": "maintain",
            "reason": "继续保留",
            "evidence_ids": [],
            "response_to_challenge_ids": ["C1"],
        },
        {
            "argument_id": "A1",
            "action": "revise",
            "reason": "缩小结论范围",
            "revised_claim": None,
            "revised_reasoning": "新的推理",
            "evidence_ids": ["E1"],
            "response_to_challenge_ids": ["C1"],
        },
        {
            "argument_id": "A1",
            "action": "withdraw",
            "reason": "证据失效",
            "evidence_ids": ["E1"],
            "response_to_challenge_ids": ["C1"],
        },
    ],
)
def test_argument_update_rejects_fields_inconsistent_with_action(payload):
    with pytest.raises(ValidationError):
        ArgumentUpdateDraft.model_validate(payload)


def test_position_rejects_duplicate_argument_ids():
    argument = DebateArgument(
        argument_id="A1",
        claim="观点",
        reasoning="推理",
        evidence_ids=["E1"],
    )

    with pytest.raises(ValidationError, match="argument_id"):
        DebatePosition(
            agent_id="support-agent",
            stance="support",
            round_number=0,
            arguments=[argument, argument.model_copy(deep=True)],
        )
