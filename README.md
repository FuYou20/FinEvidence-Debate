# FinEvidence-Debate

证据驱动的金融研究多 Agent 辩论系统。项目基于 LangGraph 编排正方研究、反方研究和裁判 Agent，通过“立论—交叉质疑—观点修正—裁决”工作流生成包含证据引用、争议焦点和不确定性说明的结构化研究结论。

证据获取由独立的 FinEvidence A2A 服务负责，本仓库只关注辩论编排、观点演化、裁判机制、记忆管理和 Web 过程展示。

## 系统结构

```text
研究议题
   ↓
LangGraph 辩论状态机
   ├─ 正方研究 Agent
   ├─ 反方研究 Agent
   └─ 多轮交叉质疑与观点更新
   ↓
裁判 Agent
   ↓
结构化裁决、评分与未解决问题
```

## 核心设计

- 将辩论建模为显式 LangGraph 状态图，统一管理执行顺序、轮数、共享状态和停止条件。
- 使用“观点＋论据＋证据 ID”的 Pydantic 协议，支持观点保留、修正与撤回，并记录完整演化链路。
- 裁判从证据质量、推理质量、信息覆盖、反驳有效性和不确定性处理等维度评分；证据不足时输出 `insufficient`。
- 使用 LangGraph State 保存工作记忆，使用 SQLite 持久化观点版本、质疑、引用关系、事件和阶段 Checkpoint。
- 通过有效观点筛选、证据去重、旧轮次摘要和关键证据优先保留控制上下文。对照实验中平均输入 Token 降低 62.08%，总 Token 降低 57.19%，关键观点与引用保持完整。
- 通过 A2A Client 调用独立证据服务，使辩论逻辑与证据获取实现解耦。

## Web 展示

FastAPI 提供异步任务接口，SSE 将执行事件持续推送至原生 HTML/CSS/JavaScript 页面。页面实时展示双方观点、证据数量、交叉质疑、运行轨迹、裁判评分和未解决问题。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

先启动兼容 A2A 的证据服务，再启动辩论 API：

```bash
python -m src.scripts.debate_api_server
```

浏览器访问：

- 页面：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`

运行测试：

```bash
pytest -q
```

## 当前边界

- FastAPI 任务队列当前保存在进程内存中，服务重启后不能自动恢复运行中的任务。
- SQLite 已保存阶段 Checkpoint，但尚未接入工作流自动续跑。
- 调查质量依赖外部证据服务返回的证据覆盖度与真实性。
