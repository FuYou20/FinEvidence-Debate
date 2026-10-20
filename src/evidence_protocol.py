from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RequestMode = Literal["stance_research", "verify_evidence"]
ResearchDirection = Literal["support", "oppose"]
BundleStatus = Literal["sufficient", "insufficient"]


def _normalize_required_string(value, *, field_name):
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空或只包含空白字符")
    return normalized


def _normalize_optional_string(value):
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


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


class EvidenceRequest(BaseModel):
    """上层 Agent 发给中立证据 Agent 的统一请求协议。"""

    request_id: str
    requester: str
    mode: RequestMode

    # stance_research 模式使用
    topic: str | None = None
    direction: ResearchDirection | None = None
    research_focus: list[str] = Field(default_factory=list)
    context_evidence_ids: list[str] = Field(default_factory=list)

    # verify_evidence 模式使用
    evidence_ids: list[str] = Field(default_factory=list)
    verification_goal: str | None = None

    max_deepening_steps: int = Field(default=2, ge=0, le=4)

    @field_validator("request_id", "requester")
    @classmethod
    def normalize_required_strings(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("topic", "verification_goal")
    @classmethod
    def normalize_optional_strings(cls, value):
        return _normalize_optional_string(value)

    @field_validator(
        "research_focus",
        "context_evidence_ids",
        "evidence_ids",
    )
    @classmethod
    def normalize_string_lists(cls, values, info):
        return _normalize_string_list(
            values,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def validate_mode_fields(self):
        """校验两种调查模式允许和要求出现的字段组合。"""
        if self.mode == "stance_research":
            if not self.topic:
                raise ValueError("stance_research 的 topic 必须存在")
            if not self.direction:
                raise ValueError("stance_research 的 direction 必须存在")
            if self.evidence_ids:
                raise ValueError(
                    "stance_research 的 evidence_ids 必须为空"
                )
            if self.verification_goal is not None:
                raise ValueError(
                    "stance_research 的 verification_goal 必须为 None"
                )

        elif self.mode == "verify_evidence":
            if not self.evidence_ids:
                raise ValueError(
                    "verify_evidence 的 evidence_ids 不能为空"
                )
            if not self.verification_goal:
                raise ValueError(
                    "verify_evidence 的 verification_goal 必须存在"
                )
            if self.topic is not None:
                raise ValueError("verify_evidence 的 topic 必须为空")
            if self.direction is not None:
                raise ValueError("verify_evidence 的 direction 必须为空")
            if self.research_focus:
                raise ValueError(
                    "verify_evidence 的 research_focus 必须为空"
                )
            if self.context_evidence_ids:
                raise ValueError(
                    "verify_evidence 的 context_evidence_ids 必须为空"
                )

        return self


class EvidenceBundle(BaseModel):
    """证据 Agent 返回给上层 Agent 的中立证据包。"""

    request_id: str
    mode: RequestMode
    status: BundleStatus
    evidence: list[dict] = Field(default_factory=list)
    source_checks: list[dict] = Field(default_factory=list)
    inconsistencies: list[dict] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    investigation_trace: list[dict] = Field(default_factory=list)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value):
        return _normalize_required_string(
            value,
            field_name="request_id",
        )

    @field_validator("missing_information")
    @classmethod
    def normalize_missing_information(cls, values):
        return _normalize_string_list(
            values,
            field_name="missing_information",
        )

    @model_validator(mode="after")
    def validate_result_semantics(self):
        if self.status == "sufficient" and not self.evidence:
            raise ValueError(
                "status 为 sufficient 时 evidence 不能为空"
            )
        if self.status == "insufficient" and not self.missing_information:
            raise ValueError(
                "status 为 insufficient 时必须说明 missing_information"
            )
        return self
