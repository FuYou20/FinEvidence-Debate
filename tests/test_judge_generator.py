from types import SimpleNamespace
import json

import pytest

from errors import LLMJsonParseError
from src.judge_generator import (
    build_compact_debate_context,
    create_final_judge_generator,
    create_preliminary_judge_generator,
)
from src.judge_protocol import FinalVerdictDraft, PreliminaryJudgment


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.outputs.pop(0))


def make_client(*outputs):
    responses = FakeResponses(outputs)
    return SimpleNamespace(responses=responses), responses


def score(value):
    return {
        "evidence_quality": value,
        "reasoning_quality": value,
        "coverage": value,
        "rebuttal_quality": value,
        "uncertainty_handling": value,
    }


def verdict_draft():
    return {
        "verdict": "mixed",
        "support_score": score(3),
        "oppose_score": score(3),
        "argument_assessments": [],
        "decisive_reasons": ["双方均有部分有效证据"],
        "unresolved_issues": [],
        "summary": "当前论证质量接近",
    }


def test_preliminary_generator_uses_structured_output():
    client, responses = make_client(
        {
            "verdict_draft": verdict_draft(),
            "verification_requests": [
                {
                    "evidence_ids": ["E1"],
                    "verification_goal": "核实原始数据口径",
                    "reason": "会影响核心观点",
                }
            ],
        }
    )
    generate = create_preliminary_judge_generator(
        client,
        model_name="test-model",
    )

    result = generate(debate_result={"topic": "辩题"})

    assert result["verification_requests"][0]["evidence_ids"] == [
        "E1"
    ]
    assert responses.calls[0]["text_format"] is PreliminaryJudgment
    assert "status=active" in (
        responses.calls[0]["input"][0]["content"]
    )
    assert "辩题" in responses.calls[0]["input"][1]["content"]
    assert result["verdict_draft"]["verdict"] == "mixed"


def test_final_generator_returns_structured_verdict():
    client, responses = make_client(
        {
            "verdict": "support_preferred",
            "support_score": score(4),
            "oppose_score": score(2),
            "argument_assessments": [],
            "decisive_reasons": ["支持方证据更直接"],
            "unresolved_issues": [],
            "summary": "支持方当前论证质量更高",
        }
    )
    generate = create_final_judge_generator(
        client,
        model_name="test-model",
    )

    result = generate(
        debate_result={"topic": "辩题"},
        preliminary_judgment={},
        verification_results=[],
    )

    assert result["verdict"] == "support_preferred"
    assert responses.calls[0]["text_format"] is FinalVerdictDraft


def test_compact_context_deduplicates_repeated_evidence_text():
    evidence = {
        "evidence_id": "E1",
        "doc_id": "report-1",
        "chunk_id": "c1",
        "page_number": 1,
        "text": "同一段较长的证据正文",
    }
    position = {
        "agent_id": "support-agent",
        "stance": "support",
        "round_number": 0,
        "arguments": [],
    }
    debate = {
        "topic": "辩题",
        "supporting_position": position,
        "opposing_position": {**position, "stance": "oppose"},
        "initial_evidence": [
            {"request_id": "R1", "mode": "stance_research", "status": "sufficient", "evidence": [evidence]},
            {"request_id": "R2", "mode": "stance_research", "status": "sufficient", "evidence": [evidence]},
        ],
        "rounds": [],
        "stop_reason": "no_new_challenges",
    }

    compact = build_compact_debate_context(debate)

    assert len(compact["evidence_library"]) == 1
    assert compact["initial_evidence"][0]["evidence_ids"] == ["E1"]
    assert json.dumps(compact, ensure_ascii=False).count("同一段较长的证据正文") == 1


def test_compact_context_summarizes_old_rounds_and_keeps_recent_detail():
    def make_round(number, reason):
        return {
            "round_number": number,
            "supporting_challenges": [
                {
                    "challenge_id": f"C{number}",
                    "challenge_type": "claim",
                    "target_argument_id": "A1",
                    "target_evidence_ids": [],
                    "reason": reason,
                    "investigation_question": "调查问题",
                }
            ],
            "opposing_challenges": [],
            "supporting_updates": [],
            "opposing_updates": [],
            "supporting_investigations": [],
            "opposing_investigations": [],
        }

    compact = build_compact_debate_context(
        {
            "topic": "辩题",
            "supporting_position": {"arguments": []},
            "opposing_position": {"arguments": []},
            "rounds": [
                make_round(1, "旧轮次不应重复发送的长篇理由"),
                make_round(2, "最近轮次需要保留的理由"),
            ],
        },
        recent_rounds=1,
    )

    payload = json.dumps(compact, ensure_ascii=False)
    assert compact["earlier_round_summaries"][0]["round_number"] == 1
    assert compact["recent_rounds"][0]["round_number"] == 2
    assert "C1" in payload
    assert "旧轮次不应重复发送的长篇理由" not in payload
    assert "最近轮次需要保留的理由" in payload


def test_compact_context_prioritizes_active_argument_evidence():
    evidence = [
        {
            "evidence_id": evidence_id,
            "doc_id": "report-1",
            "chunk_id": evidence_id,
            "page_number": 1,
            "text": evidence_id * 50,
        }
        for evidence_id in ("E1", "E2", "E3")
    ]
    position = {
        "agent_id": "support-agent",
        "stance": "support",
        "round_number": 1,
        "arguments": [
            {
                "argument_id": "A1",
                "status": "active",
                "evidence_ids": ["E3"],
            }
        ],
    }

    compact = build_compact_debate_context(
        {
            "topic": "辩题",
            "supporting_position": position,
            "opposing_position": {"arguments": []},
            "initial_evidence": [{"evidence": evidence}],
            "rounds": [],
        },
        max_evidence_items=1,
        max_evidence_text_chars=100,
    )

    assert [
        item["evidence_id"] for item in compact["evidence_library"]
    ] == ["E3"]


@pytest.mark.parametrize(
    "factory, call_kwargs",
    [
        (
            create_preliminary_judge_generator,
            {"debate_result": {}},
        ),
        (
            create_final_judge_generator,
            {
                "debate_result": {},
                "preliminary_judgment": {},
                "verification_results": [],
            },
        ),
    ],
)
def test_judge_generators_reject_missing_parsed_output(
    factory,
    call_kwargs,
):
    client, _responses = make_client(None)
    generate = factory(client, model_name="test-model")

    with pytest.raises(LLMJsonParseError):
        generate(**call_kwargs)
