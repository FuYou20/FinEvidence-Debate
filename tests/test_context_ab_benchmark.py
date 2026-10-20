from src.scripts.benchmark_context_ab import (
    build_comparison,
    build_variant_context,
    evaluate_context_fidelity,
    evaluate_compact_answer_quality,
    evaluate_judgment_quality,
)


def make_debate():
    evidence = {
        "evidence_id": "E1",
        "doc_id": "D1",
        "chunk_id": "C1",
        "page_number": 1,
        "text": "证据" * 100,
    }
    argument = {
        "argument_id": "A1",
        "status": "active",
        "evidence_ids": ["E1"],
    }
    return {
        "topic": "辩题",
        "supporting_position": {"arguments": [argument]},
        "opposing_position": {"arguments": []},
        "initial_evidence": [{"evidence": [evidence]}],
        "rounds": [],
    }


def test_optimized_context_retains_active_argument_evidence():
    debate = make_debate()
    context = build_variant_context(debate, "optimized")

    result = evaluate_context_fidelity(debate, context)

    assert result["required_evidence_retention_rate"] == 1.0
    assert result["missing_required_evidence_ids"] == []


def test_quality_metrics_check_coverage_and_invalid_citations():
    debate = make_debate()
    judgment = {
        "verdict_draft": {
            "verdict": "mixed",
            "argument_assessments": [
                {
                    "argument_id": "A1",
                    "accepted_evidence_ids": ["E1", "UNKNOWN"],
                }
            ],
        },
        "verification_requests": [],
    }

    result = evaluate_judgment_quality(
        judgment,
        debate,
        reference_verdict="mixed",
    )

    assert result["active_argument_coverage_rate"] == 1.0
    assert result["citation_validity_rate"] == 0.5
    assert result["invalid_evidence_ids"] == ["UNKNOWN"]
    assert result["reference_verdict_match"] is True


def test_compact_answer_quality_checks_same_core_invariants():
    result = evaluate_compact_answer_quality(
        {
            "verdict": "mixed",
            "assessed_argument_ids": ["A1"],
            "cited_evidence_ids": ["E1"],
        },
        make_debate(),
        reference_verdict="mixed",
    )

    assert result["active_argument_coverage_rate"] == 1.0
    assert result["citation_validity_rate"] == 1.0
    assert result["reference_verdict_match"] is True


def test_comparison_calculates_reductions():
    results = [
        {
            "variant": "baseline",
            "context_characters": 1000,
            "model_performance": {
                "input_tokens": 500,
                "duration_seconds": 10,
                "output_tokens": 100,
            },
        },
        {
            "variant": "optimized",
            "context_characters": 400,
            "model_performance": {
                "input_tokens": 200,
                "duration_seconds": 6,
                "output_tokens": 90,
            },
        },
    ]

    comparison = build_comparison(results)

    assert comparison["context_character_reduction_percent"] == 60.0
    assert comparison["input_token_reduction_percent"] == 60.0
    assert comparison["latency_reduction_percent"] == 40.0
