"""SQLite-backed durable memory for debate runs.

The live workflow keeps its frequently-changing working state in Python
objects.  This module projects durable milestones into SQLite so evidence,
argument evolution, challenges and verdicts survive process restarts.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock

from .debate_protocol import DebatePosition, DebateResult
from .evidence_protocol import EvidenceBundle, EvidenceRequest
from .judge_protocol import JudgeVerdict


SCHEMA_VERSION = 1


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _nonempty_string(value, *, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value.strip()


class SQLiteMemoryStore:
    """Durable run memory with normalized cross-Agent references.

    One instance owns one SQLite connection.  Calls are serialized with a
    lock, and every public write method commits atomically.
    """

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    def _initialize_schema(self):
        schema = """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS debate_runs (
            run_id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            status TEXT NOT NULL,
            config_json TEXT NOT NULL,
            output_path TEXT,
            current_stage TEXT,
            current_round INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS run_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            name TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS evidence_requests (
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            request_id TEXT NOT NULL,
            requester TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            result_status TEXT,
            error_type TEXT,
            request_json TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY(run_id, request_id)
        );

        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id TEXT PRIMARY KEY,
            source_kind TEXT,
            doc_id TEXT,
            chunk_id TEXT,
            page_number INTEGER,
            url TEXT,
            published_at TEXT,
            content_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS run_evidence (
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
            request_id TEXT,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY(run_id, evidence_id)
        );

        CREATE TABLE IF NOT EXISTS investigation_steps (
            run_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            action TEXT,
            query TEXT,
            reason TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(run_id, request_id, step_index),
            FOREIGN KEY(run_id, request_id)
                REFERENCES evidence_requests(run_id, request_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS arguments (
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            argument_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            agent_id TEXT NOT NULL,
            stance TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            last_action TEXT NOT NULL,
            claim TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            revision_reason TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(run_id, argument_id, version)
        );

        CREATE TABLE IF NOT EXISTS argument_evidence (
            run_id TEXT NOT NULL,
            argument_id TEXT NOT NULL,
            argument_version INTEGER NOT NULL,
            evidence_id TEXT NOT NULL,
            PRIMARY KEY(run_id, argument_id, argument_version, evidence_id),
            FOREIGN KEY(run_id, argument_id, argument_version)
                REFERENCES arguments(run_id, argument_id, version)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS challenges (
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            challenge_id TEXT NOT NULL,
            challenger_id TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            challenge_type TEXT NOT NULL,
            target_argument_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            investigation_question TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(run_id, challenge_id)
        );

        CREATE TABLE IF NOT EXISTS challenge_evidence (
            run_id TEXT NOT NULL,
            challenge_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            PRIMARY KEY(run_id, challenge_id, evidence_id),
            FOREIGN KEY(run_id, challenge_id)
                REFERENCES challenges(run_id, challenge_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS argument_updates (
            update_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            round_number INTEGER NOT NULL,
            stance TEXT NOT NULL,
            argument_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, round_number, stance, argument_id)
        );

        CREATE TABLE IF NOT EXISTS verdicts (
            run_id TEXT PRIMARY KEY REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            judge_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            summary TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES debate_runs(run_id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_run
            ON run_events(run_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_arguments_current
            ON arguments(run_id, argument_id, version DESC);
        CREATE INDEX IF NOT EXISTS idx_checkpoints_run
            ON checkpoints(run_id, checkpoint_id DESC);
        """
        with self._transaction() as connection:
            connection.executescript(schema)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self):
        with self._lock:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def start_run(self, run_id, *, topic, config=None, output_path=None):
        run_id = _nonempty_string(run_id, field_name="run_id")
        topic = _nonempty_string(topic, field_name="topic")
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO debate_runs(
                    run_id, topic, status, config_json, output_path,
                    created_at, updated_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    topic,
                    _json(config or {}),
                    str(output_path) if output_path is not None else None,
                    now,
                    now,
                ),
            )
        return run_id

    def update_run(
        self,
        run_id,
        *,
        status=None,
        current_stage=None,
        current_round=None,
        error_type=None,
    ):
        run_id = _nonempty_string(run_id, field_name="run_id")
        fields = ["updated_at = ?"]
        values = [_utc_now()]
        for column, value in (
            ("status", status),
            ("current_stage", current_stage),
            ("current_round", current_round),
            ("error_type", error_type),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if status in {"completed", "failed"}:
            fields.append("completed_at = ?")
            values.append(_utc_now())
        values.append(run_id)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"UPDATE debate_runs SET {', '.join(fields)} WHERE run_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"不存在的 run_id: {run_id}")

    def append_event(self, run_id, event):
        run_id = _nonempty_string(run_id, field_name="run_id")
        if not isinstance(event, dict):
            raise TypeError("event 必须是 dict")
        name = _nonempty_string(event.get("name"), field_name="event.name")
        payload = deepcopy(event)
        occurred_at = payload.pop("at", None) or _utc_now()
        with self._transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO run_events(
                    run_id, sequence, name, occurred_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, sequence, name, str(occurred_at), _json(payload)),
            )
        return sequence

    def start_evidence_request(self, run_id, request):
        request = EvidenceRequest.model_validate(request)
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO evidence_requests(
                    run_id, request_id, requester, mode, status,
                    request_json, created_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                ON CONFLICT(run_id, request_id) DO UPDATE SET
                    requester = excluded.requester,
                    mode = excluded.mode,
                    status = 'running',
                    request_json = excluded.request_json,
                    error_type = NULL,
                    completed_at = NULL
                """,
                (
                    run_id,
                    request.request_id,
                    request.requester,
                    request.mode,
                    _json(request),
                    now,
                ),
            )

    def complete_evidence_request(self, run_id, bundle):
        bundle = EvidenceBundle.model_validate(bundle)
        now = _utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE evidence_requests SET
                    status = 'completed', result_status = ?, result_json = ?,
                    error_type = NULL, completed_at = ?
                WHERE run_id = ? AND request_id = ?
                """,
                (
                    bundle.status,
                    _json(bundle),
                    now,
                    run_id,
                    bundle.request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(
                    "证据请求尚未登记: "
                    f"{run_id}/{bundle.request_id}"
                )
            for item in bundle.evidence:
                self._save_evidence(
                    connection,
                    run_id,
                    bundle.request_id,
                    item,
                    now,
                )
            connection.execute(
                "DELETE FROM investigation_steps WHERE run_id = ? AND request_id = ?",
                (run_id, bundle.request_id),
            )
            for index, step in enumerate(bundle.investigation_trace):
                connection.execute(
                    """
                    INSERT INTO investigation_steps(
                        run_id, request_id, step_index, action,
                        query, reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        bundle.request_id,
                        index,
                        step.get("action"),
                        step.get("query") or step.get("final_query"),
                        step.get("reason") or step.get("stop_reason"),
                        _json(step),
                    ),
                )

    def fail_evidence_request(self, run_id, request_id, error_type):
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE evidence_requests SET
                    status = 'failed', error_type = ?, completed_at = ?
                WHERE run_id = ? AND request_id = ?
                """,
                (str(error_type), _utc_now(), run_id, request_id),
            )

    @staticmethod
    def _save_evidence(connection, run_id, request_id, item, now):
        if not isinstance(item, dict):
            raise TypeError("证据必须是 dict")
        evidence_id = _nonempty_string(
            item.get("evidence_id"),
            field_name="evidence_id",
        )
        content_json = _json(item)
        existing = connection.execute(
            "SELECT content_json FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if existing is not None and existing["content_json"] != content_json:
            raise ValueError(f"evidence_id 对应了不同内容: {evidence_id}")
        connection.execute(
            """
            INSERT OR IGNORE INTO evidence(
                evidence_id, source_kind, doc_id, chunk_id, page_number,
                url, published_at, content_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                item.get("source_kind"),
                item.get("doc_id"),
                item.get("chunk_id"),
                item.get("page_number"),
                item.get("url"),
                item.get("published_at"),
                content_json,
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO run_evidence(
                run_id, evidence_id, request_id, first_seen_at
            ) VALUES (?, ?, ?, ?)
            """,
            (run_id, evidence_id, request_id, now),
        )

    def save_position(self, run_id, position):
        position = DebatePosition.model_validate(position)
        with self._transaction() as connection:
            for argument in position.arguments:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO arguments(
                        run_id, argument_id, version, agent_id, stance,
                        round_number, status, last_action, claim, reasoning,
                        revision_reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        argument.argument_id,
                        argument.version,
                        position.agent_id,
                        position.stance,
                        position.round_number,
                        argument.status,
                        argument.last_action,
                        argument.claim,
                        argument.reasoning,
                        argument.revision_reason,
                        _json(argument),
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM argument_evidence
                    WHERE run_id = ? AND argument_id = ? AND argument_version = ?
                    """,
                    (run_id, argument.argument_id, argument.version),
                )
                connection.executemany(
                    """
                    INSERT INTO argument_evidence(
                        run_id, argument_id, argument_version, evidence_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            argument.argument_id,
                            argument.version,
                            evidence_id,
                        )
                        for evidence_id in argument.evidence_ids
                    ],
                )

    def save_challenges(self, run_id, challenges):
        if not isinstance(challenges, list):
            raise TypeError("challenges 必须是 list")
        with self._transaction() as connection:
            for raw in challenges:
                item = dict(raw)
                challenge_id = _nonempty_string(
                    item.get("challenge_id"),
                    field_name="challenge_id",
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO challenges(
                        run_id, challenge_id, challenger_id, round_number,
                        challenge_type, target_argument_id, reason,
                        investigation_question, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        challenge_id,
                        item["challenger_id"],
                        item["round_number"],
                        item["challenge_type"],
                        item["target_argument_id"],
                        item["reason"],
                        item["investigation_question"],
                        _json(item),
                    ),
                )
                connection.execute(
                    "DELETE FROM challenge_evidence WHERE run_id = ? AND challenge_id = ?",
                    (run_id, challenge_id),
                )
                connection.executemany(
                    """
                    INSERT INTO challenge_evidence(
                        run_id, challenge_id, evidence_id
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (run_id, challenge_id, evidence_id)
                        for evidence_id in item.get(
                            "target_evidence_ids",
                            [],
                        )
                    ],
                )

    def save_updates(self, run_id, *, round_number, stance, updates):
        if stance not in {"support", "oppose"}:
            raise ValueError("stance 必须是 support 或 oppose")
        if not isinstance(updates, list):
            raise TypeError("updates 必须是 list")
        with self._transaction() as connection:
            for raw in updates:
                item = dict(raw)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO argument_updates(
                        run_id, round_number, stance, argument_id,
                        action, reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        round_number,
                        stance,
                        item["argument_id"],
                        item["action"],
                        item["reason"],
                        _json(item),
                    ),
                )

    def save_debate_result(self, run_id, result):
        result = DebateResult.model_validate(result)
        self.save_position(run_id, result.initial_supporting_position)
        self.save_position(run_id, result.initial_opposing_position)
        self.save_position(run_id, result.supporting_position)
        self.save_position(run_id, result.opposing_position)
        for round_record in result.rounds:
            self.save_challenges(
                run_id,
                [
                    item.model_dump(mode="json")
                    for item in (
                        round_record.supporting_challenges
                        + round_record.opposing_challenges
                    )
                ],
            )
            self.save_updates(
                run_id,
                round_number=round_record.round_number,
                stance="support",
                updates=[
                    item.model_dump(mode="json")
                    for item in round_record.supporting_updates
                ],
            )
            self.save_updates(
                run_id,
                round_number=round_record.round_number,
                stance="oppose",
                updates=[
                    item.model_dump(mode="json")
                    for item in round_record.opposing_updates
                ],
            )
        self.save_checkpoint(
            run_id,
            stage="debate_completed",
            state={"debate": result.model_dump(mode="json")},
        )

    def save_verdict(self, run_id, verdict):
        verdict = JudgeVerdict.model_validate(verdict)
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO verdicts(
                    run_id, judge_id, verdict, summary, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    verdict.judge_id,
                    verdict.verdict,
                    verdict.summary,
                    _json(verdict),
                    now,
                ),
            )

    def save_checkpoint(self, run_id, *, stage, state):
        stage = _nonempty_string(stage, field_name="stage")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints(run_id, stage, state_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, stage, _json(state), _utc_now()),
            )

    def load_latest_checkpoint(self, run_id):
        with self._lock:
            row = self._connection.execute(
                """
                SELECT checkpoint_id, stage, state_json, created_at
                FROM checkpoints WHERE run_id = ?
                ORDER BY checkpoint_id DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "checkpoint_id": row["checkpoint_id"],
            "stage": row["stage"],
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
        }

    def get_run_summary(self, run_id):
        with self._lock:
            run = self._connection.execute(
                "SELECT * FROM debate_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"不存在的 run_id: {run_id}")
            counts = {}
            for name, table in (
                ("events", "run_events"),
                ("evidence_requests", "evidence_requests"),
                ("evidence", "run_evidence"),
                ("argument_versions", "arguments"),
                ("challenges", "challenges"),
                ("verdicts", "verdicts"),
                ("checkpoints", "checkpoints"),
            ):
                counts[name] = self._connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
        return {**dict(run), "counts": counts}

    def list_runs(self, *, limit=20):
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit <= 0
        ):
            raise ValueError("limit 必须是正整数")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT run_id, topic, status, current_stage, current_round,
                       error_type, created_at, updated_at, completed_at
                FROM debate_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
