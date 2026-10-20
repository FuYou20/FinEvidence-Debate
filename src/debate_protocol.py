from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .evidence_protocol import EvidenceBundle


DebateStance = Literal["support", "oppose"]
ChallengeType = Literal["claim", "evidence"]
ArgumentAction = Literal["maintain", "revise", "withdraw"]
ArgumentStatus = Literal["active", "withdrawn"]


def _normalize_required_string(value, *, field_name):
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
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


class ArgumentDraft(BaseModel):
    """生成器提出的观点草稿；ID 由业务代码统一分配。"""

    claim: str
    reasoning: str
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("claim", "reasoning")
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


class ArgumentDraftBatch(BaseModel):
    """结构化输出使用的观点草稿列表包装。"""

    arguments: list[ArgumentDraft] = Field(default_factory=list)


class DebateArgument(BaseModel):
    """辩论中的一条可追踪观点及其证据引用。"""

    argument_id: str
    claim: str
    reasoning: str
    evidence_ids: list[str] = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    status: ArgumentStatus = "active"
    last_action: Literal[
        "initial",
        "maintain",
        "revise",
        "withdraw",
    ] = "initial"
    revision_reason: str | None = None
    response_to_challenge_ids: list[str] = Field(default_factory=list)

    @field_validator("argument_id", "claim", "reasoning")
    @classmethod
    def normalize_required_strings(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("revision_reason")
    @classmethod
    def normalize_revision_reason(cls, value):
        return _normalize_optional_string(value)

    @field_validator("evidence_ids", "response_to_challenge_ids")
    @classmethod
    def normalize_string_lists(cls, values, info):
        return _normalize_string_list(
            values,
            field_name=info.field_name,
        )


class DebatePosition(BaseModel):
    """某一方在指定轮次结束后的全部观点。"""

    agent_id: str
    stance: DebateStance
    round_number: int = Field(ge=0)
    arguments: list[DebateArgument] = Field(default_factory=list)

    @field_validator("agent_id")
    @classmethod
    def normalize_agent_id(cls, value):
        return _normalize_required_string(value, field_name="agent_id")

    @model_validator(mode="after")
    def validate_unique_argument_ids(self):
        ids = [argument.argument_id for argument in self.arguments]
        if len(ids) != len(set(ids)):
            raise ValueError("arguments 中的 argument_id 不能重复")
        return self


class InitialPositionResult(BaseModel):
    """初始立论及其证据调查结果。"""

    position: DebatePosition
    evidence_bundle: EvidenceBundle


class ChallengeDraft(BaseModel):
    """生成器提出的质疑草稿；ID 由业务代码统一分配。"""

    challenge_type: ChallengeType
    target_argument_id: str
    target_evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    investigation_question: str

    @field_validator(
        "target_argument_id",
        "reason",
        "investigation_question",
    )
    @classmethod
    def normalize_required_strings(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("target_evidence_ids")
    @classmethod
    def normalize_target_evidence_ids(cls, values):
        return _normalize_string_list(
            values,
            field_name="target_evidence_ids",
        )

    @model_validator(mode="after")
    def validate_challenge_fields(self):
        if self.challenge_type == "evidence" and not self.target_evidence_ids:
            raise ValueError(
                "evidence 类型质疑必须提供 target_evidence_ids"
            )
        return self


class DebateChallenge(ChallengeDraft):
    """一条已经分配 ID 的观点或证据质疑。"""

    challenge_id: str
    challenger_id: str
    round_number: int = Field(ge=1)

    @field_validator("challenge_id", "challenger_id")
    @classmethod
    def normalize_identifiers(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )


class ChallengeDraftBatch(BaseModel):
    """结构化输出使用的质疑草稿列表包装。"""

    challenges: list[ChallengeDraft] = Field(default_factory=list)


class ChallengeInvestigation(BaseModel):
    """一条质疑以及为该质疑取得的中立证据包。"""

    challenge: DebateChallenge
    evidence_bundle: EvidenceBundle


class ArgumentUpdateDraft(BaseModel):
    """观点方对质疑作出的保留、修正或撤回决定。"""

    argument_id: str
    action: ArgumentAction
    reason: str
    revised_claim: str | None = None
    revised_reasoning: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    response_to_challenge_ids: list[str] = Field(min_length=1)

    @field_validator("argument_id", "reason")
    @classmethod
    def normalize_required_strings(cls, value, info):
        return _normalize_required_string(
            value,
            field_name=info.field_name,
        )

    @field_validator("revised_claim", "revised_reasoning")
    @classmethod
    def normalize_optional_strings(cls, value):
        return _normalize_optional_string(value)

    @field_validator("evidence_ids", "response_to_challenge_ids")
    @classmethod
    def normalize_string_lists(cls, values, info):
        return _normalize_string_list(
            values,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def validate_action_fields(self):
        if self.action == "revise":
            if not self.revised_claim or not self.revised_reasoning:
                raise ValueError(
                    "revise 必须提供 revised_claim 和 revised_reasoning"
                )
            if not self.evidence_ids:
                raise ValueError("revise 必须提供最终 evidence_ids")
        elif self.action == "maintain":
            if self.revised_claim is not None:
                raise ValueError("maintain 不能提供 revised_claim")
            if self.revised_reasoning is not None:
                raise ValueError("maintain 不能提供 revised_reasoning")
            if not self.evidence_ids:
                raise ValueError("maintain 必须提供最终 evidence_ids")
        else:
            if self.revised_claim is not None:
                raise ValueError("withdraw 不能提供 revised_claim")
            if self.revised_reasoning is not None:
                raise ValueError("withdraw 不能提供 revised_reasoning")
            if self.evidence_ids:
                raise ValueError("withdraw 不应提供新的 evidence_ids")
        return self


class ArgumentUpdateDraftBatch(BaseModel):
    """结构化输出使用的观点更新列表包装。"""

    updates: list[ArgumentUpdateDraft] = Field(default_factory=list)


class DebateRoundRecord(BaseModel):
    """一轮交叉质疑、调查和观点更新记录。"""

    round_number: int = Field(ge=1)
    supporting_challenges: list[DebateChallenge]
    opposing_challenges: list[DebateChallenge]
    supporting_investigations: list[ChallengeInvestigation]
    opposing_investigations: list[ChallengeInvestigation]
    supporting_updates: list[ArgumentUpdateDraft]
    opposing_updates: list[ArgumentUpdateDraft]


class DebateConfig(BaseModel):
    """多轮辩论的硬限制。"""

    max_rounds: int = Field(default=2, ge=1, le=5)
    max_challenges_per_agent_per_round: int = Field(
        default=2,
        ge=0,
        le=5,
    )
    stop_when_no_challenges: bool = True


class DebateResult(BaseModel):
    """多轮辩论结束后的状态和审计记录。"""

    topic: str
    initial_supporting_position: DebatePosition
    initial_opposing_position: DebatePosition
    supporting_position: DebatePosition
    opposing_position: DebatePosition
    initial_evidence: list[EvidenceBundle]
    rounds: list[DebateRoundRecord]
    stop_reason: Literal["max_rounds", "no_new_challenges"]

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value):
        return _normalize_required_string(value, field_name="topic")
