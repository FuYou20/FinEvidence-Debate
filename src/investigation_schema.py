from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


InvestigationAction = Literal[
    "finish",
    "rewrite_query",
    "multimodal",
    "web_search",
]
DeepeningAction = Literal[
    "rewrite_query",
    "multimodal",
    "web_search",
]
StopReason = Literal[
    "evidence_sufficient",
    "max_deepening_steps",
    "no_progress",
    "action_unavailable",
    "action_failed",
]


class EvidenceDecision(BaseModel):
    """证据判断器对下一步调查动作的结构化决策。"""

    enough: bool
    next_action: InvestigationAction
    reason: str
    next_query: str | None = None

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value):
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason 不能为空或只包含空白字符")
        return normalized

    @field_validator("next_query")
    @classmethod
    def normalize_next_query(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_action_matches_enough(self):
        if self.enough and self.next_action != "finish":
            raise ValueError(
                "enough 为 true 时，next_action 必须是 finish"
            )
        if not self.enough and self.next_action == "finish":
            raise ValueError(
                "enough 为 false 时，next_action 不能是 finish"
            )
        query_actions = {"rewrite_query", "web_search"}
        if self.next_action in query_actions and not self.next_query:
            raise ValueError(
                "rewrite_query 或 web_search 必须同时提供 next_query"
            )
        if self.next_action not in query_actions and self.next_query is not None:
            raise ValueError(
                "finish 或 multimodal 不应提供 next_query"
            )
        return self


class InvestigationStep(BaseModel):
    """一次实际执行的深入调查动作。"""

    step_number: int = Field(ge=1)
    action: DeepeningAction
    reason: str
    query_before: str
    query_after: str
    added_evidence_count: int = Field(ge=0)
    error_message: str | None = None
    retryable: bool | None = None


class InvestigationResult(BaseModel):
    """证据调查工作流的最终结果与审计轨迹。"""

    status: Literal["sufficient", "insufficient"]
    question: str
    final_query: str
    evidence: list[dict]
    steps: list[InvestigationStep]
    stop_reason: StopReason
    last_decision: EvidenceDecision

    @model_validator(mode="after")
    def validate_status_matches_stop_reason(self):
        if (
            self.status == "sufficient"
            and self.stop_reason != "evidence_sufficient"
        ):
            raise ValueError(
                "status 为 sufficient 时，stop_reason 必须是 "
                "evidence_sufficient"
            )
        if (
            self.status == "insufficient"
            and self.stop_reason == "evidence_sufficient"
        ):
            raise ValueError(
                "status 为 insufficient 时，stop_reason 不能是 "
                "evidence_sufficient"
            )
        return self
