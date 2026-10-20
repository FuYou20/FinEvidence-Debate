# 多 Agent 真实运行入口

在项目根目录 `03_rag` 执行：

```bash
python -m src.scripts.multi_agent_demo \
  "仅分析2022年研报中的传音非洲竞争优势及其持续性，不推断最新市场情况" \
  --max-rounds 1 --max-challenges 1 --max-verifications 1
```

默认执行一轮辩论，每方最多一次质疑、裁判最多一次复核，每次调查最多一次
深入动作。可用 `--max-deepening-steps 2` 放宽调查预算。
命令行会输出调查阶段和最终裁决，完整记录
保存在 `data/runs/debate_时间戳.json`。每次调查完成也会保存中间结果。

## 已接入的依赖

| 能力 | 实现 | 配置 |
|---|---|---|
| Dense + BM25 检索 | `evidence_runtime.py` 调用 `hybrid_retrieve`，按配置扩展邻居 chunk | 已构建的 `data/cache/retrieval_index` |
| 查询向量化 | 现有 Qwen Embedding 适配器 | `DASHSCOPE_API_KEY`、`QWEN_EMBEDDING_BASE_URL` |
| Query Rewrite | `query_rewriter.py`，Responses API 结构化输出 | `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` |
| 充分性判断 | `evidence_judge.py` + `create_evidence_decision_generator` | 同上 |
| 联网交叉检索 | `web_search.py`，博查搜索适配器 | `BOCHA_API_KEY` |
| 正反研究辩论 | `debate_agent.py` + `debate_orchestrator.py` | 同上模型配置 |
| 裁判 | `debate_judge.py`，初审、受限复核、终审 | 同上模型配置 |

`--disable-web` 可以关闭博查。多模态解析保持 `None`，金融数据接口尚未启用。
判断器会看到当前可用动作；如果仍选择不可用动作，工作流以
`action_unavailable` 停止并返回部分证据，状态为 `insufficient`。

## 证据注册与查证

```text
本地检索 / 网页搜索
        ↓
统一证据字典 + 来源信息
        ↓
EvidenceRegistry.register()
        ↓
稳定 evidence_id + 不可变快照
        ↓
研究 Agent 观点引用 / 裁判复核请求
        ↓
EvidenceRegistry.get_many()
        ↓
原始证据作为 initial_evidence 进入调查工作流
        ↓
按需改写查询或联网补证
```

本地证据以 `doc_id + chunk_id` 标识。一次运行使用固定索引；同一个 ID 对应
不同内容时抛出异常，避免悄悄覆盖已经被引用的证据。分数、排名、embedding
等检索临时字段不会进入证据快照。

网页证据按 URL、摘要及来源元数据生成快照 ID。同一 URL 返回不同内容时保留
两个版本，不改动此前的引用。来源 URL 与发布时间会进入模型上下文；抓取日期
单独保存在 `crawled_at`，不会补充为发布日期。

当前注册表的生命周期是一场运行。JSON 输出中的 `evidence_registry` 保存了
本场证据快照，`evidence_requests` 保存请求、返回状态与调查步骤。

## 当前边界

- 网页证据来自博查摘要，标记为 `content_origin=search_summary`。系统尚未抓取
  网页全文，不应将“搜到摘要”声称为“核验了原文”。
- 本地证据来自已有 PDF 文本/表格抽取，原文件路径和页码用于回溯；尚未进行
  原始页面的视觉核对。
- 查证结果保持中立：充分或不足。论点是否得到支持由研究方和裁判评价。
- 已知搜索网络/HTTP 错误转换为 `action_failed`，返回已有证据与失败原因；
  代码缺陷和模型调用异常向上传播，命令行入口保存失败记录后报错。
- 结构化输出和 Pydantic 只校验数据结构。观点的可靠性仍需通过真实案例评估。

## 单独检测联网能力

```bash
python -m src.scripts.probe_web_search
```

探测只打印结果数与 URL，失败时只打印状态码和可重试标志，不打印密钥。

结构化生成器按照 [OpenAI Structured Outputs 文档](https://developers.openai.com/api/docs/guides/structured-outputs)
使用 `responses.parse(..., text_format=模型)`；兼容服务是否接受该协议由实际联调验证。

## 展示与回放

终端依次展示双方立论、每轮质疑、证据调查、观点保留/修正/撤回和裁判评分。
颜色自动适配交互终端，`--no-color` 可关闭颜色。

每次保存 JSON 时，同时更新同名 HTML。报告可直接用浏览器打开，无需服务端、
第三方前端库或外部字体，包含正反方分栏、观点演变、评分表、可展开的调查记录
以及可搜索的证据库；点击论点下的证据 ID 可跳转并展开原文。

回放已有真实记录，不调用模型，也不需要 API 密钥：

```bash
python -m src.scripts.multi_agent_demo \
  --replay data/runs/debate_20261016_205902.json
```

`run_debate(..., on_event=callback)` 和 `judge(..., on_event=callback)` 可注入
可选的展示监听器。监听器获得数据副本；展示逻辑与研究判断逻辑分离。
