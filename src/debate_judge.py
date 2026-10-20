from copy import deepcopy

from .debate_protocol import DebateResult
from .debate_events import emit_event, validate_observer
from .evidence_protocol import EvidenceBundle, EvidenceRequest
from .judge_generator import (
    create_final_judge_generator,
    create_preliminary_judge_generator,
)
from .judge_protocol import (
    FinalVerdictDraft,
    JudgeVerdict,
    PreliminaryJudgment,
)


class DebateJudge:
    """辩论后的中立裁判。

    裁判先评价最终论点，再把少量决定性的真实性疑问交给证据
    Agent 复核，最后根据辩论记录和复核结果输出结构化裁决。
    """

    def __init__(
        self,
        *,
        judge_id,
        request_evidence,
        generate_preliminary_judgment,
        generate_final_verdict,
    ):
        if not isinstance(judge_id, str) or not judge_id.strip():
            raise ValueError("judge_id 必须是非空字符串")
        for name, dependency in (
            ("request_evidence", request_evidence),
            (
                "generate_preliminary_judgment",
                generate_preliminary_judgment,
            ),
            ("generate_final_verdict", generate_final_verdict),
        ):
            if not callable(dependency):
                raise TypeError(f"{name} 必须是可调用对象")

        self.judge_id = judge_id.strip()
        self.request_evidence = request_evidence
        self.generate_preliminary_judgment = (
            generate_preliminary_judgment
        )
        self.generate_final_verdict = generate_final_verdict
        self._request_sequence = 0

    def _next_request_id(self):
        self._request_sequence += 1
        return f"{self.judge_id}:verification:{self._request_sequence}"

    def _verify(self, verification_request):
        request = EvidenceRequest(
            request_id=self._next_request_id(),
            requester=self.judge_id,
            mode="verify_evidence",
            evidence_ids=verification_request.evidence_ids,
            verification_goal=verification_request.verification_goal,
        )
        raw_bundle = self.request_evidence(request=request)
        bundle = EvidenceBundle.model_validate(raw_bundle)
        if bundle.request_id != request.request_id:
            raise ValueError("证据包 request_id 与裁判请求不一致")
        if bundle.mode != request.mode:
            raise ValueError("证据包 mode 与裁判请求不一致")
        return bundle

    def judge(self, debate_result, *, max_verification_requests=2, on_event=None):
        """评价一场已结束的辩论并返回最终裁决。"""
        result = DebateResult.model_validate(debate_result)
        validate_observer(on_event)
        if (
            not isinstance(max_verification_requests, int)
            or isinstance(max_verification_requests, bool)
            or max_verification_requests < 0
        ):
            raise ValueError(
                "max_verification_requests 必须是非负整数"
            )

        emit_event(on_event, "stage_started", label="裁判初审：检查双方论证与待复核证据")
        raw_preliminary = self.generate_preliminary_judgment(
            debate_result=result.model_dump(),
        )
        preliminary = PreliminaryJudgment.model_validate(
            raw_preliminary
        )
        emit_event(on_event, "judge_review_ready", requested=len(preliminary.verification_requests),
                   allowed=max_verification_requests)

        verification_results = [
            self._verify(verification_request)
            for verification_request in (
                preliminary.verification_requests[
                    :max_verification_requests
                ]
            )
        ]

        if verification_results:
            emit_event(on_event, "stage_started", label="裁判终审：结合新增复核结果修正裁决")
            raw_final = self.generate_final_verdict(
                debate_result=result.model_dump(),
                preliminary_judgment=preliminary.model_dump(),
                verification_results=[
                    bundle.model_dump()
                    for bundle in verification_results
                ],
            )
            final_draft = FinalVerdictDraft.model_validate(raw_final)
        else:
            emit_event(on_event, "stage_started", label="裁判初审已形成裁决：无需额外模型调用")
            final_draft = preliminary.verdict_draft
        verdict = JudgeVerdict(
            judge_id=self.judge_id,
            verification_results=deepcopy(verification_results),
            **final_draft.model_dump(),
        )
        emit_event(on_event, "verdict_ready", verdict=verdict.model_dump())
        return verdict


def create_debate_judge(
    client,
    *,
    model_name,
    request_evidence,
    judge_id="judge-agent",
):
    """用 Responses API 结构化生成器创建裁判 Agent。"""
    return DebateJudge(
        judge_id=judge_id,
        request_evidence=request_evidence,
        generate_preliminary_judgment=(
            create_preliminary_judge_generator(
                client,
                model_name=model_name,
            )
        ),
        generate_final_verdict=create_final_judge_generator(
            client,
            model_name=model_name,
        ),
    )
