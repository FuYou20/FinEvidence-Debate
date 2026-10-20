from copy import deepcopy
from io import StringIO
import json
from types import SimpleNamespace

from src.debate_presenter import DebatePresenter
from src.debate_report import render_debate_html


def make_record():
    argument = {
        "argument_id": "A1", "claim": "本土化带来有限优势",
        "reasoning": "历史材料只说明历史时期", "evidence_ids": ["E1"],
        "status": "active",
    }
    position = {"agent_id": "support-agent", "stance": "support", "arguments": [argument]}
    score = dict.fromkeys([
        "evidence_quality", "reasoning_quality", "coverage",
        "rebuttal_quality", "uncertainty_handling",
    ], 3)
    return {
        "topic": "竞争优势是否持续", "status": "completed",
        "debate": {
            "initial_supporting_position": position,
            "initial_opposing_position": {"stance": "oppose", "arguments": []},
            "supporting_position": position,
            "opposing_position": {"stance": "oppose", "arguments": []},
            "rounds": [],
        },
        "verdict": {"verdict": "mixed", "summary": "双方各有有限依据",
                    "support_score": score, "oppose_score": score},
        "evidence_registry": {"E1": {"doc_id": "doc1", "source_kind": "local",
                                     "page_number": 15, "text": "原文证据"}},
        "evidence_requests": [],
    }


def test_report_preserves_evidence_navigation_and_does_not_mutate_record():
    record = make_record()
    before = deepcopy(record)
    html = render_debate_html(record)

    assert record == before
    assert "竞争优势是否持续" in html
    assert 'href="#ev-' in html
    assert 'id="ev-' in html
    assert "原文证据" in html
    assert "15/25" in html


def test_model_text_and_source_urls_cannot_inject_html():
    record = make_record()
    record["topic"] = '<img src=x onerror="alert(1)">'
    record["evidence_registry"]["E1"].update({
        "source_kind": "web", "url": "javascript:alert(1)",
        "text": "<script>alert('secret')</script>",
    })
    html = render_debate_html(record)

    assert '<img src=x' not in html
    assert "<script>alert('secret')</script>" not in html
    assert '&lt;script&gt;' in html
    assert 'href="javascript:' not in html


def test_plain_console_replay_shows_both_sides_and_verdict():
    stream = StringIO()
    presenter = DebatePresenter(file=stream, color=False)
    presenter.replay(make_record())
    text = stream.getvalue()

    assert "正方立论" in text
    assert "反方立论" in text
    assert "裁判结论" in text
    assert "双方各有依据" in text
    assert "\033[" not in text


def test_replay_does_not_call_live_dependencies(monkeypatch, tmp_path):
    import src.scripts.debate_replay as demo

    path = tmp_path / "record.json"
    original = json.dumps(make_record(), ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        demo,
        "parse_args",
        lambda: SimpleNamespace(record=path, no_color=True),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("回放不能初始化真实客户端或加载索引")

    monkeypatch.setattr(
        demo,
        "load_record",
        lambda record_path: json.loads(
            record_path.read_text(encoding="utf-8")
        ),
    )
    demo.main()

    assert path.with_suffix(".html").is_file()
    assert path.read_text(encoding="utf-8") == original
