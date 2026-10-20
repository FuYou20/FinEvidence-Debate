const $ = (selector) => document.querySelector(selector);
const elements = {
  form: $("#debateForm"), topic: $("#topic"), start: $("#startButton"), error: $("#formError"),
  activeTopic: $("#activeTopic"), runBadge: $("#runBadge"), phase: $("#phaseMetric"), round: $("#roundMetric"),
  evidence: $("#evidenceMetric"), elapsed: $("#elapsedMetric"), truth: $("#truthStatus"), coverage: $("#coverageStatus"),
  debateId: $("#debateId"), support: $("#supportArguments"), oppose: $("#opposeArguments"), timeline: $("#timeline"),
  eventCount: $("#eventCount"), verdictTitle: $("#verdictTitle"), verdictSummary: $("#verdictSummary"),
  scores: $("#scoreBoard"), unresolved: $("#unresolvedIssues"), serviceDot: $("#serviceDot"), serviceText: $("#serviceText"),
};

let source = null;
let startedAt = null;
let timer = null;
let eventCount = 0;
const evidenceIds = new Set();

const stageNames = {
  job_queued: "任务已进入队列", job_started: "正在初始化研究 Agent", debate_started: "辩论已经开始",
  debate_completed: "交叉辩论完成", job_completed: "研究任务完成", job_failed: "研究任务失败",
};
const verdictNames = { support_preferred: "正方论证更充分", oppose_preferred: "反方论证更充分", mixed: "双方各有依据", insufficient: "证据不足，暂不裁决" };
const scoreNames = { evidence_quality: "证据质量", reasoning_quality: "推理质量", coverage: "信息覆盖", rebuttal_quality: "反驳质量", uncertainty_handling: "不确定性处理" };

function setRunStatus(status, text) {
  elements.runBadge.className = `run-badge ${status}`;
  elements.runBadge.querySelector("b").textContent = text;
}

function resetArena() {
  if (source) source.close();
  clearInterval(timer);
  eventCount = 0; evidenceIds.clear(); startedAt = Date.now();
  elements.timeline.replaceChildren(); elements.support.replaceChildren(empty("等待正方完成立论。"));
  elements.oppose.replaceChildren(empty("等待反方完成立论。"));
  elements.scores.replaceChildren(); elements.unresolved.replaceChildren();
  elements.verdictTitle.textContent = "等待裁判";
  elements.verdictSummary.textContent = "系统将结合双方论证和证据复核结果作出裁决。";
  elements.truth.textContent = "等待证据"; elements.coverage.textContent = "等待调查";
  elements.round.textContent = "0"; elements.evidence.textContent = "0"; elements.eventCount.textContent = "0 EVENTS";
  timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    elements.elapsed.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }, 1000);
}

function empty(text) { const p = document.createElement("p"); p.className = "empty-state"; p.textContent = text; return p; }
function node(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text != null) el.textContent = text; return el; }

function renderPosition(position) {
  const target = position.stance === "support" ? elements.support : elements.oppose;
  target.replaceChildren();
  if (!position.arguments.length) { target.append(empty("证据不足，未形成可交付论点。")); return; }
  position.arguments.forEach((argument) => {
    const card = $("#argumentTemplate").content.firstElementChild.cloneNode(true);
    card.querySelector(".argument-id").textContent = `${argument.argument_id} · v${argument.version}`;
    card.querySelector("h3").textContent = argument.claim;
    card.querySelector(".reasoning").textContent = argument.reasoning;
    const refs = card.querySelector(".evidence-refs");
    argument.evidence_ids.forEach((id) => refs.append(node("span", "", id)));
    if (argument.status === "withdrawn") card.style.opacity = ".55";
    target.append(card);
  });
}

function addTimeline(title, detail = "", kind = "", at = new Date().toISOString()) {
  const first = elements.timeline.querySelector(".timeline-empty"); if (first) first.remove();
  const li = node("li", kind); const time = node("time", "", new Date(at).toLocaleTimeString("zh-CN", { hour12: false }));
  li.append(time, node("strong", "", title)); if (detail) li.append(node("p", "", detail)); elements.timeline.append(li);
  elements.timeline.scrollTop = elements.timeline.scrollHeight;
}

function handleEvidence(bundle, at) {
  bundle.evidence.forEach((item) => { if (item.evidence_id) evidenceIds.add(item.evidence_id); });
  elements.evidence.textContent = String(evidenceIds.size);
  elements.coverage.textContent = bundle.status === "sufficient" ? "材料充分" : "仍有信息缺口";
  elements.truth.textContent = bundle.inconsistencies?.length ? "发现证据冲突" : bundle.source_checks?.length ? "已完成交叉核验" : "来源可追溯";
  const missing = (bundle.missing_information || []).join("\n");
  addTimeline(bundle.status === "sufficient" ? "证据达到交付要求" : "证据仍不充分", `${bundle.evidence.length} 条证据${missing ? `\n缺口：${missing}` : ""}`, "evidence", at);
}

function renderVerdict(verdict) {
  elements.verdictTitle.textContent = verdictNames[verdict.verdict] || verdict.verdict;
  elements.verdictSummary.textContent = verdict.summary;
  elements.scores.replaceChildren();
  Object.entries(scoreNames).forEach(([key, label]) => {
    const row = node("div", "score-row"); row.append(node("span", "", label), node("span", "", `${verdict.support_score[key]}/5`), node("span", "", `${verdict.oppose_score[key]}/5`)); elements.scores.append(row);
  });
  elements.unresolved.replaceChildren();
  if (verdict.unresolved_issues?.length) {
    const list = node("ul"); verdict.unresolved_issues.forEach((issue) => list.append(node("li", "", issue)));
    elements.unresolved.append(node("strong", "", "尚未解决的问题"), list);
  }
}

function handleEvent(name, event) {
  eventCount += 1; elements.eventCount.textContent = `${eventCount} EVENTS`;
  if (name === "stage_started") { elements.phase.textContent = event.label; addTimeline(event.label, "", "", event.at); }
  else if (name === "round_started") { elements.round.textContent = String(event.round_number); addTimeline(`进入第 ${event.round_number} 轮交叉质疑`, "", "challenge", event.at); }
  else if (name === "position_ready") { renderPosition(event.position); addTimeline(`${event.position.stance === "support" ? "正方" : "反方"}完成立论`, `${event.position.arguments.length} 项观点`, "", event.at); }
  else if (name === "evidence_started") addTimeline("证据调查启动", `${event.request.requester} · ${event.request.mode}`, "evidence", event.at);
  else if (name === "evidence_ready") handleEvidence(event.bundle, event.at);
  else if (name === "challenges_ready") addTimeline(`${event.stance === "support" ? "正方" : "反方"}提出质疑`, `${event.challenges.length} 条质疑`, "challenge", event.at);
  else if (name === "updates_ready") { renderPosition(event.position); addTimeline(`${event.position.stance === "support" ? "正方" : "反方"}更新观点`, event.updates.map((u) => `${u.action}: ${u.reason}`).join("\n") || "本轮无需修改", "challenge", event.at); }
  else if (name === "verdict_ready") { renderVerdict(event.verdict); addTimeline("裁判完成最终判断", verdictNames[event.verdict.verdict] || event.verdict.verdict, "verdict", event.at); }
  else if (stageNames[name]) { elements.phase.textContent = stageNames[name]; addTimeline(stageNames[name], "", name.includes("failed") ? "challenge" : "", event.at); }

  if (name === "job_completed") { setRunStatus("completed", "已完成"); elements.start.disabled = false; clearInterval(timer); source?.close(); }
  if (name === "job_failed") { setRunStatus("failed", "运行失败"); elements.start.disabled = false; elements.error.textContent = `任务执行失败：${event.error_type}`; clearInterval(timer); source?.close(); }
}

const eventNames = ["job_queued", "job_started", "debate_started", "stage_started", "evidence_started", "evidence_ready", "position_ready", "round_started", "challenges_ready", "updates_ready", "debate_completed", "judge_review_ready", "verdict_ready", "job_completed", "job_failed"];

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault(); elements.error.textContent = ""; resetArena(); elements.start.disabled = true; setRunStatus("running", "运行中");
  const payload = { topic: elements.topic.value.trim(), max_rounds: Number($("#maxRounds").value), max_challenges: Number($("#maxChallenges").value), max_verifications: Number($("#maxVerifications").value), max_deepening_steps: Number($("#maxDeepening").value) };
  elements.activeTopic.textContent = payload.topic;
  try {
    const response = await fetch("/api/debates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json(); if (!response.ok) throw new Error(result.detail?.[0]?.msg || result.detail || "创建任务失败");
    elements.debateId.textContent = result.debate_id;
    source = new EventSource(result.events_url);
    eventNames.forEach((name) => source.addEventListener(name, (message) => handleEvent(name, JSON.parse(message.data))));
    source.onerror = () => { if (!elements.start.disabled) return; elements.serviceText.textContent = "事件流正在重连"; };
  } catch (error) { setRunStatus("failed", "创建失败"); elements.error.textContent = error.message; elements.start.disabled = false; clearInterval(timer); }
});

fetch("/health").then((response) => { if (!response.ok) throw new Error(); elements.serviceDot.className = "state-dot online"; elements.serviceText.textContent = "FastAPI 服务正常"; }).catch(() => { elements.serviceDot.className = "state-dot offline"; elements.serviceText.textContent = "后端服务不可用"; });
