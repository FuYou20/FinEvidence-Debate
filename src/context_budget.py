"""Prompt-side context compaction utilities.

The durable debate and evidence records remain untouched.  These helpers only
build bounded projections for model calls, using deterministic character
budgets so the project does not depend on a model-specific tokenizer.
"""

from copy import deepcopy


DEFAULT_MAX_EVIDENCE_ITEMS = 12
DEFAULT_MAX_TOTAL_TEXT_CHARS = 16_000
DEFAULT_MAX_TEXT_CHARS_PER_ITEM = 2_400
DEFAULT_RECENT_DEBATE_ROUNDS = 1


EVIDENCE_PROMPT_FIELDS = (
    "evidence_id",
    "doc_id",
    "chunk_id",
    "page_number",
    "content_type",
    "table_id",
    "title",
    "published_at",
    "source_kind",
    "content_origin",
    "url",
    "source_path",
    "rank",
    "score",
    "text",
)


def _require_nonnegative_int(value, *, field_name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数")


def truncate_text(text, max_chars):
    """Return a bounded excerpt while retaining both ends of long content."""
    if not isinstance(text, str):
        text = str(text)
    _require_nonnegative_int(max_chars, field_name="max_chars")
    if len(text) <= max_chars:
        return text
    if max_chars == 0:
        return ""

    marker = "\n…[正文已压缩]…\n"
    if max_chars <= len(marker) + 8:
        return text[:max_chars]
    available = max_chars - len(marker)
    head_size = max(1, int(available * 0.7))
    tail_size = available - head_size
    return text[:head_size] + marker + text[-tail_size:]


def _evidence_identity(record, position):
    evidence_id = record.get("evidence_id")
    if evidence_id:
        return ("evidence_id", str(evidence_id))
    return (
        "location",
        record.get("doc_id"),
        record.get("chunk_id"),
        record.get("page_number"),
        record.get("table_id"),
        position,
    )


def compact_evidence_records(
    evidence,
    *,
    priority_ids=(),
    max_items=DEFAULT_MAX_EVIDENCE_ITEMS,
    max_total_text_chars=DEFAULT_MAX_TOTAL_TEXT_CHARS,
    max_text_chars_per_item=DEFAULT_MAX_TEXT_CHARS_PER_ITEM,
):
    """Deduplicate evidence and create a bounded, traceable prompt projection.

    ``priority_ids`` are selected before ordinary retrieval results.  The item
    cap is soft for those IDs: evidence already cited by an active argument is
    never discarded merely to satisfy ``max_items``.
    """
    if not isinstance(evidence, list):
        raise TypeError("evidence 必须是 list")
    for field_name, value in (
        ("max_items", max_items),
        ("max_total_text_chars", max_total_text_chars),
        ("max_text_chars_per_item", max_text_chars_per_item),
    ):
        _require_nonnegative_int(value, field_name=field_name)

    unique_records = []
    seen = set()
    for position, record in enumerate(evidence):
        if not isinstance(record, dict):
            continue
        identity = _evidence_identity(record, position)
        if identity in seen:
            continue
        seen.add(identity)
        unique_records.append(record)

    priority_order = []
    priority_seen = set()
    for value in priority_ids:
        if value is None:
            continue
        normalized = str(value)
        if normalized not in priority_seen:
            priority_order.append(normalized)
            priority_seen.add(normalized)

    by_id = {
        str(record["evidence_id"]): record
        for record in unique_records
        if record.get("evidence_id") is not None
    }
    selected = [by_id[value] for value in priority_order if value in by_id]
    selected_object_ids = {id(record) for record in selected}
    ordinary_limit = max(max_items - len(selected), 0)
    for record in unique_records:
        if id(record) in selected_object_ids:
            continue
        if ordinary_limit <= 0:
            break
        selected.append(record)
        selected_object_ids.add(id(record))
        ordinary_limit -= 1

    compacted = []
    remaining_chars = max_total_text_chars
    for position, record in enumerate(selected):
        remaining_items = len(selected) - position
        fair_share = (
            remaining_chars // remaining_items
            if remaining_items and remaining_chars > 0
            else 0
        )
        allowance = min(max_text_chars_per_item, fair_share)
        original_text = str(record.get("text", ""))
        excerpt = truncate_text(original_text, allowance)
        remaining_chars -= len(excerpt)

        prompt_record = {
            field: deepcopy(record[field])
            for field in EVIDENCE_PROMPT_FIELDS
            if field in record and field != "text"
        }
        prompt_record["text"] = excerpt
        if len(excerpt) < len(original_text):
            prompt_record["text_truncated"] = True
            prompt_record["original_text_chars"] = len(original_text)
        compacted.append(prompt_record)

    return compacted


def collect_evidence_records(value):
    """Collect nested evidence dictionaries without copying large embeddings."""
    records = []

    def visit(node):
        if isinstance(node, dict):
            if node.get("evidence_id") and "text" in node:
                records.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return records


def active_evidence_ids(*positions):
    """Return cited evidence IDs in first-seen order from active arguments."""
    result = []
    seen = set()
    for position in positions:
        if not isinstance(position, dict):
            continue
        for argument in position.get("arguments", []):
            if argument.get("status", "active") != "active":
                continue
            for evidence_id in argument.get("evidence_ids", []):
                if evidence_id not in seen:
                    result.append(evidence_id)
                    seen.add(evidence_id)
    return result


def compact_position(position, *, include_withdrawn=False):
    """Project a position to the arguments that still matter for the next call."""
    if not isinstance(position, dict):
        return position
    arguments = position.get("arguments", [])
    active = [
        deepcopy(item)
        for item in arguments
        if item.get("status", "active") == "active"
    ]
    result = {
        key: deepcopy(position.get(key))
        for key in ("agent_id", "stance", "round_number")
        if key in position
    }
    result["arguments"] = active
    if include_withdrawn:
        result["withdrawn_argument_ids"] = [
            item.get("argument_id")
            for item in arguments
            if item.get("status") == "withdrawn"
            and item.get("argument_id")
        ]
    return result


def _bounded_list(values, *, max_items=8):
    if not isinstance(values, list):
        return []
    if len(values) <= max_items:
        return deepcopy(values)
    return deepcopy(values[:max_items]) + [
        {"omitted_item_count": len(values) - max_items}
    ]


def compact_evidence_bundle(
    bundle,
    *,
    include_evidence=False,
    priority_ids=(),
    max_evidence_items=DEFAULT_MAX_EVIDENCE_ITEMS,
    max_total_text_chars=DEFAULT_MAX_TOTAL_TEXT_CHARS,
    max_text_chars_per_item=DEFAULT_MAX_TEXT_CHARS_PER_ITEM,
):
    """Project an EvidenceBundle; audit lists and traces are explicitly bounded."""
    if not isinstance(bundle, dict):
        return {}
    result = {
        "request_id": bundle.get("request_id"),
        "mode": bundle.get("mode"),
        "status": bundle.get("status"),
        "evidence_ids": [
            item.get("evidence_id")
            for item in bundle.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        ],
        "source_checks": _bounded_list(bundle.get("source_checks", [])),
        "inconsistencies": _bounded_list(bundle.get("inconsistencies", [])),
        "missing_information": _bounded_list(
            bundle.get("missing_information", [])
        ),
        "investigation_trace": _bounded_list(
            bundle.get("investigation_trace", []),
            max_items=4,
        ),
    }
    if include_evidence:
        result["evidence"] = compact_evidence_records(
            bundle.get("evidence", []),
            priority_ids=priority_ids,
            max_items=max_evidence_items,
            max_total_text_chars=max_total_text_chars,
            max_text_chars_per_item=max_text_chars_per_item,
        )
    return result


def compact_investigations(
    investigations,
    *,
    priority_ids=(),
    max_total_text_chars=DEFAULT_MAX_TOTAL_TEXT_CHARS,
    max_text_chars_per_item=DEFAULT_MAX_TEXT_CHARS_PER_ITEM,
):
    """Compact challenge investigations while keeping challenge/bundle linkage."""
    if not isinstance(investigations, list):
        raise TypeError("investigations 必须是 list")
    if not investigations:
        return []
    per_bundle_budget = max_total_text_chars // len(investigations)
    return [
        {
            "challenge": deepcopy(item.get("challenge", {})),
            "evidence_bundle": compact_evidence_bundle(
                item.get("evidence_bundle", {}),
                include_evidence=True,
                priority_ids=priority_ids,
                max_total_text_chars=per_bundle_budget,
                max_text_chars_per_item=max_text_chars_per_item,
            ),
        }
        for item in investigations
        if isinstance(item, dict)
    ]
