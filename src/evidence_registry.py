from copy import deepcopy
import hashlib
import json


_TRANSIENT_FIELDS = frozenset(
    {
        "embedding",
        "index_id",
        "rank",
        "score",
        "dense_rank",
        "dense_score",
        "sparse_rank",
        "sparse_score",
        "rrf_score",
        "retrieval_source",
        "retrieval_score",
        "neighbor_score",
        "is_direct_hit",
        "expanded_from",
        "neighbor_distance",
    }
)


def _normalized_nonempty_string(value, *, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _canonical_record(record, *, source_kind):
    if not isinstance(record, dict):
        raise TypeError("证据记录必须是 dict")
    if source_kind not in {"local", "web", "multimodal", "financial"}:
        raise ValueError(f"不支持的 source_kind: {source_kind}")

    normalized = {
        key: deepcopy(value)
        for key, value in record.items()
        if key not in _TRANSIENT_FIELDS
    }
    normalized["source_kind"] = source_kind

    explicit_id = normalized.get("evidence_id")
    if explicit_id is not None:
        normalized["evidence_id"] = _normalized_nonempty_string(
            explicit_id,
            field_name="evidence_id",
        )
        return normalized

    if source_kind == "web":
        url = _normalized_nonempty_string(
            normalized.get("url"),
            field_name="web 证据的 url",
        )
        normalized["url"] = url
        identity = _stable_payload(normalized)
    else:
        doc_id = _normalized_nonempty_string(
            normalized.get("doc_id"),
            field_name="doc_id",
        )
        chunk_id = _normalized_nonempty_string(
            normalized.get("chunk_id"),
            field_name="chunk_id",
        )
        identity = f"{doc_id}|{chunk_id}"

    digest = hashlib.sha256(
        f"{source_kind}|{identity}".encode("utf-8")
    ).hexdigest()[:16]
    normalized["evidence_id"] = f"E-{source_kind}-{digest}"
    return normalized


def _stable_payload(record):
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class EvidenceRegistry:
    """保存 evidence_id 到不可变证据快照的映射。"""

    def __init__(self):
        self._records = {}

    def register(self, records, *, source_kind):
        if not isinstance(records, list):
            raise TypeError("records 必须是 list")

        registered = []
        for record in records:
            normalized = _canonical_record(
                record,
                source_kind=source_kind,
            )
            evidence_id = normalized["evidence_id"]
            existing = self._records.get(evidence_id)
            if existing is not None:
                if _stable_payload(existing) != _stable_payload(normalized):
                    raise ValueError(
                        f"evidence_id 对应了不同内容: {evidence_id}"
                    )
                registered.append(deepcopy(existing))
                continue

            self._records[evidence_id] = deepcopy(normalized)
            registered.append(deepcopy(normalized))
        return registered

    def get(self, evidence_id):
        normalized_id = _normalized_nonempty_string(
            evidence_id,
            field_name="evidence_id",
        )
        if normalized_id not in self._records:
            raise KeyError(f"未注册的 evidence_id: {normalized_id}")
        return deepcopy(self._records[normalized_id])

    def get_many(self, evidence_ids):
        if not isinstance(evidence_ids, list):
            raise TypeError("evidence_ids 必须是 list")
        return [self.get(evidence_id) for evidence_id in evidence_ids]

    def snapshot(self):
        return deepcopy(self._records)

    def __len__(self):
        return len(self._records)
