"""将运行 JSON 生成为可离线阅读的 HTML；所有模型文本按纯文本转义。"""

from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from .debate_presenter import ACTION_NAMES, STOP_NAMES, VERDICT_NAMES


def _text(value):
    return escape(str(value if value is not None else ""), quote=True)


def _evidence_anchor(evidence_id):
    return "ev-" + sha256(str(evidence_id).encode("utf-8")).hexdigest()[:16]


def _refs(evidence_ids):
    return "".join(
        f'<a class="ref" href="#{_evidence_anchor(item)}">{_text(item)}</a>'
        for item in evidence_ids
    ) or '<span class="muted">暂无证据引用</span>'


def _list(items):
    return "<ul>" + "".join(f"<li>{_text(item)}</li>" for item in items) + "</ul>" if items else ""


def _argument(argument, *, final=False):
    withdrawn = argument.get("status") == "withdrawn"
    status = "已撤回" if withdrawn else ("最终观点" if final else "初始观点")
    return (
        f'<article class="argument {"withdrawn" if withdrawn else ""}">'
        f'<div class="eyebrow">{_text(argument["argument_id"])} <span>{status}</span></div>'
        f'<h3>{_text(argument["claim"])}</h3>'
        f'<p>{_text(argument["reasoning"])}</p>'
        f'<div class="refs">{_refs(argument.get("evidence_ids", []))}</div></article>'
    )


def _position(position, stance, *, final=False):
    label = "正方" if stance == "support" else "反方"
    arguments = position.get("arguments", [])
    return (
        f'<div class="side {stance}"><div class="side-heading">'
        f'<span class="dot"></span><h2>{label}</h2>'
        f'<span class="muted">{len(arguments)} 项观点</span></div>'
        + ("".join(_argument(item, final=final) for item in arguments)
           or '<p class="empty">没有形成可交付论点。</p>') + '</div>'
    )


def _bundle(bundle):
    sufficient = bundle.get("status") == "sufficient"
    steps = bundle.get("investigation_trace", [])
    actions = {"rewrite_query": "改写查询", "web_search": "联网补证", "multimodal": "多模态解析"}
    step_html = ""
    for step in steps:
        if "action" in step:
            step_html += (
                f'<li><strong>{_text(actions.get(step["action"], step["action"]))}</strong>'
                f'<span>新增 {_text(step.get("added_evidence_count", 0))} 条</span>'
                f'<p>{_text(step.get("reason", ""))}</p></li>'
            )
        elif "stop_reason" in step:
            step_html += f'<li class="muted">{_text(STOP_NAMES.get(step["stop_reason"], step["stop_reason"]))}</li>'
    return (
        '<details class="investigation"><summary>'
        f'<span class="badge {"good" if sufficient else "warn"}">'
        f'{"材料充分" if sufficient else "材料仍有缺口"}</span>'
        f'<span>{len(bundle.get("evidence", []))} 条证据 · 查看调查路径</span></summary>'
        f'<ol class="steps">{step_html}</ol>'
        + _list(bundle.get("missing_information", [])) + '</details>'
    )


def _round(round_record):
    body = ""
    for side, stance, label in (("supporting", "support", "正方"), ("opposing", "oppose", "反方")):
        challenges = round_record.get(f"{side}_challenges", [])
        cards = ""
        investigations = {
            item["challenge"]["challenge_id"]: item["evidence_bundle"]
            for item in round_record.get(f"{side}_investigations", [])
        }
        for challenge in challenges:
            kind = "质疑推理" if challenge["challenge_type"] == "claim" else "质疑证据"
            cards += (
                f'<article class="challenge"><div class="eyebrow">{kind} · '
                f'针对 {_text(challenge["target_argument_id"])}</div>'
                f'<h3>{_text(challenge["reason"])}</h3>'
                f'<p>调查问题：{_text(challenge["investigation_question"])}</p>'
                + (_bundle(investigations[challenge["challenge_id"]])
                   if challenge["challenge_id"] in investigations else "") + '</article>'
            )
        updates = ""
        for update in round_record.get(f"{side}_updates", []):
            updates += (
                f'<article class="update"><div class="eyebrow">'
                f'<span class="badge action-{_text(update["action"])}">{ACTION_NAMES.get(update["action"], "回应")}</span>'
                f' {_text(update["argument_id"])}</div><p>{_text(update["reason"])}</p>'
                + (f'<blockquote>{_text(update["revised_claim"])}</blockquote>' if update.get("revised_claim") else "")
                + '</article>'
            )
        body += (f'<div class="side {stance}"><h3 class="column-label">{label}提出的质疑</h3>'
                 + (cards or '<p class="empty">没有新的质疑。</p>')
                 + f'<h3 class="column-label">{label}对对方质疑的回应</h3>'
                 + (updates or '<p class="empty">本轮未调整观点。</p>') + '</div>')
    return (f'<section class="round"><div class="round-label">ROUND '
            f'{int(round_record["round_number"]):02d}</div><div class="two-columns">{body}</div></section>')


def _score_table(verdict):
    dimensions = [("evidence_quality", "证据质量"), ("reasoning_quality", "推理质量"),
                  ("coverage", "覆盖度"), ("rebuttal_quality", "反驳质量"),
                  ("uncertainty_handling", "不确定性处理")]
    rows = ""
    for key, label in dimensions:
        support = verdict["support_score"][key]
        oppose = verdict["oppose_score"][key]
        rows += (f'<tr><td>{label}</td><td><div class="score-cell">{support}/5 '
                 f'<meter min="0" max="5" value="{support}"></meter></div></td>'
                 f'<td><div class="score-cell">{oppose}/5 '
                 f'<meter min="0" max="5" value="{oppose}"></meter></div></td></tr>')
    support_total = sum(verdict["support_score"][key] for key, _ in dimensions)
    oppose_total = sum(verdict["oppose_score"][key] for key, _ in dimensions)
    return ('<table><thead><tr><th>论证质量</th><th>正方</th><th>反方</th></tr></thead>'
            f'<tbody>{rows}</tbody><tfoot><tr><td>合计</td><td>{support_total}/25</td>'
            f'<td>{oppose_total}/25</td></tr></tfoot></table>')


def _source(evidence_id, source):
    web = source.get("source_kind") == "web"
    if web:
        label = (
            "网页正文"
            if source.get("content_origin") == "web_fulltext"
            else "联网摘要"
        )
    else:
        label = "表格" if source.get("content_type") == "table" else "研报正文"
    title = source.get("title") or source.get("doc_id") or evidence_id
    location = source.get("url") if web else source.get("source_path", source.get("doc_id", ""))
    location_html = _text(location)
    if web and urlparse(str(location)).scheme.lower() in {"http", "https"}:
        location_html = f'<a href="{_text(location)}" target="_blank" rel="noopener noreferrer">{_text(location)}</a>'
    return (
        f'<details class="source" id="{_evidence_anchor(evidence_id)}"><summary>'
        f'<span class="badge">{label}</span><span>{_text(title)}</span></summary>'
        f'<div class="eyebrow">{_text(evidence_id)}</div><p class="location">{location_html}</p>'
        f'<p class="muted">日期：{_text(source.get("published_at") or "未提供")}'
        + (
            f' · 第 {_text(source.get("page_number"))} 页'
            if not web
            else (
                ' · 已抓取网页正文'
                if source.get("content_origin") == "web_fulltext"
                else ' · 搜索摘要，未取得网页正文'
            )
        )
        + f'</p><pre>{_text(source.get("text", ""))}</pre></details>'
    )


STYLE = """
:root{--bg:#f5f4ef;--paper:#fff;--ink:#172c37;--muted:#64737c;--line:#dce3df;--green:#137a63;--amber:#a36a1b}
*{box-sizing:border-box}html{scroll-padding-top:82px}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.8 ui-sans-serif,system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif}
a{color:var(--green);text-decoration:none}a:hover{text-decoration:underline}main{max-width:1250px;margin:auto;padding:42px 36px 80px}.masthead{display:flex;justify-content:space-between;color:var(--muted);letter-spacing:.15em;font-size:11px}.hero{padding:35px 0 28px;border-bottom:1px solid var(--line)}.eyebrow{font-size:11px;letter-spacing:.04em;color:var(--muted);overflow-wrap:anywhere}.eyebrow span{margin-left:8px}h1{font-size:clamp(25px,3vw,42px);line-height:1.4;max-width:920px;margin:14px 0 20px;letter-spacing:-.03em}h2{font-size:22px;margin:0}h3{font-size:16px;line-height:1.8;margin:8px 0}p{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere}.muted{color:var(--muted);font-size:13px}.badge{display:inline-flex;align-items:center;background:#edf1ef;border-radius:5px;padding:2px 9px;font-size:11px;white-space:nowrap}.good,.action-maintain{background:#e4f3ec;color:var(--green)}.warn,.action-revise{background:#fcf1dc;color:var(--amber)}.action-withdraw{background:#f9e9e7;color:#ab4b46}.verdict-label{font-size:13px;background:var(--ink);color:white;padding:5px 14px;border-radius:100px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:25px 0}.metric{padding:18px 22px;background:var(--paper);border:1px solid var(--line);border-radius:10px}.metric strong{display:block;font-size:30px;font-weight:600;line-height:1.3}.metric span{font-size:12px;color:var(--muted)}nav{position:sticky;top:0;background:#f5f4eff5;backdrop-filter:blur(12px);padding:15px 0;display:flex;gap:25px;border-bottom:1px solid var(--line);z-index:2;font-size:13px}nav a{color:var(--ink)}.section-title{display:flex;align-items:baseline;gap:16px;margin:45px 0 20px}.section-title small{font-size:11px;letter-spacing:.12em;color:var(--muted)}.two-columns{display:grid;grid-template-columns:1fr 1fr;gap:22px}.side-heading{display:flex;gap:10px;align-items:center;margin-bottom:16px}.side-heading .muted{margin-left:auto}.dot{width:9px;height:9px;border-radius:50%;background:var(--green)}.oppose .dot{background:var(--amber)}.argument,.challenge,.update{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:20px 23px;margin-bottom:13px}.support .argument{border-top:3px solid var(--green)}.oppose .argument{border-top:3px solid var(--amber)}.argument p{color:#4b5e66;font-size:14px}.withdrawn{opacity:.6}.refs{display:flex;flex-wrap:wrap;gap:6px;margin-top:16px}.ref{font:10px/1.8 ui-monospace,monospace;padding:2px 6px;border:1px solid var(--line);border-radius:4px;overflow-wrap:anywhere}.column-label{font-size:13px;color:var(--muted);margin:18px 0 10px}.round{margin-bottom:26px}.round-label{font:12px ui-monospace,monospace;letter-spacing:.18em;margin-top:25px;padding:12px 0;border-top:1px solid var(--line)}.investigation{border-top:1px dashed var(--line);padding-top:13px;margin-top:16px;font-size:13px}summary{cursor:pointer;list-style-position:inside}summary span{margin-right:8px}.steps{padding-left:20px;color:var(--muted)}.steps li{padding:5px 0}.steps span{margin-left:14px;font-size:12px}.steps p{font-size:12px}blockquote{border-left:3px solid var(--green);margin:15px 0 0;padding:8px 15px;background:#f2f7f4;font-size:14px}.decision{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:28px}.decision h3{font-size:23px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:12px 8px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:400}tfoot td{border:0;font-weight:700}.score-cell{display:flex;gap:12px;align-items:center}meter{width:70px;height:7px;accent-color:var(--green)}ul{padding-left:20px}.judge-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:30px}.source{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:16px 20px;margin-bottom:9px;scroll-margin-top:80px}.source:target{outline:2px solid var(--green)}.source .eyebrow{margin-top:15px}.location{font-size:12px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.9 ui-monospace,'Microsoft YaHei',monospace;background:#f5f6f3;border-radius:6px;padding:15px;max-height:400px;overflow:auto}input{width:100%;padding:13px 16px;margin-bottom:14px;border:1px solid var(--line);border-radius:8px;font:inherit;background:white}footer{border-top:1px solid var(--line);margin-top:45px;padding-top:18px;font-size:12px;color:var(--muted)}.empty{color:var(--muted);padding:15px;border:1px dashed var(--line);border-radius:8px}.timeline{border-left:2px solid var(--line);padding-left:18px}.timeline p{font-size:13px}.timeline strong{margin-right:10px}.mobile-scroll{overflow:auto}
@media(max-width:750px){main{padding:24px 18px}.two-columns,.judge-grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}nav{gap:18px;overflow:auto;white-space:nowrap}.masthead span:last-child{display:none}.decision{padding:20px}.metric strong{font-size:25px}}
@media print{nav,input{display:none}body{background:white}main{max-width:none;padding:0}.argument,.challenge,.update{break-inside:avoid}.source pre{max-height:none}.section-title{margin-top:25px}}
"""


def render_debate_html(record):
    if not isinstance(record, dict):
        raise TypeError("record 必须是 dict")
    debate = record.get("debate", {})
    verdict = record.get("verdict")
    evidence = record.get("evidence_registry", {})
    requests = record.get("evidence_requests", [])
    topic = record.get("topic") or debate.get("topic") or "研究辩论记录"
    web_count = sum(item.get("source_kind") == "web" for item in evidence.values())
    status = {"completed": "已完成", "running": "运行中", "failed": "运行失败"}.get(record.get("status"), "历史记录")
    title = VERDICT_NAMES.get(verdict["verdict"], verdict["verdict"]) if verdict else status
    metrics = "".join(f'<div class="metric"><strong>{value}</strong><span>{label}</span></div>' for value, label in [
        (len(debate.get("rounds", [])), "已完成辩论轮次"), (len(evidence), "可追溯证据快照"),
        (web_count, "联网摘要证据"), (len(requests), "证据调查请求"),
    ])
    initial = '<div class="two-columns">' + _position(debate.get("initial_supporting_position", {}), "support") + _position(debate.get("initial_opposing_position", {}), "oppose") + '</div>'
    rounds = "".join(_round(item) for item in debate.get("rounds", [])) or '<p class="empty">尚无交叉辩论记录。</p>'
    final = '<div class="two-columns">' + _position(debate.get("supporting_position", {}), "support", final=True) + _position(debate.get("opposing_position", {}), "oppose", final=True) + '</div>'
    judgment = '<p class="empty">裁判结果尚未生成。</p>'
    if verdict:
        assessments = "".join(
            f'<details class="investigation"><summary>{_text(item["argument_id"])} · '
            f'{_text({"well_supported":"支撑充分","partially_supported":"部分支撑","unsupported":"缺乏支撑","unverifiable":"无法核实"}.get(item["assessment"],item["assessment"]))}</summary>'
            f'<p>{_text(item["reason"])}</p>{_refs(item.get("accepted_evidence_ids", []))}'
            f'{_list(item.get("unresolved_issues", []))}</details>' for item in verdict.get("argument_assessments", [])
        )
        judgment = (f'<div class="decision"><div class="judge-grid"><div><div class="eyebrow">FINAL ASSESSMENT</div>'
                    f'<h3>{_text(title)}</h3><p>{_text(verdict["summary"])}</p>'
                    f'{_list(verdict.get("decisive_reasons", []))}</div><div class="mobile-scroll">{_score_table(verdict)}</div></div>'
                    f'{assessments}<h3 class="column-label">仍未解决的问题</h3>'
                    + (_list(verdict.get("unresolved_issues", [])) or '<p class="muted">裁判未额外列出。</p>') + '</div>')
    request_timeline = ""
    for item in requests:
        request = item.get("request", {})
        result = item.get("result")
        label = "证据复核" if request.get("mode") == "verify_evidence" else "定向调查"
        request_timeline += f'<div class="timeline"><p><strong>{_text(request.get("requester"))}</strong>{label}</p>'
        if result:
            request_timeline += _bundle(result)
        else:
            request_timeline += f'<p class="muted">{_text(item.get("error_type", "等待返回"))}</p>'
        request_timeline += '</div>'
    sources = "".join(_source(key, value) for key, value in evidence.items())
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>研究辩论 · {_text(topic)}</title><style>{STYLE}</style></head><body><main>'
            '<div class="masthead"><span>RESEARCH ARENA / EVIDENCE FIRST</span><span>DEBATE RECORD</span></div>'
            f'<header class="hero"><div class="eyebrow">多 AGENT 研究辩论 · {_text(status)}</div>'
            f'<h1>{_text(topic)}</h1><span class="verdict-label">{_text(title)}</span></header>'
            f'<div class="metrics">{metrics}</div><nav><a href="#opening">初始立论</a><a href="#rounds">交叉辩论</a>'
            '<a href="#final">最终观点</a><a href="#judge">裁判</a><a href="#evidence">证据库</a></nav>'
            f'<section id="opening"><div class="section-title"><small>01 / OPENING</small><h2>双方如何立论</h2></div>{initial}</section>'
            f'<section id="rounds"><div class="section-title"><small>02 / CROSS EXAMINATION</small><h2>质疑、调查与修正</h2></div>{rounds}</section>'
            f'<section id="final"><div class="section-title"><small>03 / FINAL POSITIONS</small><h2>辩论后保留的观点</h2></div>{final}</section>'
            f'<section id="judge"><div class="section-title"><small>04 / JUDGMENT</small><h2>裁判如何判断</h2></div>{judgment}</section>'
            '<details class="investigation"><summary>展开全部证据调查轨迹</summary>'
            f'{request_timeline}</details><section id="evidence"><div class="section-title"><small>05 / SOURCES</small><h2>每条证据都可回溯</h2></div>'
            f'<input id="search" type="search" aria-label="搜索证据" placeholder="搜索证据 ID、标题、来源或正文…"><div id="sources">{sources}</div>'
            '<p id="empty-search" class="muted" hidden>没有匹配的证据。</p></section>'
            '<footer>由已保存的运行 JSON 生成。展示分数反映本次论证评价；联网材料会标注网页正文或搜索摘要来源。多模态与金融数据接口未启用。</footer></main>'
            '<script>document.getElementById("search").addEventListener("input",function(){let n=0;const q=this.value.toLowerCase();document.querySelectorAll("#sources .source").forEach(e=>{e.hidden=!e.textContent.toLowerCase().includes(q);if(!e.hidden)n++});document.getElementById("empty-search").hidden=n>0});'
            'function reveal(){const e=document.getElementById(location.hash.slice(1));if(e&&e.classList.contains("source")){e.open=true;e.hidden=false}}window.addEventListener("hashchange",reveal);reveal();</script></body></html>')


def write_debate_report(record, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_debate_html(record), encoding="utf-8")
    return path
