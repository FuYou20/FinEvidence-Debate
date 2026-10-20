from time import monotonic, sleep

from starlette.testclient import TestClient

from src.debate_api import (
    CreateDebateRequest,
    DebateJobManager,
    build_debate_api_app,
)


def fake_runner(request, emit):
    emit({"name": "debate_started", "topic": request.topic})
    emit({"name": "stage_started", "label": "测试阶段"})
    return {
        "topic": request.topic,
        "debate": {"stop_reason": "no_new_challenges"},
        "verdict": {"verdict": "insufficient"},
    }


def wait_terminal(manager, job_id, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        status = manager.get(job_id)["status"]
        if status in {"completed", "failed"}:
            return status
        sleep(0.01)
    raise AssertionError("job did not finish")


def test_create_request_normalizes_topic_and_focus():
    request = CreateDebateRequest(
        topic="  辩题  ",
        supporting_focus=[" 渠道 ", "渠道"],
    )

    assert request.topic == "辩题"
    assert request.supporting_focus == ["渠道"]


def test_job_manager_records_streamable_events_and_result():
    manager = DebateJobManager(fake_runner)
    try:
        job_id = manager.create({"topic": "测试辩题"})
        assert wait_terminal(manager, job_id) == "completed"

        events, status = manager.events_after(job_id, 0)
        assert status == "completed"
        assert [event["name"] for event in events] == [
            "job_queued",
            "job_started",
            "debate_started",
            "stage_started",
            "job_completed",
        ]
        assert [event["event_id"] for event in events] == [1, 2, 3, 4, 5]
        result = manager.get(job_id, include_result=True)["result"]
        assert result["verdict"]["verdict"] == "insufficient"
    finally:
        manager.close()


def test_http_api_creates_job_streams_sse_and_returns_result():
    manager = DebateJobManager(fake_runner)
    app = build_debate_api_app(manager)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debates",
                json={"topic": "测试流式辩论", "max_challenges": 0},
            )
            assert response.status_code == 202
            payload = response.json()
            job_id = payload["debate_id"]
            assert wait_terminal(manager, job_id) == "completed"

            status = client.get(f"/api/debates/{job_id}")
            assert status.status_code == 200
            assert status.json()["status"] == "completed"

            events = client.get(f"/api/debates/{job_id}/events")
            assert events.status_code == 200
            assert events.headers["content-type"].startswith(
                "text/event-stream"
            )
            assert "event: debate_started" in events.text
            assert "event: job_completed" in events.text

            result = client.get(f"/api/debates/{job_id}/result")
            assert result.status_code == 200
            assert result.json()["result"]["topic"] == "测试流式辩论"
    finally:
        manager.close()


def test_http_api_rejects_invalid_payload_and_unknown_job():
    manager = DebateJobManager(fake_runner)
    app = build_debate_api_app(manager)
    try:
        with TestClient(app) as client:
            invalid = client.post("/api/debates", json={"topic": " "})
            missing = client.get("/api/debates/not-found")

        assert invalid.status_code == 422
        assert missing.status_code == 404
    finally:
        manager.close()


def test_fastapi_serves_frontend_and_openapi_document():
    manager = DebateJobManager(fake_runner)
    app = build_debate_api_app(manager)
    try:
        with TestClient(app) as client:
            page = client.get("/")
            schema = client.get("/openapi.json")
            docs = client.get("/docs")

        assert page.status_code == 200
        assert "FinEvidence" in page.text
        assert schema.status_code == 200
        assert schema.json()["info"]["title"] == "FinEvidence Debate API"
        assert docs.status_code == 200
    finally:
        manager.close()
