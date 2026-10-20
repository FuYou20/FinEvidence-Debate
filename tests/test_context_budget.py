from copy import deepcopy

from src.context_budget import (
    compact_evidence_records,
    compact_position,
    truncate_text,
)


def evidence(evidence_id, text):
    return {
        "evidence_id": evidence_id,
        "doc_id": "report-1",
        "chunk_id": f"chunk-{evidence_id}",
        "page_number": 1,
        "text": text,
        "embedding": [0.1] * 100,
    }


def test_truncate_text_keeps_head_and_tail():
    text = "开头" + "中" * 200 + "结尾"

    result = truncate_text(text, 60)

    assert len(result) <= 60
    assert result.startswith("开头")
    assert result.endswith("结尾")
    assert "正文已压缩" in result


def test_compact_evidence_prioritizes_cited_ids_and_respects_budget():
    records = [
        evidence("E1", "甲" * 200),
        evidence("E2", "乙" * 200),
        evidence("E3", "丙" * 200),
    ]
    original = deepcopy(records)

    result = compact_evidence_records(
        records,
        priority_ids=["E3"],
        max_items=2,
        max_total_text_chars=100,
        max_text_chars_per_item=80,
    )

    assert [item["evidence_id"] for item in result] == ["E3", "E1"]
    assert sum(len(item["text"]) for item in result) <= 100
    assert all("embedding" not in item for item in result)
    assert all(item["text_truncated"] is True for item in result)
    assert records == original


def test_priority_ids_are_not_dropped_by_soft_item_cap():
    records = [
        evidence("E1", "甲" * 20),
        evidence("E2", "乙" * 20),
        evidence("E3", "丙" * 20),
    ]

    result = compact_evidence_records(
        records,
        priority_ids=["E2", "E3"],
        max_items=1,
        max_total_text_chars=100,
    )

    assert [item["evidence_id"] for item in result] == ["E2", "E3"]


def test_compact_position_keeps_active_state_not_withdrawn_prose():
    position = {
        "agent_id": "support-agent",
        "stance": "support",
        "round_number": 2,
        "arguments": [
            {
                "argument_id": "A1",
                "status": "active",
                "claim": "仍有效",
            },
            {
                "argument_id": "A2",
                "status": "withdrawn",
                "claim": "已经撤回且无需反复发送的长篇论证",
            },
        ],
    }

    result = compact_position(position, include_withdrawn=True)

    assert [item["argument_id"] for item in result["arguments"]] == ["A1"]
    assert result["withdrawn_argument_ids"] == ["A2"]
    assert "已经撤回" not in str(result)
