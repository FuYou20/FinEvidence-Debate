from copy import deepcopy

from errors import InvalidCitationError

from .debate_protocol import (
    ArgumentDraft,
    ArgumentUpdateDraft,
    ChallengeDraft,
    ChallengeInvestigation,
    DebateArgument,
    DebateChallenge,
    DebatePosition,
    InitialPositionResult,
)
from .evidence_protocol import EvidenceBundle, EvidenceRequest


def _collect_evidence_ids(evidence):
    evidence_ids = set()
    for position, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            raise TypeError(f"第 {position} 条证据必须是 dict")
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError(
                f"第 {position} 条证据缺少非空 evidence_id"
            )
        evidence_ids.add(evidence_id.strip())
    return evidence_ids


def _validate_citations(evidence_ids, allowed_ids, *, owner):
    unknown_ids = sorted(set(evidence_ids) - set(allowed_ids))
    if unknown_ids:
        raise InvalidCitationError(
            f"{owner} 引用了不存在的证据: {unknown_ids}"
        )


class ResearchDebateAgent:
    """兼具研究与辩论能力的单方 Agent。

    证据获取全部委托给中立证据 Agent。本类只负责形成观点、质疑
    对方观点或证据，以及依据查证结果保留、修正或撤回观点。
    """

    def __init__(
        self,
        *,
        agent_id,
        stance,
        request_evidence,
        generate_arguments,
        generate_challenges,
        generate_updates,
    ):
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id 必须是非空字符串")
        if stance not in {"support", "oppose"}:
            raise ValueError("stance 必须是 support 或 oppose")
        for name, dependency in (
            ("request_evidence", request_evidence),
            ("generate_arguments", generate_arguments),
            ("generate_challenges", generate_challenges),
            ("generate_updates", generate_updates),
        ):
            if not callable(dependency):
                raise TypeError(f"{name} 必须是可调用对象")

        self.agent_id = agent_id.strip()
        self.stance = stance
        self.request_evidence = request_evidence
        self.generate_arguments = generate_arguments
        self.generate_challenges = generate_challenges
        self.generate_updates = generate_updates
        self._request_sequence = 0
        self._argument_sequence = 0
        self._challenge_sequence = 0

    def _next_request_id(self, purpose):
        self._request_sequence += 1
        return f"{self.agent_id}:request:{self._request_sequence}:{purpose}"

    def _next_argument_id(self):
        self._argument_sequence += 1
        return f"{self.agent_id}:argument:{self._argument_sequence}"

    def _next_challenge_id(self):
        self._challenge_sequence += 1
        return f"{self.agent_id}:challenge:{self._challenge_sequence}"

    def _request_evidence_bundle(self, request):
        validated_request = EvidenceRequest.model_validate(request)
        raw_bundle = self.request_evidence(request=validated_request)
        bundle = EvidenceBundle.model_validate(raw_bundle)
        if bundle.request_id != validated_request.request_id:
            raise ValueError("证据包 request_id 与请求不一致")
        if bundle.mode != validated_request.mode:
            raise ValueError("证据包 mode 与请求不一致")
        return bundle

    def build_initial_position(self, topic, *, research_focus=None):
        """调查本方立场并生成带证据引用的初始观点。"""
        request = EvidenceRequest(
            request_id=self._next_request_id("initial"),
            requester=self.agent_id,
            mode="stance_research",
            topic=topic,
            direction=self.stance,
            research_focus=research_focus or [],
        )
        bundle = self._request_evidence_bundle(request)

        arguments = []
        if bundle.evidence:
            raw_drafts = self.generate_arguments(
                topic=request.topic,
                stance=self.stance,
                evidence=deepcopy(bundle.evidence),
                missing_information=deepcopy(
                    bundle.missing_information
                ),
            )
            if not isinstance(raw_drafts, list):
                raise TypeError("generate_arguments 必须返回 list")

            allowed_ids = _collect_evidence_ids(bundle.evidence)
            for raw_draft in raw_drafts:
                draft = ArgumentDraft.model_validate(raw_draft)
                _validate_citations(
                    draft.evidence_ids,
                    allowed_ids,
                    owner="初始观点",
                )
                arguments.append(
                    DebateArgument(
                        argument_id=self._next_argument_id(),
                        claim=draft.claim,
                        reasoning=draft.reasoning,
                        evidence_ids=draft.evidence_ids,
                    )
                )

        return InitialPositionResult(
            position=DebatePosition(
                agent_id=self.agent_id,
                stance=self.stance,
                round_number=0,
                arguments=arguments,
            ),
            evidence_bundle=bundle,
        )

    def create_challenges(
        self,
        opponent_position,
        *,
        round_number,
        max_challenges,
    ):
        """分析对方观点，产生观点质疑或证据质疑。"""
        position = DebatePosition.model_validate(opponent_position)
        if position.stance == self.stance:
            raise ValueError("只能质疑对方立场的 position")
        if not isinstance(max_challenges, int) or max_challenges < 0:
            raise ValueError("max_challenges 必须是非负整数")
        if max_challenges == 0 or not position.arguments:
            return []

        raw_drafts = self.generate_challenges(
            challenger_id=self.agent_id,
            challenger_stance=self.stance,
            opponent_position=position.model_dump(),
            max_challenges=max_challenges,
        )
        if not isinstance(raw_drafts, list):
            raise TypeError("generate_challenges 必须返回 list")

        argument_by_id = {
            argument.argument_id: argument
            for argument in position.arguments
        }
        challenges = []
        for raw_draft in raw_drafts[:max_challenges]:
            draft = ChallengeDraft.model_validate(raw_draft)
            target = argument_by_id.get(draft.target_argument_id)
            if target is None:
                raise ValueError(
                    "质疑引用了不存在的 target_argument_id: "
                    f"{draft.target_argument_id}"
                )
            _validate_citations(
                draft.target_evidence_ids,
                target.evidence_ids,
                owner="证据质疑",
            )
            target_evidence_ids = draft.target_evidence_ids
            if draft.challenge_type == "claim" and not target_evidence_ids:
                target_evidence_ids = list(target.evidence_ids)
            challenges.append(
                DebateChallenge(
                    challenge_id=self._next_challenge_id(),
                    challenger_id=self.agent_id,
                    round_number=round_number,
                    **{
                        **draft.model_dump(),
                        "target_evidence_ids": target_evidence_ids,
                    },
                )
            )
        return challenges

    def investigate_challenges(self, challenges):
        """按照质疑类型向中立证据 Agent 发起不同请求。"""
        investigations = []
        for raw_challenge in challenges:
            challenge = DebateChallenge.model_validate(raw_challenge)
            if challenge.challenger_id != self.agent_id:
                raise ValueError("只能调查由当前 Agent 发起的质疑")

            if challenge.challenge_type == "claim":
                request = EvidenceRequest(
                    request_id=self._next_request_id("claim-challenge"),
                    requester=self.agent_id,
                    mode="stance_research",
                    topic=challenge.investigation_question,
                    direction=self.stance,
                    research_focus=[challenge.reason],
                    context_evidence_ids=challenge.target_evidence_ids,
                )
            else:
                request = EvidenceRequest(
                    request_id=self._next_request_id(
                        "evidence-challenge"
                    ),
                    requester=self.agent_id,
                    mode="verify_evidence",
                    evidence_ids=challenge.target_evidence_ids,
                    verification_goal=challenge.investigation_question,
                )

            investigations.append(
                ChallengeInvestigation(
                    challenge=challenge,
                    evidence_bundle=self._request_evidence_bundle(request),
                )
            )
        return investigations

    def respond_to_challenges(
        self,
        current_position,
        investigations,
        *,
        round_number,
    ):
        """根据质疑及查证材料保留、修正或撤回观点。"""
        position = DebatePosition.model_validate(current_position)
        if position.agent_id != self.agent_id:
            raise ValueError("current_position 不属于当前 Agent")

        validated_investigations = [
            ChallengeInvestigation.model_validate(item)
            for item in investigations
        ]
        if not validated_investigations:
            return DebatePosition(
                **{
                    **position.model_dump(),
                    "round_number": round_number,
                }
            ), []

        raw_updates = self.generate_updates(
            agent_id=self.agent_id,
            stance=self.stance,
            current_position=position.model_dump(),
            investigations=[
                item.model_dump() for item in validated_investigations
            ],
        )
        if not isinstance(raw_updates, list):
            raise TypeError("generate_updates 必须返回 list")

        argument_by_id = {
            argument.argument_id: argument
            for argument in position.arguments
        }
        challenge_by_id = {
            item.challenge.challenge_id: item.challenge
            for item in validated_investigations
        }
        allowed_evidence_ids = {
            evidence_id
            for argument in position.arguments
            for evidence_id in argument.evidence_ids
        }
        for item in validated_investigations:
            allowed_evidence_ids.update(
                _collect_evidence_ids(item.evidence_bundle.evidence)
            )

        updates = [
            ArgumentUpdateDraft.model_validate(item)
            for item in raw_updates
        ]
        updated_argument_ids = [update.argument_id for update in updates]
        if len(updated_argument_ids) != len(set(updated_argument_ids)):
            raise ValueError("同一轮不能重复更新同一个 argument_id")

        for update in updates:
            if update.argument_id not in argument_by_id:
                raise ValueError(
                    "更新引用了不存在的 argument_id: "
                    f"{update.argument_id}"
                )
            for challenge_id in update.response_to_challenge_ids:
                challenge = challenge_by_id.get(challenge_id)
                if challenge is None:
                    raise ValueError(
                        "更新回应了不存在的 challenge_id: "
                        f"{challenge_id}"
                    )
                if challenge.target_argument_id != update.argument_id:
                    raise ValueError(
                        "更新回应的质疑不属于该 argument_id"
                    )
            _validate_citations(
                update.evidence_ids,
                allowed_evidence_ids,
                owner="观点更新",
            )

        update_by_argument_id = {
            update.argument_id: update for update in updates
        }
        next_arguments = []
        for argument in position.arguments:
            update = update_by_argument_id.get(argument.argument_id)
            if update is None:
                next_arguments.append(argument.model_copy(deep=True))
                continue

            if update.action == "withdraw":
                claim = argument.claim
                reasoning = argument.reasoning
                evidence_ids = argument.evidence_ids
                status = "withdrawn"
            elif update.action == "revise":
                claim = update.revised_claim
                reasoning = update.revised_reasoning
                evidence_ids = update.evidence_ids
                status = "active"
            else:
                claim = argument.claim
                reasoning = argument.reasoning
                evidence_ids = update.evidence_ids
                status = "active"

            next_arguments.append(
                DebateArgument(
                    argument_id=argument.argument_id,
                    claim=claim,
                    reasoning=reasoning,
                    evidence_ids=evidence_ids,
                    version=argument.version + 1,
                    status=status,
                    last_action=update.action,
                    revision_reason=update.reason,
                    response_to_challenge_ids=(
                        update.response_to_challenge_ids
                    ),
                )
            )

        return DebatePosition(
            agent_id=self.agent_id,
            stance=self.stance,
            round_number=round_number,
            arguments=next_arguments,
        ), updates


def create_research_debate_agent(
    client,
    *,
    model_name,
    agent_id,
    stance,
    request_evidence,
):
    """使用同一个模型客户端组装一个可运行的研究辩论 Agent。"""
    from .debate_generators import (
        create_argument_generator,
        create_challenge_generator,
        create_update_generator,
    )

    return ResearchDebateAgent(
        agent_id=agent_id,
        stance=stance,
        request_evidence=request_evidence,
        generate_arguments=create_argument_generator(
            client,
            model_name=model_name,
        ),
        generate_challenges=create_challenge_generator(
            client,
            model_name=model_name,
        ),
        generate_updates=create_update_generator(
            client,
            model_name=model_name,
        ),
    )
