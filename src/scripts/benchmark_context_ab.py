"""Run a reproducible full-context vs compact-context judge benchmark."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from src.context_budget import active_evidence_ids
from src.judge_generator import (
    PRELIMINARY_JUDGE_SYSTEM_PROMPT,
    build_compact_debate_context,
)
from src.judge_protocol import PreliminaryJudgment
from src.llm_generator import _build_prompt_input
from src.performance_monitor import InstrumentedClient, ModelCallMonitor


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_FILE = (
    PROJECT_ROOT
    / "data"
    / "runs"
    / "debate_optimization_v2_attempt3.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "benchmarks"


class ContextABAnswer(BaseModel):
    """Small, fixed output used to isolate input-context cost."""

    verdict: Literal[
        "support_preferred",
        "oppose_preferred",
        "mixed",
        "insufficient",
    ]
    assessed_argument_ids: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    decisive_reasons: list[str] = Field(default_factory=list, max_length=4)
    unresolved_issues: list[str] = Field(default_factory=list, max_length=4)
    summary: str = Field(max_length=600)


BENCHMARK_SYSTEM_PROMPT = """你是中立的研究辩论裁判。
只评价双方最终 status=active 的观点，不得补充外部事实。
必须在 assessed_argument_ids 中列出你实际检查过的全部有效观点 ID；
必须在 cited_evidence_ids 中列出裁决真正采用的 evidence_id，且不得编造 ID。
输出保持简洁，decisive_reasons 和 unresolved_issues 各不超过 4 条，summary 不超过 600 字。
裁决表示论证质量：support_preferred、oppose_preferred、mixed 或 insufficient。"""


def _json_payload(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _collect_ids(value, *, key):
    result = []

    def visit(node):
        if isinstance(node, dict):
            candidate = node.get(key)
            if candidate is not None:
                result.append(str(candidate))
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return list(dict.fromkeys(result))


def build_variant_context(debate, variant):
    if variant == "baseline":
        return debate
    if variant == "optimized":
        return build_compact_debate_context(debate)
    raise ValueError(f"不支持的 variant: {variant}")


def build_judge_prompt(context):
    return (
        "请审查以下辩论上下文。先形成完整裁决草稿，再判断"
        "是否存在会改变裁决的关键证据需要复核。\n\n"
        f"辩论上下文：\n{_json_payload(context)}"
    )


def evaluate_context_fidelity(debate, context):
    required_ids = active_evidence_ids(
        debate.get("supporting_position", {}),
        debate.get("opposing_position", {}),
    )
    available_ids = set(_collect_ids(context, key="evidence_id"))
    retained = [value for value in required_ids if value in available_ids]
    return {
        "required_evidence_count": len(required_ids),
        "required_evidence_retained": len(retained),
        "required_evidence_retention_rate": (
            round(len(retained) / len(required_ids), 4)
            if required_ids
            else 1.0
        ),
        "missing_required_evidence_ids": [
            value for value in required_ids if value not in available_ids
        ],
    }


def evaluate_judgment_quality(judgment, debate, *, reference_verdict=None):
    draft = judgment["verdict_draft"]
    active_argument_ids = []
    for position_name in ("supporting_position", "opposing_position"):
        position = debate.get(position_name, {})
        for argument in position.get("arguments", []):
            if argument.get("status", "active") == "active":
                active_argument_ids.append(argument.get("argument_id"))
    active_argument_ids = [value for value in active_argument_ids if value]

    assessments = draft.get("argument_assessments", [])
    assessed_ids = {
        item.get("argument_id") for item in assessments
        if item.get("argument_id")
    }
    covered = [value for value in active_argument_ids if value in assessed_ids]

    cited_ids = []
    for assessment in assessments:
        cited_ids.extend(assessment.get("accepted_evidence_ids", []))
    for request in judgment.get("verification_requests", []):
        cited_ids.extend(request.get("evidence_ids", []))
    cited_ids = list(dict.fromkeys(cited_ids))
    valid_evidence_ids = set(_collect_ids(debate, key="evidence_id"))
    invalid_citations = [
        value for value in cited_ids if value not in valid_evidence_ids
    ]

    return {
        "protocol_valid": True,
        "verdict": draft.get("verdict"),
        "reference_verdict": reference_verdict,
        "reference_verdict_match": (
            draft.get("verdict") == reference_verdict
            if reference_verdict
            else None
        ),
        "active_argument_count": len(active_argument_ids),
        "assessed_active_argument_count": len(covered),
        "active_argument_coverage_rate": (
            round(len(covered) / len(active_argument_ids), 4)
            if active_argument_ids
            else 1.0
        ),
        "citation_count": len(cited_ids),
        "invalid_citation_count": len(invalid_citations),
        "citation_validity_rate": (
            round((len(cited_ids) - len(invalid_citations)) / len(cited_ids), 4)
            if cited_ids
            else None
        ),
        "invalid_evidence_ids": invalid_citations,
        "verification_request_count": len(
            judgment.get("verification_requests", [])
        ),
    }


def evaluate_compact_answer_quality(answer, debate, *, reference_verdict=None):
    active_argument_ids = []
    for position_name in ("supporting_position", "opposing_position"):
        for argument in debate.get(position_name, {}).get("arguments", []):
            if argument.get("status", "active") == "active":
                argument_id = argument.get("argument_id")
                if argument_id:
                    active_argument_ids.append(argument_id)
    assessed_ids = set(answer.get("assessed_argument_ids", []))
    covered = [value for value in active_argument_ids if value in assessed_ids]

    cited_ids = list(dict.fromkeys(answer.get("cited_evidence_ids", [])))
    valid_evidence_ids = set(_collect_ids(debate, key="evidence_id"))
    invalid_citations = [
        value for value in cited_ids if value not in valid_evidence_ids
    ]
    return {
        "protocol_valid": True,
        "verdict": answer.get("verdict"),
        "reference_verdict": reference_verdict,
        "reference_verdict_match": (
            answer.get("verdict") == reference_verdict
            if reference_verdict
            else None
        ),
        "active_argument_count": len(active_argument_ids),
        "assessed_active_argument_count": len(covered),
        "active_argument_coverage_rate": (
            round(len(covered) / len(active_argument_ids), 4)
            if active_argument_ids
            else 1.0
        ),
        "citation_count": len(cited_ids),
        "invalid_citation_count": len(invalid_citations),
        "citation_validity_rate": (
            round((len(cited_ids) - len(invalid_citations)) / len(cited_ids), 4)
            if cited_ids
            else None
        ),
        "invalid_evidence_ids": invalid_citations,
    }


def run_variant(
    raw_client,
    *,
    model_name,
    debate,
    variant,
    reference_verdict,
    schema_mode="compact",
):
    context = build_variant_context(debate, variant)
    context_json = _json_payload(context)
    prompt = build_judge_prompt(context)
    monitor = ModelCallMonitor()
    client = InstrumentedClient(raw_client, monitor)
    result = {
        "variant": variant,
        "context_characters": len(context_json),
        "context_fidelity": evaluate_context_fidelity(debate, context),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        if schema_mode == "compact":
            system_prompt = BENCHMARK_SYSTEM_PROMPT
            text_format = ContextABAnswer
        elif schema_mode == "full":
            system_prompt = PRELIMINARY_JUDGE_SYSTEM_PROMPT
            text_format = PreliminaryJudgment
        else:
            raise ValueError(f"不支持的 schema_mode: {schema_mode}")
        response = client.responses.parse(
            model=model_name,
            input=_build_prompt_input(
                system_prompt=system_prompt,
                user_prompt=prompt,
            ),
            text_format=text_format,
        )
        judgment = text_format.model_validate(
            response.output_parsed
        ).model_dump(mode="json")
        result["status"] = "completed"
        result["judgment"] = judgment
        if schema_mode == "compact":
            result["quality"] = evaluate_compact_answer_quality(
                judgment,
                debate,
                reference_verdict=reference_verdict,
            )
        else:
            result["quality"] = evaluate_judgment_quality(
                judgment,
                debate,
                reference_verdict=reference_verdict,
            )
    except Exception as exc:
        result["status"] = "failed"
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
        result["quality"] = {"protocol_valid": False}
    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    result["model_performance"] = monitor.snapshot()
    return result


def _percent_reduction(baseline, optimized):
    if baseline in (None, 0) or optimized is None:
        return None
    return round((1 - optimized / baseline) * 100, 2)


def build_comparison(results):
    by_variant = {item["variant"]: item for item in results}
    baseline = by_variant.get("baseline", {})
    optimized = by_variant.get("optimized", {})
    baseline_perf = baseline.get("model_performance", {})
    optimized_perf = optimized.get("model_performance", {})
    return {
        "context_character_reduction_percent": _percent_reduction(
            baseline.get("context_characters"),
            optimized.get("context_characters"),
        ),
        "input_token_reduction_percent": _percent_reduction(
            baseline_perf.get("input_tokens"),
            optimized_perf.get("input_tokens"),
        ),
        "latency_reduction_percent": _percent_reduction(
            baseline_perf.get("duration_seconds"),
            optimized_perf.get("duration_seconds"),
        ),
        "output_token_reduction_percent": _percent_reduction(
            baseline_perf.get("output_tokens"),
            optimized_perf.get("output_tokens"),
        ),
    }


def render_markdown(report):
    rows = []
    for result in report["results"]:
        perf = result["model_performance"]
        quality = result.get("quality", {})
        fidelity = result["context_fidelity"]
        rows.append(
            "| {variant} | {status} | {chars} | {input_tokens} | "
            "{duration} | {retention:.0%} | {coverage} | {citations} | "
            "{verdict} |".format(
                variant=result["variant"],
                status=result["status"],
                chars=result["context_characters"],
                input_tokens=perf.get("input_tokens"),
                duration=perf.get("duration_seconds"),
                retention=fidelity["required_evidence_retention_rate"],
                coverage=quality.get("active_argument_coverage_rate"),
                citations=quality.get("citation_validity_rate"),
                verdict=quality.get("verdict"),
            )
        )
    comparison = report["comparison"]
    return "\n".join(
        [
            "# 上下文压缩 A/B 评测",
            "",
            f"- 模型：`{report['model_name']}`",
            f"- 来源运行：`{report['source_run']}`",
            f"- 辩题：{report['topic']}",
            "",
            "| 方案 | 状态 | 上下文字数 | input tokens | 模型耗时/秒 | "
            "有效证据保留率 | 有效观点覆盖率 | 引用有效率 | 裁决 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
            *rows,
            "",
            "## 优化幅度",
            "",
            f"- 上下文字数减少：{comparison['context_character_reduction_percent']}%",
            f"- 输入 Token 减少：{comparison['input_token_reduction_percent']}%",
            f"- 模型耗时减少：{comparison['latency_reduction_percent']}%",
            f"- 输出 Token 变化：{comparison['output_token_reduction_percent']}%",
            "",
            "> 答案质量采用可重复的协议与引用指标衡量；历史裁决只作为一致性参考，"
            "不被当作绝对事实标签。",
            "",
        ]
    )


def main():
    parser = argparse.ArgumentParser(
        description="对同一辩论执行完整上下文与压缩上下文裁判 A/B 测试"
    )
    parser.add_argument("--run-file", type=Path, default=DEFAULT_RUN_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只比较上下文规模，不调用模型",
    )
    parser.add_argument(
        "--schema-mode",
        choices=("compact", "full"),
        default="compact",
        help="compact 隔离输入成本；full 使用生产裁判完整输出协议",
    )
    parser.add_argument(
        "--order",
        nargs=2,
        choices=("baseline", "optimized"),
        default=("baseline", "optimized"),
        metavar=("FIRST", "SECOND"),
        help="控制 A/B 执行顺序，便于降低服务时序波动的影响",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    record = json.loads(args.run_file.read_text(encoding="utf-8"))
    debate = record["debate"]
    model_name = os.environ["DEEPSEEK_MODEL"].strip()
    reference_verdict = (record.get("verdict") or {}).get("verdict")

    raw_client = None
    if not args.dry_run:
        raw_client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"].strip(),
            base_url=os.environ["DEEPSEEK_BASE_URL"].strip(),
            timeout=300.0,
            max_retries=0,
        )

    results = []
    if len(set(args.order)) != 2:
        parser.error("--order 必须同时包含 baseline 和 optimized")
    for variant in args.order:
        if args.dry_run:
            context = build_variant_context(debate, variant)
            results.append({
                "variant": variant,
                "status": "dry_run",
                "context_characters": len(_json_payload(context)),
                "context_fidelity": evaluate_context_fidelity(
                    debate,
                    context,
                ),
                "quality": {},
                "model_performance": {},
            })
        else:
            print(f"开始运行 {variant}...", flush=True)
            results.append(
                run_variant(
                    raw_client,
                    model_name=model_name,
                    debate=debate,
                    variant=variant,
                    reference_verdict=reference_verdict,
                    schema_mode=args.schema_mode,
                )
            )
            latest = results[-1]
            perf = latest["model_performance"]
            print(
                f"{variant}: status={latest['status']}, "
                f"input_tokens={perf.get('input_tokens')}, "
                f"duration={perf.get('duration_seconds')}",
                flush=True,
            )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_run": str(args.run_file),
        "topic": record.get("topic"),
        "model_name": model_name,
        "reference_verdict": reference_verdict,
        "schema_mode": args.schema_mode,
        "results": results,
        "comparison": build_comparison(results),
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"context_ab_{timestamp}.json"
    markdown_path = args.output_dir / f"context_ab_{timestamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
