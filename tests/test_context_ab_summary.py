from src.scripts.summarize_context_ab import summarize_reports


def result(variant, input_tokens, total_tokens, duration):
    return {
        "variant": variant,
        "status": "completed",
        "context_characters": 1000 if variant == "baseline" else 400,
        "context_fidelity": {"required_evidence_retention_rate": 1.0},
        "quality": {
            "active_argument_coverage_rate": 1.0,
            "citation_validity_rate": 1.0,
            "reference_verdict_match": True,
        },
        "model_performance": {
            "prompt_characters": 1100 if variant == "baseline" else 500,
            "input_tokens": input_tokens,
            "output_tokens": total_tokens - input_tokens,
            "total_tokens": total_tokens,
            "duration_seconds": duration,
        },
    }


def test_summarize_reports_averages_reversed_ab_runs():
    reports = [
        {"results": [result("baseline", 500, 600, 10), result("optimized", 200, 280, 8)]},
        {"results": [result("optimized", 200, 300, 9), result("baseline", 500, 620, 12)]},
    ]

    summary = summarize_reports(reports)

    assert summary["variants"]["baseline"]["samples"] == 2
    assert summary["variants"]["baseline"]["mean_total_tokens"] == 610
    assert summary["variants"]["optimized"]["mean_total_tokens"] == 290
    assert summary["comparison"]["input_token_reduction_percent"] == 60.0
    assert summary["comparison"]["argument_coverage_delta"] == 0.0
