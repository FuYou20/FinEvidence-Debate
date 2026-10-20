# 多 Agent 记忆系统

## 目标

当前系统采用“三层状态管理”：频繁变化的工作状态留在进程内，已完成的证据请求使用 Redis 跨 Agent 复用，具有审计或恢复价值的业务状态写入 SQLite。模型本身不保存记忆，每次调用仍由业务代码按需构建上下文。

```text
工作记忆（Python / Pydantic）
  当前轮次、当前 Prompt、临时检索结果、步骤预算
                     │ 请求命中/回填
                     ▼
请求缓存（Redis）
  EvidenceBundle、TTL、索引配置命名空间、命中指标
                     │ miss 后执行调查
                     ▼
证据调查（FAISS / BM25 / Web）
                     │ 关键阶段落库
                     ▼
持久记忆（SQLite）
  运行、证据、观点版本、引用关系、质疑、调查轨迹、裁决、检查点
                     │ 完整展示
                     ▼
运行产物（JSON + HTML）
```

## 数据职责

- `debate_runs`：一次辩论的身份、主题、状态和当前阶段。
- `evidence_requests`：谁因为什么目的请求过证据，以及请求是否成功。
- `evidence`：按 `evidence_id` 去重的不可变证据快照。
- `run_evidence`：证据在哪一次运行中出现。
- `arguments`：每个观点的所有版本，修正不会覆盖历史版本。
- `argument_evidence`：观点版本与证据 ID 的多对多引用关系。
- `challenges`：质疑者、目标观点和调查问题。
- `challenge_evidence`：质疑直接针对的证据。
- `argument_updates`：保留、修正、撤回决定。
- `investigation_steps`：改写查询、联网补证和停止原因等调查轨迹。
- `verdicts`：最终结构化裁决。
- `run_events`：展示层和审计使用的有序事件流。
- `checkpoints`：关键阶段的 JSON 状态快照。

完整 JSON 保留原始协议对象，规范化字段用于查询和建立跨 Agent 关系。两者同时保存，避免为了查询牺牲审计信息。

## Redis 证据请求缓存

缓存键由调查模式、标准化问题、方向、关注点、已有证据 ID、深挖预算和索引配置共同计算。`request_id` 与 `requester` 不参与计算，因此不同 Agent 提出的同一语义请求可以复用结果。

```text
Agent 请求
  ├─ Redis hit  → 恢复 EvidenceBundle 并注入 EvidenceRegistry
  └─ Redis miss → 执行检索/联网调查 → 写 Redis → 返回结果
```

索引的 `corpus_fingerprint`、Embedding、切分配置和检索参数都进入缓存命名空间。语料或检索策略变化后会生成新命名空间，不会误用旧证据。

环境变量：

```dotenv
REDIS_URL=redis://:<password>@127.0.0.1:6379/0
EVIDENCE_CACHE_TTL_SECONDS=21600
EVIDENCE_CACHE_INSUFFICIENT_TTL_SECONDS=900
```

充分证据默认缓存 6 小时；证据不足结果只缓存 15 分钟，以便更快获取后来出现的新材料。Redis 连接失败时系统记录警告并安全退化为实时调查，不影响 SQLite、FAISS 和主流程。

可通过下面的参数临时关闭：

```bash
python -m src.scripts.multi_agent_demo --disable-evidence-cache
```

运行结果的 `performance.evidence_runtime` 中包含：

- `evidence_cache_hits`
- `evidence_cache_misses`
- `evidence_cache_writes`
- `evidence_cache_errors`
- `avoided_investigations`

## 运行方式

默认运行 Demo 时自动创建数据库：

```bash
python -m src.scripts.multi_agent_demo
```

默认位置：

```text
data/memory/agent_memory.sqlite3
```

可以显式指定：

```bash
python -m src.scripts.multi_agent_demo \
  --memory-db data/memory/demo.sqlite3
```

查看运行列表：

```bash
python -m src.scripts.inspect_memory
```

查看某次运行：

```bash
python -m src.scripts.inspect_memory --run-id <run_id>
```

`run_id` 同时写入结果 JSON，因而 JSON、HTML 和 SQLite 记录可以相互对应。

## 写入时机

系统不会把每个局部变量都写入数据库，而是在以下边界落库：

1. 证据请求开始、完成或失败；
2. 初始立论完成；
3. 质疑生成；
4. 观点保留、修正或撤回；
5. 辩论阶段完成；
6. 裁判完成；
7. 整次运行成功或失败。

每次数据库写入均使用事务。写入过程中发生异常时会整体回滚，避免只写入半条证据或半组引用关系。

## 当前边界

- 已具备持久保存、跨 Agent 引用、请求级缓存复用、审计查询和最近检查点读取。
- 工作流仍以进程内对象作为执行状态，不会在每个步骤查询数据库。
- 当前检查点为后续“断点恢复”提供数据基础，但尚未实现从任意中间步骤自动续跑；自动续跑需要进一步恢复 Agent 内部 ID 序列和待执行步骤。
- FAISS 向量继续保存在索引文件中，SQLite 保存业务记忆，不重复保存高维向量。
- 当前缓存可复用顺序到达的相同请求；多个进程同时首次请求同一问题时仍可能同时 miss，后续可以加入 Redis 分布式锁防止缓存击穿。
