"""终端展示与 JSON 回放。仅消费展示事件，不调用模型。"""

import shutil
import sys
import time
import unicodedata


STANCE_NAMES = {"support": "正方", "oppose": "反方"}
ACTION_NAMES = {"maintain": "保留", "revise": "修正", "withdraw": "撤回"}
VERDICT_NAMES = {
    "support_preferred": "正方论证更充分", "oppose_preferred": "反方论证更充分",
    "mixed": "双方各有依据", "insufficient": "证据不足，暂不裁决",
}
STOP_NAMES = {
    "evidence_sufficient": "证据达到当前交付要求",
    "max_deepening_steps": "已达调查预算，仍有缺口",
    "no_progress": "未获得新的有效材料",
    "action_unavailable": "所需能力暂未启用",
    "action_failed": "外部能力调用失败",
}


def _wrap(text, width):
    """按显示宽度折行，避免中文被 textwrap 当作半角字符。"""
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        line, used = "", 0
        for char in paragraph:
            size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
            if used + size > width:
                lines.append(line)
                line, used = "", 0
            line += char
            used += size
        lines.append(line)
    return lines


class DebatePresenter:
    def __init__(self, *, file=None, color=None):
        self.file = file if file is not None else sys.stdout
        if (
            self.file is sys.stdout
            and hasattr(self.file, "reconfigure")
            and str(getattr(self.file, "encoding", "")).lower() not in {"utf-8", "utf8"}
        ):
            self.file.reconfigure(encoding="utf-8", errors="replace")
        self.color = self.file.isatty() if color is None else color
        self.started = time.monotonic()
        self.width = min(104, max(50, shutil.get_terminal_size((96, 24)).columns - 2))

    def line(self, text="", *, color=None):
        if self.color and color:
            text = f"\033[{color}m{text}\033[0m"
        print(text, file=self.file, flush=True)

    def paragraph(self, text, *, indent="  "):
        for line in _wrap(text, self.width - len(indent)):
            self.line(indent + line)

    def section(self, title, *, color="36"):
        self.line()
        self.line("─" * self.width, color="90")
        self.line(f"  {title}", color=color)
        self.line("─" * self.width, color="90")

    def handle_event(self, event):
        name = event["name"]
        if name == "debate_started":
            self.section("RESEARCH ARENA  /  证据驱动研究辩论", color="1;36")
            self.paragraph(event["topic"])
            config = event.get("config", {})
            if config:
                self.line(f"  预算：{config.get('max_rounds', '—')} 轮 · 每方每轮最多 "
                          f"{config.get('max_challenges_per_agent_per_round', '—')} 条质疑")
            else:
                self.line("  历史运行记录回放 · 不调用模型")
        elif name == "stage_started":
            elapsed = int(time.monotonic() - self.started)
            self.line(f"\n  … {event['label']}  ·  累计 {elapsed // 60:02d}:{elapsed % 60:02d}", color="90")
        elif name == "round_started":
            self.section(f"ROUND {event['round_number']:02d}  /  交叉质疑与证据回应")
        elif name == "position_ready":
            self.position(event["position"])
        elif name == "challenges_ready":
            stance = event["stance"]
            title = STANCE_NAMES.get(stance, stance)
            self.line(f"\n  {title}提出的质疑", color="32" if stance == "support" else "33")
            if not event["challenges"]:
                self.paragraph("本轮没有新的质疑。")
            for challenge in event["challenges"]:
                kind = "观点推理" if challenge["challenge_type"] == "claim" else "证据真实性"
                self.paragraph(f"[{kind}] → {challenge['target_argument_id']}")
                self.paragraph(challenge["reason"])
                self.paragraph("调查问题：" + challenge["investigation_question"])
        elif name == "updates_ready":
            position = event["position"]
            self.line(f"\n  {STANCE_NAMES.get(position['stance'])}的观点更新", color="36")
            arguments = {item["argument_id"]: item for item in position["arguments"]}
            if not event["updates"]:
                self.paragraph("本轮未调整观点。")
            for update in event["updates"]:
                self.paragraph(f"[{ACTION_NAMES[update['action']]}] {update['argument_id']}")
                self.paragraph("原因：" + update["reason"])
                if update["action"] == "revise":
                    self.paragraph("修正后：" + arguments[update["argument_id"]]["claim"])
        elif name == "evidence_started":
            request = event["request"]
            label = "证据复核" if request["mode"] == "verify_evidence" else "定向调查"
            self.line(f"  ↳ {label} · {request['requester']}", color="90")
        elif name == "evidence_ready":
            bundle = event["bundle"]
            label = "材料充分" if bundle["status"] == "sufficient" else "材料仍有缺口"
            self.line(f"  ↳ {label} · {len(bundle['evidence'])} 条证据",
                      color="32" if bundle["status"] == "sufficient" else "33")
            for step in bundle.get("investigation_trace", []):
                if "action" in step:
                    action = {"rewrite_query": "改写查询", "web_search": "联网补证", "multimodal": "多模态解析"}.get(step["action"], step["action"])
                    self.paragraph(f"{action} · 新增 {step.get('added_evidence_count', 0)} 条", indent="      ")
            for missing in bundle.get("missing_information", []):
                self.paragraph("缺口：" + missing, indent="      ")
        elif name == "judge_review_ready":
            self.paragraph(f"裁判提出 {event['requested']} 项复核需求，本次最多执行 {event['allowed']} 项。")
        elif name == "verdict_ready":
            self.verdict(event["verdict"])
        elif name == "debate_completed":
            self.line(f"\n  辩论完成 · {event['rounds']} 轮", color="90")

    def position(self, position):
        stance = position["stance"]
        self.section(f"{STANCE_NAMES.get(stance, stance)}立论", color="32" if stance == "support" else "33")
        if not position["arguments"]:
            self.paragraph("证据不足，未提出可交付论点。")
        for index, argument in enumerate(position["arguments"], 1):
            self.line(f"\n  {index:02d}  {argument['argument_id']}", color="1")
            self.paragraph(argument["claim"])
            self.paragraph("论证：" + argument["reasoning"])
            self.paragraph("证据：" + " · ".join(argument["evidence_ids"]))

    def verdict(self, verdict):
        self.section("VERDICT  /  裁判结论", color="1;36")
        self.paragraph(VERDICT_NAMES.get(verdict["verdict"], verdict["verdict"]))
        self.line()
        self.line("  评分维度                 正方      反方")
        for key, label in [("evidence_quality", "证据质量"), ("reasoning_quality", "推理质量"),
                           ("coverage", "覆盖度"), ("rebuttal_quality", "反驳质量"),
                           ("uncertainty_handling", "不确定性处理")]:
            self.line(f"  {label}{' ' * (23 - len(label) * 2)}{verdict['support_score'][key]}/5       {verdict['oppose_score'][key]}/5")
        support = sum(verdict["support_score"].values())
        oppose = sum(verdict["oppose_score"].values())
        self.line(f"  合计                     {support}/25     {oppose}/25", color="1")
        self.line()
        self.paragraph(verdict["summary"])
        for reason in verdict.get("decisive_reasons", []):
            self.paragraph("依据：" + reason)
        for issue in verdict.get("unresolved_issues", []):
            self.paragraph("尚未解决：" + issue)

    def replay(self, record):
        debate = record.get("debate", {})
        self.handle_event({"name": "debate_started", "topic": record.get("topic", debate.get("topic", "研究辩论回放"))})
        for key in ("initial_supporting_position", "initial_opposing_position"):
            if key in debate:
                self.position(debate[key])
        for round_record in debate.get("rounds", []):
            self.handle_event({"name": "round_started", "round_number": round_record["round_number"]})
            for side, stance in (("supporting", "support"), ("opposing", "oppose")):
                self.handle_event({"name": "challenges_ready", "stance": stance,
                                   "challenges": round_record[f"{side}_challenges"]})
                for investigation in round_record[f"{side}_investigations"]:
                    self.handle_event({"name": "evidence_ready", "bundle": investigation["evidence_bundle"]})
                self.line(f"\n  {STANCE_NAMES[stance]}的回应", color="36")
                for update in round_record[f"{side}_updates"]:
                    self.paragraph(f"[{ACTION_NAMES[update['action']]}] {update['argument_id']}")
                    self.paragraph(update["reason"])
                    if update.get("revised_claim"):
                        self.paragraph("修正后：" + update["revised_claim"])
        if record.get("verdict"):
            self.verdict(record["verdict"])

    def saved(self, json_path, html_path):
        self.section("运行记录已保存", color="32")
        self.paragraph(f"JSON：{json_path}")
        self.paragraph(f"HTML：{html_path}")
