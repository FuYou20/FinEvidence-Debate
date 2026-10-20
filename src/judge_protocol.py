from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .debate_protocol import DebateStance
from .evidence_protocol import EvidenceBundle


ArgumentAssessmentStatus = Literal[
    "well_supported",
    "partially_supported",
    "unsupported",
    "unverifiable",
]
VerdictLabel = Literal[
    "support_preferred",
    "oppose_preferred",
    "mixed",
    "insufficient",
]


def _normalize_required_string(value, *, field_name):
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def _normalize_string_list(values, *, field_name):
    normalized_values = []
    seen = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} 不能包含空字符串")
        if normalized not in seen:
            normalized_values.append(normalized)
            seen.add(normalized)
    return normalized_values


class ArgumentAssessment(BaseModel):
    """裁判对一条最终观点的证据与推理质量评价。"""

    argument_id: str
    stance: DebateStance
    assessment: ArgumentAssessmentStatus
    reason: str
    accepted_evidence_ids: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)

    @field_validator("argument_id", "reason")
    @classmethod
    def normalize_required_strings(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("accepted_evidence_ids", "unresolved_issues")
    @classmethod
    def normalize_string_lists(cls, values, info):
        return _normalize_string_list(
            values,
            field_name=info.field_name,
        )


class JudgeVerificationRequest(BaseModel):
    """裁判认为会影响最终结论的证据复核请求。"""

    evidence_ids: list[str] = Field(min_length=1)
    verification_goal: str
    reason: str

    @field_validator("verification_goal", "reason")
    @classmethod
    def normalize_required_strings(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("evidence_ids")
    @classmethod
    def normalize_evidence_ids(cls, values):
        return _normalize_string_list(
            values,
            field_name="evidence_ids",
        )


class SideScore(BaseModel):
    """一方在五个维度上的论证质量分数，均为 1 到 5 分。"""

    evidence_quality: int = Field(ge=1, le=5)
    reasoning_quality: int = Field(ge=1, le=5)
    coverage: int = Field(ge=1, le=5)
    rebuttal_quality: int = Field(ge=1, le=5)
    uncertainty_handling: int = Field(ge=1, le=5)

    @property
    def total(self):
        return sum(
            (
                self.evidence_quality,
                self.reasoning_quality,
                self.coverage,
                self.rebuttal_quality,
                self.uncertainty_handling,
            )
        )


class FinalVerdictDraft(BaseModel):
    """模型根据辩论和复核结果给出的最终结构化裁决。"""

    verdict: VerdictLabel
    support_score: SideScore
    oppose_score: SideScore
    argument_assessments: list[ArgumentAssessment] = Field(
        default_factory=list
    )
    decisive_reasons: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    summary: str

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value):
        return _normalize_required_string(value, field_name="summary")

    @field_validator("decisive_reasons", "unresolved_issues")
    @classmethod
    def normalize_string_lists(cls, values, info):
        return _normalize_string_list(
            values,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def validate_explanation(self):
        if self.verdict == "insufficient":
            if not self.unresolved_issues:
                raise ValueError(
                    "verdict 为 insufficient 时必须说明 unresolved_issues"
                )
        elif not self.decisive_reasons:
            raise ValueError(
                "非 insufficient 裁决必须说明 decisive_reasons"
            )
        return self


class PreliminaryJudgment(BaseModel):
    """一次完成初步裁决；只在关键真实性疑问存在时申请复核。"""

    verdict_draft: FinalVerdictDraft
    verification_requests: list[JudgeVerificationRequest] = Field(
        default_factory=list
    )


class JudgeVerdict(FinalVerdictDraft):
    """裁判 Agent 的最终输出及其实际执行的证据复核记录。"""

    judge_id: str
    verification_results: list[EvidenceBundle] = Field(
        default_factory=list
    )

    @field_validator("judge_id")
    @classmethod
    def normalize_judge_id(cls, value):
        return _normalize_required_string(value, field_name="judge_id")
