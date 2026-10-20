"""FastAPI REST/SSE service for LangGraph debate jobs.

Browsers use a small REST/SSE surface. Evidence investigation remains a
backend-to-backend A2A call so the web client never needs to understand A2A.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from .a2a_adapter.client import A2AEvidenceClient
from .debate_agent import create_research_debate_agent
from .debate_graph import run_debate_graph
from .debate_judge import create_debate_judge


logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"completed", "failed"}
FRONTEND_DIR = Path(__file__).resolve().parent / "web" / "static"


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CreateDebateRequest(BaseModel):
    topic: str
    max_rounds: int = Field(default=1, ge=1, le=5)
    max_challenges: int = Field(default=1, ge=0, le=5)
    max_verifications: int = Field(default=1, ge=0, le=5)
    max_deepening_steps: int = Field(default=1, ge=0, le=4)
    supporting_focus: list[str] = Field(default_factory=list)
    opposing_focus: list[str] = Field(default_factory=list)

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value):
        normalized = value.strip()
        if not normalized:
            raise ValueError("topic 不能为空")
        return normalized

    @field_validator("supporting_focus", "opposing_focus")
    @classmethod
    def normalize_focus(cls, values):
        result = []
        seen = set()
        for value in values:
            normalized = value.strip()
            if not normalized:
                raise ValueError("focus 不能包含空字符串")
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result


class DebateJobManager:
    """Run blocking debate graphs and retain a bounded in-process event log."""

    def __init__(self, runner, *, max_workers=1, max_events_per_job=1000):
        if not callable(runner):
            raise TypeError("runner 必须是可调用对象")
        if max_workers <= 0:
            raise ValueError("max_workers 必须大于 0")
        if max_events_per_job <= 0:
            raise ValueError("max_events_per_job 必须大于 0")
        self.runner = runner
        self.max_events_per_job = max_events_per_job
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="debate-job",
        )
        self._jobs = {}
        self._lock = RLock()

    def create(self, request):
        validated = CreateDebateRequest.model_validate(request)
        job_id = uuid4().hex
        now = _utc_now()
        with self._lock:
            self._jobs[job_id] = {
                "debate_id": job_id,
                "status": "queued",
                "topic": validated.topic,
                "request": validated.model_dump(mode="json"),
                "created_at": now,
                "updated_at": now,
                "events": [],
                "next_event_id": 1,
                "result": None,
                "error_type": None,
            }
        self._emit(job_id, {"name": "job_queued"})
        self._executor.submit(self._run, job_id, validated)
        return job_id

    def _emit(self, job_id, event):
        if not isinstance(event, dict) or not event.get("name"):
            raise ValueError("event 必须包含非空 name")
        with self._lock:
            job = self._jobs[job_id]
            item = {
                "event_id": job["next_event_id"],
                "at": _utc_now(),
                **deepcopy(event),
            }
            job["next_event_id"] += 1
            job["events"].append(item)
            if len(job["events"]) > self.max_events_per_job:
                job["events"] = job["events"][-self.max_events_per_job:]
            job["updated_at"] = item["at"]

    def _run(self, job_id, request):
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["updated_at"] = _utc_now()
        self._emit(job_id, {"name": "job_started"})
        try:
            result = self.runner(
                request,
                lambda event: self._emit(job_id, event),
            )
        except Exception as exc:
            logger.exception("debate job failed: %s", job_id)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["error_type"] = type(exc).__name__
                job["updated_at"] = _utc_now()
            self._emit(
                job_id,
                {
                    "name": "job_failed",
                    "error_type": type(exc).__name__,
                },
            )
            return
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "completed"
            job["result"] = deepcopy(result)
            job["updated_at"] = _utc_now()
        self._emit(job_id, {"name": "job_completed"})

    def get(self, job_id, *, include_result=False):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            payload = {
                key: deepcopy(job[key])
                for key in (
                    "debate_id",
                    "status",
                    "topic",
                    "request",
                    "created_at",
                    "updated_at",
                    "error_type",
                )
            }
            payload["event_count"] = len(job["events"])
            if include_result:
                payload["result"] = deepcopy(job["result"])
            return payload

    def events_after(self, job_id, event_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, None
            events = [
                deepcopy(item)
                for item in job["events"]
                if item["event_id"] > event_id
            ]
            return events, job["status"]

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)


class ProductionDebateRunner:
    """Compose debate Agents while delegating all evidence work over A2A."""

    def __init__(
        self,
        *,
        api_key,
        base_url,
        model_name,
        evidence_base_url,
        evidence_timeout_seconds=240.0,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.evidence_base_url = evidence_base_url
        self.evidence_timeout_seconds = evidence_timeout_seconds

    @classmethod
    def from_env(cls):
        def require(name):
            value = os.getenv(name)
            if not value or not value.strip():
                raise RuntimeError(f"未检测到 {name}")
            return value.strip()

        return cls(
            api_key=require("DEEPSEEK_API_KEY"),
            base_url=require("DEEPSEEK_BASE_URL"),
            model_name=require("DEEPSEEK_MODEL"),
            evidence_base_url=os.getenv(
                "A2A_BASE_URL", "http://127.0.0.1:9999"
            ).strip(),
            evidence_timeout_seconds=float(
                os.getenv("DEBATE_API_A2A_TIMEOUT", "240")
            ),
        )

    def __call__(self, request, emit):
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=60.0,
            max_retries=1,
        )
        evidence_client = A2AEvidenceClient(
            self.evidence_base_url,
            timeout_seconds=self.evidence_timeout_seconds,
        )

        def request_evidence(*, request):
            bounded_request = request.model_copy(update={
                "max_deepening_steps": request_config.max_deepening_steps,
            })
            emit({
                "name": "evidence_started",
                "request": bounded_request.model_dump(mode="json"),
            })
            bundle = evidence_client.request_evidence(
                request=bounded_request
            )
            emit({
                "name": "evidence_ready",
                "bundle": bundle.model_dump(mode="json"),
            })
            return bundle

        request_config = CreateDebateRequest.model_validate(request)
        supporting = create_research_debate_agent(
            client,
            model_name=self.model_name,
            agent_id="support-agent",
            stance="support",
            request_evidence=request_evidence,
        )
        opposing = create_research_debate_agent(
            client,
            model_name=self.model_name,
            agent_id="oppose-agent",
            stance="oppose",
            request_evidence=request_evidence,
        )
        judge = create_debate_judge(
            client,
            model_name=self.model_name,
            request_evidence=request_evidence,
        )
        try:
            debate = run_debate_graph(
                request_config.topic,
                supporting,
                opposing,
                config={
                    "max_rounds": request_config.max_rounds,
                    "max_challenges_per_agent_per_round": (
                        request_config.max_challenges
                    ),
                },
                supporting_focus=request_config.supporting_focus,
                opposing_focus=request_config.opposing_focus,
                on_event=emit,
            )
            verdict = judge.judge(
                debate,
                max_verification_requests=(
                    request_config.max_verifications
                ),
                on_event=emit,
            )
            return {
                "topic": request_config.topic,
                "debate": debate.model_dump(mode="json"),
                "verdict": verdict.model_dump(mode="json"),
                "evidence_registry": evidence_client.registry.snapshot(),
                "evidence_requests": evidence_client.snapshot_requests(),
                "performance": {
                    "evidence_a2a": evidence_client.snapshot_metrics()
                },
            }
        finally:
            evidence_client.close()


def _sse(event):
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return (
        f'id: {event["event_id"]}\n'
        f'event: {event["name"]}\n'
        f"data: {data}\n\n"
    )


def build_debate_api_app(job_manager, *, allowed_origins=None):
    if not isinstance(job_manager, DebateJobManager):
        raise TypeError("job_manager 必须是 DebateJobManager")
    app = FastAPI(
        title="FinEvidence Debate API",
        description=(
            "基于 LangGraph 与 A2A 的证据驱动研究辩论接口，"
            "通过 SSE 推送执行过程。"
        ),
        version="0.2.0",
    )
    app.state.job_manager = job_manager
    origins = allowed_origins or [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "service": "debate-api"}

    @app.post("/api/debates", status_code=202, tags=["debates"])
    async def create_debate(payload: CreateDebateRequest):
        debate_id = job_manager.create(payload)
        return {
            "debate_id": debate_id,
            "status": "queued",
            "status_url": f"/api/debates/{debate_id}",
            "events_url": f"/api/debates/{debate_id}/events",
            "result_url": f"/api/debates/{debate_id}/result",
        }

    @app.get("/api/debates/{debate_id}", tags=["debates"])
    async def get_debate(debate_id: str):
        payload = job_manager.get(debate_id)
        if payload is None:
            return JSONResponse(
                {"detail": "debate not found"}, status_code=404
            )
        return payload

    @app.get("/api/debates/{debate_id}/result", tags=["debates"])
    async def get_result(debate_id: str):
        payload = job_manager.get(debate_id, include_result=True)
        if payload is None:
            return JSONResponse(
                {"detail": "debate not found"}, status_code=404
            )
        if payload["status"] == "failed":
            return JSONResponse(payload, status_code=409)
        if payload["status"] != "completed":
            return JSONResponse(payload, status_code=202)
        return payload

    @app.get(
        "/api/debates/{debate_id}/events",
        tags=["debates"],
        response_class=StreamingResponse,
    )
    async def stream_events(
        debate_id: str,
        request: Request,
        after: int = 0,
    ):
        if job_manager.get(debate_id) is None:
            return JSONResponse(
                {"detail": "debate not found"}, status_code=404
            )
        raw_after = request.headers.get("last-event-id", str(after))
        try:
            cursor_start = max(0, int(raw_after))
        except ValueError:
            return JSONResponse(
                {"detail": "Last-Event-ID 必须是非负整数"},
                status_code=422,
            )

        async def generate():
            cursor = cursor_start
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    break
                events, status = job_manager.events_after(
                    debate_id, cursor
                )
                for event in events:
                    cursor = event["event_id"]
                    yield _sse(event)
                if status in TERMINAL_STATUSES and not events:
                    break
                idle_ticks += 1
                if idle_ticks % 50 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    if FRONTEND_DIR.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=FRONTEND_DIR),
            name="static",
        )

        @app.get("/", include_in_schema=False)
        async def frontend():
            return FileResponse(FRONTEND_DIR / "index.html")

    return app
