# FinEvidence FastAPI 辩论服务

## 服务关系

先启动证据 A2A 服务，再启动辩论 API：

```bash
python -m src.scripts.a2a_evidence_server
python -m src.scripts.debate_api_server
```

默认地址分别为 `127.0.0.1:9999` 和 `127.0.0.1:8000`。

浏览器打开 `http://127.0.0.1:8000/` 即可使用辩论过程看板；
FastAPI 自动接口文档位于 `/docs`，OpenAPI Schema 位于
`/openapi.json`。前端由同一 FastAPI 进程托管，不需要安装 Node.js
或启动单独的前端开发服务器。

## 创建任务

```http
POST /api/debates
Content-Type: application/json

{
  "topic": "传音控股在非洲市场的竞争优势是否具有持续性？",
  "max_rounds": 1,
  "max_challenges": 1,
  "max_verifications": 1,
  "max_deepening_steps": 1,
  "supporting_focus": ["渠道", "本地化"],
  "opposing_focus": ["竞争加剧", "时效性"]
}
```

响应状态为 `202 Accepted`，并返回 `debate_id`、状态、事件和结果 URL。

## 订阅事件

```http
GET /api/debates/{debate_id}/events
Accept: text/event-stream
```

服务采用标准 SSE 格式，每个事件包含递增 `id`、事件名称和 JSON `data`。客户端断线重连时可以使用 `Last-Event-ID`，也可在调试时使用 `?after=12`。

主要事件包括：

- `job_queued`、`job_started`、`job_completed`、`job_failed`
- `debate_started`、`stage_started`、`round_started`
- `evidence_started`、`evidence_ready`
- `position_ready`、`challenges_ready`、`updates_ready`
- `judge_review_ready`、`verdict_ready`

## 状态与结果

```http
GET /api/debates/{debate_id}
GET /api/debates/{debate_id}/result
```

运行中访问结果接口返回 `202`，完成返回 `200`，失败返回 `409`。结果包含辩论记录、裁判结论、证据快照、A2A 请求轨迹和调用指标。

## 当前边界

任务状态暂存在 API 进程内存中，服务重启后不会恢复。SQLite/LangGraph checkpointer 的断点恢复将在后续阶段接入。默认只监听回环地址，不提供公网认证。
