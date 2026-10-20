import json
import sqlite3

import pytest

from src.memory_store import SQLiteMemoryStore


def make_score(value=3):
    return {
        "evidence_quality": value,
        "reasoning_quality": value,
        "coverage": value,
        "rebuttal_quality": value,
        "uncertainty_handling": value,
    }


def test_persists_evidence_argument_reference_and_verdict(tmp_path):
    database_path = tmp_path / "memory.sqlite3"
    store = SQLiteMemoryStore(database_path)
    store.start_run(
        "run-1",
        topic="竞争优势是否持续",
        config={"max_rounds": 1},
    )
    request = {
        "request_id": "request-1",
        "requester": "support-agent",
        "mode": "stance_research",
        "topic": "竞争优势是否持续",
        "direction": "support",
    }
    bundle = {
        "request_id": "request-1",
        "mode": "stance_research",
        "status": "sufficient",
        "evidence": [{
            "evidence_id": "E1",
            "source_kind": "local",
            "doc_id": "report-1",
            "chunk_id": "report-1:p15:c0",
            "page_number": 15,
            "text": "历史材料中的市场份额数据",
        }],
        "investigation_trace": [{
            "action": "finish",
            "query": "历史市场份额",
            "reason": "已找到直接材料",
        }],
    }
    position = {
        "agent_id": "support-agent",
        "stance": "support",
        "round_number": 0,
        "arguments": [{
            "argument_id": "A1",
            "claim": "历史时期具有市场优势",
            "reasoning": "E1 提供了直接数据",
            "evidence_ids": ["E1"],
        }],
    }
    verdict = {
        "judge_id": "judge-agent",
        "verdict": "mixed",
        "support_score": make_score(),
        "oppose_score": make_score(),
        "summary": "历史优势成立，持续性仍需更多材料",
        "decisive_reasons": ["双方证据时间覆盖有限"],
    }

    store.start_evidence_request("run-1", request)
    store.complete_evidence_request("run-1", bundle)
    store.save_position("run-1", position)
    store.save_verdict("run-1", verdict)
    store.update_run("run-1", status="completed")
    summary = store.get_run_summary("run-1")
    store.close()

    assert summary["status"] == "completed"
    assert summary["counts"]["evidence"] == 1
    assert summary["counts"]["argument_versions"] == 1
    assert summary["counts"]["verdicts"] == 1

    connection = sqlite3.connect(database_path)
    reference = connection.execute(
        """
        SELECT argument_id, evidence_id
        FROM argument_evidence
        WHERE run_id = 'run-1'
        """
    ).fetchone()
    stored_bundle = connection.execute(
        "SELECT result_json FROM evidence_requests WHERE request_id = 'request-1'"
    ).fetchone()[0]
    connection.close()

    assert reference == ("A1", "E1")
    assert json.loads(stored_bundle)["status"] == "sufficient"


def test_latest_checkpoint_can_restore_last_durable_milestone(tmp_path):
    with SQLiteMemoryStore(tmp_path / "memory.sqlite3") as store:
        store.start_run("run-1", topic="辩题")
        store.save_checkpoint(
            "run-1",
            stage="position_ready",
            state={"round": 0, "argument_ids": ["A1"]},
        )
        store.save_checkpoint(
            "run-1",
            stage="position_updated",
            state={"round": 1, "argument_ids": ["A1"]},
        )

        checkpoint = store.load_latest_checkpoint("run-1")

    assert checkpoint["stage"] == "position_updated"
    assert checkpoint["state"]["round"] == 1


def test_same_evidence_id_cannot_silently_change_content(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.start_run("run-1", topic="辩题")
    request = {
        "request_id": "request-1",
        "requester": "support-agent",
        "mode": "stance_research",
        "topic": "辩题",
        "direction": "support",
    }
    store.start_evidence_request("run-1", request)
    store.complete_evidence_request("run-1", {
        "request_id": "request-1",
        "mode": "stance_research",
        "status": "sufficient",
        "evidence": [{"evidence_id": "E1", "text": "原始内容"}],
    })

    store.start_run("run-2", topic="另一辩题")
    changed_request = {**request, "request_id": "request-2"}
    store.start_evidence_request("run-2", changed_request)
    with pytest.raises(ValueError, match="对应了不同内容"):
        store.complete_evidence_request("run-2", {
            "request_id": "request-2",
            "mode": "stance_research",
            "status": "sufficient",
            "evidence": [{"evidence_id": "E1", "text": "被篡改内容"}],
        })
    store.close()


def test_events_are_ordered_and_run_summary_counts_them(tmp_path):
    with SQLiteMemoryStore(tmp_path / "memory.sqlite3") as store:
        store.start_run("run-1", topic="辩题")
        first = store.append_event("run-1", {"name": "debate_started"})
        second = store.append_event(
            "run-1",
            {"name": "round_started", "round_number": 1},
        )
        summary = store.get_run_summary("run-1")

    assert (first, second) == (1, 2)
    assert summary["counts"]["events"] == 2


def test_list_runs_returns_recent_run_metadata(tmp_path):
    with SQLiteMemoryStore(tmp_path / "memory.sqlite3") as store:
        store.start_run("run-1", topic="第一场")
        store.start_run("run-2", topic="第二场")

        runs = store.list_runs(limit=1)

    assert len(runs) == 1
    assert runs[0]["run_id"] in {"run-1", "run-2"}
    assert runs[0]["status"] == "running"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_list_runs_rejects_invalid_limit(tmp_path, limit):
    with SQLiteMemoryStore(tmp_path / "memory.sqlite3") as store:
        with pytest.raises(ValueError, match="正整数"):
            store.list_runs(limit=limit)
