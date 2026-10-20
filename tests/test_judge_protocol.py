import pytest
from pydantic import ValidationError

from src.judge_protocol import (
    ArgumentAssessment,
    FinalVerdictDraft,
    JudgeVerificationRequest,
    SideScore,
)


def make_score(value=3):
    return {
        "evidence_quality": value,
        "reasoning_quality": value,
        "coverage": value,
        "rebuttal_quality": value,
        "uncertainty_handling": value,
    }


def test_argument_assessment_normalizes_lists():
    assessment = ArgumentAssessment(
        argument_id=" A1 ",
        stance="support",
        assessment="partially_supported",
        reason=" 部分证据只能支持较窄结论 ",
        accepted_evidence_ids=[" E1 ", "E1", "E2"],
        unresolved_issues=[" 时效性 ", "时效性"],
    )

    assert assessment.argument_id == "A1"
    assert assessment.accepted_evidence_ids == ["E1", "E2"]
    assert assessment.unresolved_issues == ["时效性"]


def test_verification_request_requires_at_least_one_evidence_id():
    with pytest.raises(ValidationError, match="evidence_ids"):
        JudgeVerificationRequest(
            evidence_ids=[],
            verification_goal="检查时效性",
            reason="该证据会影响裁决",
        )


def test_side_score_exposes_total():
    score = SideScore(**make_score(4))

    assert score.total == 20


def test_insufficient_verdict_requires_unresolved_issue():
    with pytest.raises(ValidationError, match="unresolved_issues"):
        FinalVerdictDraft(
            verdict="insufficient",
            support_score=make_score(),
            oppose_score=make_score(),
            summary="双方证据均不足",
        )


def test_preferred_verdict_requires_decisive_reason():
    with pytest.raises(ValidationError, match="decisive_reasons"):
        FinalVerdictDraft(
            verdict="support_preferred",
            support_score=make_score(4),
            oppose_score=make_score(2),
            summary="支持方论证质量更高",
        )
