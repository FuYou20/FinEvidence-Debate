from types import SimpleNamespace

import pytest

from errors import LLMJsonParseError
from src.debate_generators import (
    create_argument_generator,
    create_challenge_generator,
    create_update_generator,
)
from src.debate_protocol import (
    ArgumentDraftBatch,
    ArgumentUpdateDraftBatch,
    ChallengeDraftBatch,
)


class FakeResponses:
    def __init__(self, output_parsed):
        self.output_parsed = output_parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.output_parsed)


def make_client(output_parsed):
    responses = FakeResponses(output_parsed)
    return SimpleNamespace(responses=responses), responses


def test_argument_generator_uses_structured_output_and_returns_drafts():
    client, responses = make_client(
        {
            "arguments": [
                {
                    "claim": "本土化形成竞争壁垒",
                    "reasoning": "长期渠道提高复制成本",
                    "evidence_ids": ["E1"],
                }
            ]
        }
    )
    generate = create_argument_generator(client, model_name="test-model")

    result = generate(
        topic="辩题",
        stance="support",
        evidence=[{"evidence_id": "E1", "text": "材料"}],
        missing_information=[],
    )

    assert result[0]["evidence_ids"] == ["E1"]
    assert responses.calls[0]["text_format"] is ArgumentDraftBatch
    assert "本土化形成竞争壁垒" not in (
        responses.calls[0]["input"][0]["content"]
    )
    assert "E1" in responses.calls[0]["input"][1]["content"]


def test_argument_generator_sends_bounded_evidence_projection():
    client, responses = make_client({"arguments": []})
    generate = create_argument_generator(
        client,
        model_name="test-model",
        max_evidence_items=1,
        max_evidence_text_chars=60,
        max_chars_per_evidence=60,
    )

    generate(
        topic="辩题",
        stance="support",
        evidence=[
            {"evidence_id": "E1", "text": "甲" * 300},
            {"evidence_id": "E2", "text": "乙" * 300},
        ],
        missing_information=[],
    )

    prompt = responses.calls[0]["input"][1]["content"]
    assert "E1" in prompt
    assert "E2" not in prompt
    assert "正文已压缩" in prompt


def test_challenge_generator_limits_number_of_model_challenges():
    challenge = {
        "challenge_type": "claim",
        "target_argument_id": "A1",
        "reason": "推理存在跳跃",
        "investigation_question": "调查替代解释",
    }
    client, responses = make_client(
        {"challenges": [challenge, challenge, challenge]}
    )
    generate = create_challenge_generator(
        client,
        model_name="test-model",
    )

    result = generate(
        challenger_id="oppose-agent",
        challenger_stance="oppose",
        opponent_position={"arguments": []},
        max_challenges=2,
    )

    assert len(result) == 2
    assert responses.calls[0]["text_format"] is ChallengeDraftBatch


def test_update_generator_returns_action_drafts():
    client, responses = make_client(
        {
            "updates": [
                {
                    "argument_id": "A1",
                    "action": "withdraw",
                    "reason": "核心证据无法追溯",
                    "evidence_ids": [],
                    "response_to_challenge_ids": ["C1"],
                }
            ]
        }
    )
    generate = create_update_generator(client, model_name="test-model")

    result = generate(
        agent_id="support-agent",
        stance="support",
        current_position={"arguments": []},
        investigations=[],
    )

    assert result[0]["action"] == "withdraw"
    assert responses.calls[0]["text_format"] is ArgumentUpdateDraftBatch


@pytest.mark.parametrize(
    "factory, call_kwargs",
    [
        (
            create_argument_generator,
            {
                "topic": "辩题",
                "stance": "support",
                "evidence": [],
                "missing_information": [],
            },
        ),
        (
            create_challenge_generator,
            {
                "challenger_id": "agent",
                "challenger_stance": "oppose",
                "opponent_position": {},
                "max_challenges": 1,
            },
        ),
        (
            create_update_generator,
            {
                "agent_id": "agent",
                "stance": "support",
                "current_position": {},
                "investigations": [],
            },
        ),
    ],
)
def test_generators_reject_missing_parsed_output(factory, call_kwargs):
    client, _responses = make_client(None)
    generate = factory(client, model_name="test-model")

    with pytest.raises(LLMJsonParseError):
        generate(**call_kwargs)
