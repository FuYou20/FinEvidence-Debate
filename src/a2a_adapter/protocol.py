"""Translate between A2A Parts and FinEvidence domain models."""

import json

from a2a.helpers import get_data_parts, get_message_text, new_data_part
from pydantic import ValidationError

from src.evidence_protocol import EvidenceBundle, EvidenceRequest


EVIDENCE_REQUEST_MEDIA_TYPE = (
    "application/vnd.finevidence.evidence-request+json"
)
EVIDENCE_BUNDLE_MEDIA_TYPE = (
    "application/vnd.finevidence.evidence-bundle+json"
)


class A2AEvidenceRequestError(ValueError):
    """The A2A message does not contain one valid EvidenceRequest."""


def message_to_evidence_request(message):
    """Read one structured request from an A2A Message.

    DataPart is the canonical transport.  JSON text is accepted as a
    debugging convenience for generic A2A clients.
    """
    if message is None:
        raise A2AEvidenceRequestError("A2A message 不能为空")
    data_parts = get_data_parts(message.parts)
    if len(data_parts) > 1:
        raise A2AEvidenceRequestError(
            "一个请求只能包含一个 EvidenceRequest DataPart"
        )
    if data_parts:
        payload = data_parts[0]
    else:
        raw_text = get_message_text(message).strip()
        if not raw_text:
            raise A2AEvidenceRequestError(
                "请求必须包含 EvidenceRequest DataPart 或 JSON 文本"
            )
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise A2AEvidenceRequestError(
                "文本输入必须是合法的 EvidenceRequest JSON"
            ) from exc
    if not isinstance(payload, dict):
        raise A2AEvidenceRequestError(
            "EvidenceRequest 必须是 JSON object"
        )
    try:
        return EvidenceRequest.model_validate(payload)
    except ValidationError as exc:
        raise A2AEvidenceRequestError(
            f"EvidenceRequest 字段校验失败: {exc}"
        ) from exc


def evidence_bundle_to_parts(bundle):
    """Serialize one validated bundle as an A2A DataPart list."""
    validated = EvidenceBundle.model_validate(bundle)
    return [
        new_data_part(
            validated.model_dump(mode="json"),
            media_type=EVIDENCE_BUNDLE_MEDIA_TYPE,
        )
    ]
