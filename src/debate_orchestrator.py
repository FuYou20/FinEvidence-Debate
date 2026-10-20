from .debate_agent import ResearchDebateAgent
from .debate_events import emit_event, validate_observer
from .debate_protocol import (
    DebateConfig,
    DebateResult,
    DebateRoundRecord,
)


def run_debate(
    topic,
    supporting_agent,
    opposing_agent,
    *,
    config=None,
    supporting_focus=None,
    opposing_focus=None,
    on_event=None,
):
    """运行“初始立论 + 有界多轮交叉质疑”的辩论工作流。"""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic 必须是非空字符串")
    if not isinstance(supporting_agent, ResearchDebateAgent):
        raise TypeError("supporting_agent 必须是 ResearchDebateAgent")
    if not isinstance(opposing_agent, ResearchDebateAgent):
        raise TypeError("opposing_agent 必须是 ResearchDebateAgent")
    if supporting_agent.stance != "support":
        raise ValueError("supporting_agent 的 stance 必须是 support")
    if opposing_agent.stance != "oppose":
        raise ValueError("opposing_agent 的 stance 必须是 oppose")
    if supporting_agent.agent_id == opposing_agent.agent_id:
        raise ValueError("正反双方的 agent_id 不能相同")

    normalized_topic = topic.strip()
    validated_config = DebateConfig.model_validate(config or {})
    validate_observer(on_event)
    emit_event(on_event, "debate_started", topic=normalized_topic,
               config=validated_config.model_dump())

    emit_event(on_event, "stage_started", label="正方搜集证据并立论")
    supporting_initial = supporting_agent.build_initial_position(
        normalized_topic,
        research_focus=supporting_focus,
    )
    emit_event(on_event, "position_ready", position=supporting_initial.position.model_dump())
    emit_event(on_event, "stage_started", label="反方搜集证据并立论")
    opposing_initial = opposing_agent.build_initial_position(
        normalized_topic,
        research_focus=opposing_focus,
    )
    emit_event(on_event, "position_ready", position=opposing_initial.position.model_dump())
    supporting_position = supporting_initial.position
    opposing_position = opposing_initial.position
    rounds = []
    stop_reason = "max_rounds"

    for round_number in range(1, validated_config.max_rounds + 1):
        emit_event(on_event, "round_started", round_number=round_number)
        emit_event(on_event, "stage_started", label="正方审查反方观点")
        supporting_challenges = supporting_agent.create_challenges(
            opposing_position,
            round_number=round_number,
            max_challenges=(
                validated_config.max_challenges_per_agent_per_round
            ),
        )
        emit_event(on_event, "challenges_ready", stance="support",
                   challenges=[item.model_dump() for item in supporting_challenges])
        emit_event(on_event, "stage_started", label="反方审查正方观点")
        opposing_challenges = opposing_agent.create_challenges(
            supporting_position,
            round_number=round_number,
            max_challenges=(
                validated_config.max_challenges_per_agent_per_round
            ),
        )
        emit_event(on_event, "challenges_ready", stance="oppose",
                   challenges=[item.model_dump() for item in opposing_challenges])

        if (
            validated_config.stop_when_no_challenges
            and not supporting_challenges
            and not opposing_challenges
        ):
            stop_reason = "no_new_challenges"
            break

        emit_event(on_event, "stage_started", label="调查正方提出的质疑")
        supporting_investigations = (
            supporting_agent.investigate_challenges(
                supporting_challenges
            )
        )
        emit_event(on_event, "stage_started", label="调查反方提出的质疑")
        opposing_investigations = opposing_agent.investigate_challenges(
            opposing_challenges
        )

        emit_event(on_event, "stage_started", label="正方根据调查保留、修正或撤回观点")
        supporting_position, supporting_updates = (
            supporting_agent.respond_to_challenges(
                supporting_position,
                opposing_investigations,
                round_number=round_number,
            )
        )
        emit_event(on_event, "updates_ready", position=supporting_position.model_dump(),
                   updates=[item.model_dump() for item in supporting_updates])
        emit_event(on_event, "stage_started", label="反方根据调查保留、修正或撤回观点")
        opposing_position, opposing_updates = (
            opposing_agent.respond_to_challenges(
                opposing_position,
                supporting_investigations,
                round_number=round_number,
            )
        )
        emit_event(on_event, "updates_ready", position=opposing_position.model_dump(),
                   updates=[item.model_dump() for item in opposing_updates])

        rounds.append(
            DebateRoundRecord(
                round_number=round_number,
                supporting_challenges=supporting_challenges,
                opposing_challenges=opposing_challenges,
                supporting_investigations=supporting_investigations,
                opposing_investigations=opposing_investigations,
                supporting_updates=supporting_updates,
                opposing_updates=opposing_updates,
            )
        )

    result = DebateResult(
        topic=normalized_topic,
        initial_supporting_position=(
            supporting_initial.position.model_copy(deep=True)
        ),
        initial_opposing_position=(
            opposing_initial.position.model_copy(deep=True)
        ),
        supporting_position=supporting_position,
        opposing_position=opposing_position,
        initial_evidence=[
            supporting_initial.evidence_bundle,
            opposing_initial.evidence_bundle,
        ],
        rounds=rounds,
        stop_reason=stop_reason,
    )
    emit_event(on_event, "debate_completed", stop_reason=stop_reason, rounds=len(rounds))
    return result
