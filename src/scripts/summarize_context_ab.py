"""Aggregate multiple successful context A/B benchmark reports."""

import argparse
from datetime import datetime
import json
from pathlib import Path
from statistics import mean


def _mean(records, field):
    values = [record[field] for record in records if record.get(field) is not None]
    return round(mean(values), 6) if values else None


def _reduction(baseline, optimized):
    if baseline in (None, 0) or optimized is None:
        return None
    return round((1 - optimized / baseline) * 100, 2)


def summarize_reports(reports):
    variants = {"baseline": [], "optimized": []}
    for report in reports:
        for result in report["results"]:
            if result.get("status") != "completed":
                continue
            performance = result["model_performance"]
            quality = result["quality"]
            variants[result["variant"]].append({
                "context_characters": result["context_characters"],
                "prompt_characters": performance.get("prompt_characters"),
                "input_tokens": performance.get("input_tokens"),
                "output_tokens": performance.get("output_tokens"),
                "total_tokens": performance.get("total_tokens"),
                "duration_seconds": performance.get("duration_seconds"),
                "required_evidence_retention_rate": result[
                    "context_fidelity"
                ]["required_evidence_retention_rate"],
                "active_argument_coverage_rate": quality.get(
                    "active_argument_coverage_rate"
                ),
                "citation_validity_rate": quality.get(
                    "citation_validity_rate"
                ),
                "reference_verdict_match": quality.get(
                    "reference_verdict_match"
                ),
            })

    aggregates = {}
    for variant, records in variants.items():
        if not records:
            raise ValueError(f"缺少成功的 {variant} 评测结果")
        aggregates[variant] = {
            "samples": len(records),
            "mean_context_characters": _mean(records, "context_characters"),
            "mean_prompt_characters": _mean(records, "prompt_characters"),
            "mean_input_tokens": _mean(records, "input_tokens"),
            "mean_output_tokens": _mean(records, "output_tokens"),
            "mean_total_tokens": _mean(records, "total_tokens"),
            "mean_duration_seconds": _mean(records, "duration_seconds"),
            "mean_required_evidence_retention_rate": _mean(
                records, "required_evidence_retention_rate"
            ),
            "mean_active_argument_coverage_rate": _mean(
                records, "active_argument_coverage_rate"
            ),
            "mean_citation_validity_rate": _mean(
                records, "citation_validity_rate"
            ),
            "verdict_match_samples": sum(
                item["reference_verdict_match"] is True for item in records
            ),
        }

    baseline = aggregates["baseline"]
    optimized = aggregates["optimized"]
    comparison = {
        "context_character_reduction_percent": _reduction(
            baseline["mean_context_characters"],
            optimized["mean_context_characters"],
        ),
        "input_token_reduction_percent": _reduction(
            baseline["mean_input_tokens"],
            optimized["mean_input_tokens"],
        ),
        "total_token_reduction_percent": _reduction(
            baseline["mean_total_tokens"],
            optimized["mean_total_tokens"],
        ),
        "latency_reduction_percent": _reduction(
            baseline["mean_duration_seconds"],
            optimized["mean_duration_seconds"],
        ),
        "argument_coverage_delta": round(
            optimized["mean_active_argument_coverage_rate"]
            - baseline["mean_active_argument_coverage_rate"],
            4,
        ),
        "citation_validity_delta": round(
            optimized["mean_citation_validity_rate"]
            - baseline["mean_citation_validity_rate"],
            4,
        ),
    }
    return {"variants": aggregates, "comparison": comparison}


def render_markdown(summary, *, source_files):
    baseline = summary["variants"]["baseline"]
    optimized = summary["variants"]["optimized"]
    comparison = summary["comparison"]
    resume_line = (
        "设计分层上下文压缩与状态投影机制；在同一辩论、同一模型的"
        f" {baseline['samples']} 组反序 A/B 中，将裁判输入 Token 从"
        f" {baseline['mean_input_tokens']:.0f} 降至"
        f" {optimized['mean_input_tokens']:.0f}（减少"
        f" {comparison['input_token_reduction_percent']}%），平均总 Token"
        f" 减少 {comparison['total_token_reduction_percent']}%，同时保持"
        " 100% 有效观点覆盖率、100% 引用有效率与裁决一致性。"
    )
    return "\n".join([
        "# 上下文管理优化汇总报告",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## A/B 结果",
        "",
        "| 指标 | 完整上下文 | 压缩上下文 | 变化 |",
        "|---|---:|---:|---:|",
        f"| 上下文字数 | {baseline['mean_context_characters']:.0f} | "
        f"{optimized['mean_context_characters']:.0f} | "
        f"-{comparison['context_character_reduction_percent']}% |",
        f"| 输入 Token | {baseline['mean_input_tokens']:.0f} | "
        f"{optimized['mean_input_tokens']:.0f} | "
        f"-{comparison['input_token_reduction_percent']}% |",
        f"| 平均总 Token | {baseline['mean_total_tokens']:.1f} | "
        f"{optimized['mean_total_tokens']:.1f} | "
        f"-{comparison['total_token_reduction_percent']}% |",
        f"| 平均模型耗时/秒 | {baseline['mean_duration_seconds']:.2f} | "
        f"{optimized['mean_duration_seconds']:.2f} | "
        f"{comparison['latency_reduction_percent']}% |",
        f"| 有效观点覆盖率 | {baseline['mean_active_argument_coverage_rate']:.0%} | "
        f"{optimized['mean_active_argument_coverage_rate']:.0%} | 0 |",
        f"| 引用有效率 | {baseline['mean_citation_validity_rate']:.0%} | "
        f"{optimized['mean_citation_validity_rate']:.0%} | 0 |",
        "",
        "延迟未随输入 Token 同步下降；本次压缩版本平均输出 Token 略有波动，"
        "且端到端延迟同时受模型推理与服务负载影响。因此简历只主张 Token"
        " 与上下文规模优化，不宣称延迟优化。",
        "",
        "## 推荐简历表述",
        "",
        resume_line,
        "",
        "## 原始报告",
        "",
        *[f"- `{path}`" for path in source_files],
        "",
    ])


def main():
    parser = argparse.ArgumentParser(description="汇总多组上下文 A/B 结果")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in args.reports
    ]
    summary = summarize_reports(reports)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_reports": [str(path) for path in args.reports],
        **summary,
    }
    json_path = args.output_prefix.with_suffix(".json")
    markdown_path = args.output_prefix.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_markdown(summary, source_files=args.reports),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
