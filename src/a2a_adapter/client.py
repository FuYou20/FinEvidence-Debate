"""Client-side adapter for calling FinEvidence over A2A.

The debate domain is synchronous today. This adapter exposes an async API for
the future LangGraph workflow and a synchronous wrapper for the existing loop.
"""

import asyncio
from copy import deepcopy
from time import perf_counter

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest, TaskState
import httpx

from src.evidence_protocol import EvidenceBundle, EvidenceRequest
from src.evidence_registry import EvidenceRegistry

from .protocol import EVIDENCE_REQUEST_MEDIA_TYPE


_FAILED_STATES = {
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


class A2AEvidenceClientError(RuntimeError):
    """FinEvidence could not return one valid EvidenceBundle."""


class A2AEvidenceClient:
    """Expose a remote FinEvidence Agent as the local evidence-runtime API."""

    def __init__(self, base_url, *, timeout_seconds=240.0, transport=None):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url 必须是非空字符串")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if transport is not None and not callable(transport):
            raise TypeError("transport 必须是可调用对象")

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport or self._send_over_a2a
        self.registry = EvidenceRegistry()
        self._requests = []
        self._metrics = {
            "transport": "a2a",
            "request_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_seconds": 0.0,
        }

    async def _send_over_a2a(self, request):
        """Send one request and return the last EvidenceBundle DataPart."""
        bundle_payload = None
        terminal_failure = None
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            card = await A2ACardResolver(
                httpx_client=http_client,
                base_url=self.base_url,
            ).get_agent_card()
            client = await create_client(
                agent=card,
                client_config=ClientConfig(
                    streaming=True,
                    httpx_client=http_client,
                ),
            )
            try:
                message_request = SendMessageRequest(
                    message=new_data_message(
                        request.model_dump(mode="json"),
                        media_type=EVIDENCE_REQUEST_MEDIA_TYPE,
                        role=Role.ROLE_USER,
                    )
                )
                async for response in client.send_message(message_request):
                    if response.HasField("status_update"):
                        state = response.status_update.status.state
                        if state in _FAILED_STATES:
                            terminal_failure = str(state)
                    if response.HasField("artifact_update"):
                        for payload in get_data_parts(
                            response.artifact_update.artifact.parts
                        ):
                            if isinstance(payload, dict):
                                bundle_payload = payload
            finally:
                await client.close()

        if terminal_failure is not None:
            raise A2AEvidenceClientError(
                f"FinEvidence A2A 任务失败: {terminal_failure}"
            )
        if bundle_payload is None:
            raise A2AEvidenceClientError(
                "FinEvidence A2A 响应中没有 EvidenceBundle artifact"
            )
        return bundle_payload

    def _record_evidence(self, bundle):
        grouped = {}
        for evidence in bundle.evidence:
            source_kind = evidence.get("source_kind")
            if source_kind not in {"local", "web", "multimodal", "financial"}:
                raise A2AEvidenceClientError(
                    "远程证据缺少受支持的 source_kind"
                )
            grouped.setdefault(source_kind, []).append(evidence)
        for source_kind, records in grouped.items():
            self.registry.register(records, source_kind=source_kind)

    async def async_request_evidence(self, *, request):
        """Request and validate evidence without blocking an event loop."""
        validated_request = EvidenceRequest.model_validate(request)
        started = perf_counter()
        self._metrics["request_count"] += 1
        record = {"request": validated_request.model_dump(mode="json")}
        try:
            raw_bundle = await self._transport(validated_request)
            bundle = EvidenceBundle.model_validate(raw_bundle)
            if bundle.request_id != validated_request.request_id:
                raise A2AEvidenceClientError(
                    "远程 EvidenceBundle 的 request_id 与请求不一致"
                )
            if bundle.mode != validated_request.mode:
                raise A2AEvidenceClientError(
                    "远程 EvidenceBundle 的 mode 与请求不一致"
                )
            self._record_evidence(bundle)
        except Exception as exc:
            self._metrics["failure_count"] += 1
            record["error_type"] = type(exc).__name__
            raise
        else:
            self._metrics["success_count"] += 1
            record["result"] = bundle.model_dump(mode="json")
            return bundle
        finally:
            elapsed = perf_counter() - started
            self._metrics["total_seconds"] += elapsed
            record["elapsed_seconds"] = round(elapsed, 6)
            self._requests.append(record)

    def request_evidence(self, *, request):
        """Synchronous compatibility API used by the current debate loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_request_evidence(request=request))
        raise RuntimeError(
            "事件循环中请 await async_request_evidence()，不要调用同步接口"
        )

    def snapshot_requests(self):
        return deepcopy(self._requests)

    def snapshot_metrics(self):
        metrics = deepcopy(self._metrics)
        count = metrics["request_count"]
        metrics["average_seconds"] = (
            metrics["total_seconds"] / count if count else 0.0
        )
        return metrics

    def close(self):
        """Compatibility hook; each v1 request owns its HTTP client."""
