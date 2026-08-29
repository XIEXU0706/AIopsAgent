# AIOps_Agent

> 面向智能运维告警处理场景，集 **多 Agent 智能编排**、**日志故障根因分析**、**RAG 故障知识检索**、**安全审查与风险预警**、**自动化报表导出** 以及 **AI 智能问答** 于一体的智能运维分析平台。

接收监控告警 → 多 Agent 并行分析（日志分析 / 知识检索）→ Coordinator 跨 Agent 归纳 → Safety 安全护栏审查 → 输出处置报告、预警通知与工作交接摘要。配置 LLM Key 走智能分析；**未配置时全链路规则降级，系统仍可运行**。

---

## 核心特性

- **多 Agent 协作（HEARSAY-II 黑板模式）**：以 `CollaborationBlackboard` 为共享协作空间，Coordinator 发布任务、Specialist Agent 按能力原子认领（`asyncio.Lock` 保证不重复执行）、执行后以 Artifact 写回黑板并广播事件，Agent 间完全解耦、可动态扩展。
- **安全护栏（Guardrails）双层防护**：`SafetyAgent` 第一层 17 条正则规则硬拦截高危操作（DROP / TRUNCATE / DELETE / rm -rf / flushall 等），第二层 LLM 语义级风险评级捕获「回滚版本、跨 AZ 切换」等隐式高危意图，高危操作强制人工复核。
- **处置预案动态加载**：`AIOpsSkillRegistry` 扫描 `SKILL.md` 按告警类型（error_type）匹配处置计划，**新增预案只需放一个 markdown 文件，不用改代码**；高风险场景自动叠加安全审查，并生成结构化交接摘要。
- **RAG 故障知识检索（双 embedding 后端可切换）**：`RetrievalAgent` 基于 Chroma 向量库检索历史故障案例；`EMBEDDING_BACKEND=bge` 启用 `bge-small-zh-v1.5` 语义向量（512 维，CPU 可跑，query 侧加 bge 官方检索指令前缀提升召回），未安装 sentence-transformers 时自动降级为中文 2-gram 哈希 embedding（`HashingEmbeddingFunction`，特征哈希 + L2 归一化，无模型、无网络依赖开箱即用）；统一 `encode_documents / encode_query` 接口，新增后端（如 DashScope 云端 embedding）只需实现同一接口；独立改写评测集对比实验见 `scripts/rag_eval.py`。
- **Prometheus Alertmanager 对接**：`POST /api/v1/webhook/alertmanager` 接收 Alertmanager v4 标准推送，labels/annotations 全量保留供检索 Agent 提取关键词；resolved 通知自动过滤、重复 fingerprint 幂等受理；`scripts/mock_alertmanager.py` 可模拟真实告警源持续推送。
- **MCP 工具异步队列**：报告导出（JSON/Markdown/Excel .xlsx）、预警发送（落库 + 可选 Webhook 真实推送）、备注追加等工具经 `AsyncToolQueue` 异步入队、后台消费，主流程不阻塞；队列支持**幂等去重、令牌桶限流、指数退避重试、失败隔离与 Dead Letter Queue**，任务状态经 **SSE 实时推送**前端。
- **分层记忆 + 上下文压缩**：短期 Redis 热窗口 + 长期 MySQL（`chat_kv` 表 + 联合索引）+ SQLite 兜底；长对话经「保留最近 N 条 + 摘要压缩」生成 `memoryBrief`，避免 prompt 膨胀，支持可配置归档。
- **全链路 LLM 规则降级**：日志分析、知识检索、跨 Agent 归纳、安全评级在 LLM 不可用时均自动降级为规则实现，系统在无 Key 环境下开箱即用。
- **前后端完整交付**：FastAPI 后端 + Vue 前端（告警中心 / 处置详情 / 知识库 / 智能问答 / 可视化大屏）。

---

## 架构图

```mermaid
flowchart TB
    AL[监控告警 / 对话请求] --> API[FastAPI 服务层<br/>app/main.py]
    API --> HARNESS[AIOpsAgentHarness<br/>输入脱敏 · 编排 · 报告 · trace]

    subgraph ORCH["协作中枢"]
        RT[AgentRuntime<br/>Agent 注册 / 异常兜底]
        CB[(CollaborationBlackboard<br/>HEARSAY-II 黑板)]
        RT <--> CB
    end

    HARNESS --> RT
    HARNESS -->|发布子任务| CB

    subgraph AGENTS["Agent 层"]
        COORD[CoordinatorAgent<br/>任务拆解 · LLM 归纳 · 交接摘要]
        LOG[LogAnalyzeAgent<br/>LLM 日志分析 + 正则降级]
        RET[RetrievalAgent<br/>Chroma 检索 + 关键词降级]
        SAFE[SafetyAgent<br/>17 规则硬拦截 + LLM 语义评级]
    end

    CB -->|log_analysis| LOG
    CB -->|knowledge_retrieval| RET
    LOG -->|产物回写| CB
    RET -->|产物回写| CB
    COORD --> SAFE
    HARNESS --> COORD

    subgraph SUPPORT["支撑层"]
        LLM[DeepSeek / Kimi Client]
        SKILL[AIOpsSkillRegistry<br/>SKILL.md 动态加载]
        TOOL[AsyncToolQueue<br/>报告导出 · Excel · 预警 · 备注]
        MEM[HierarchicalMemory<br/>Redis + MySQL + SQLite 兜底]
        RAG[(Chroma 故障知识库)]
    end

    LOG --- LLM
    COORD --- LLM
    SAFE --- LLM
    RET --- RAG
    COORD --- MEM
    HARNESS --> SKILL
    HARNESS --> TOOL
```

---

## 一次告警处置流程

1. **接入**：告警进入 Harness，先做输入脱敏（IP → `x.x.x.x`、Host 头 → `<sanitized>`），再构建 `ExecutionContext`（含全链路 `trace_id`）。
2. **拆解**：Coordinator 在黑板上发布 `log_analysis` / `knowledge_retrieval` 子任务。
3. **并行分析**：`LogAnalyzeAgent` 解析日志输出根因 / error_type / 建议动作；`RetrievalAgent` 检索历史故障案例。两者均带 30s 超时保护与规则降级。
4. **归纳**：Coordinator 收集黑板产物，调用 LLM 做跨 Agent 根因关联与处置优先级排序，LLM 失败则规则拼接兜底。
5. **安全护栏**：`SafetyAgent` 先正则硬拦截，未命中再 LLM 语义评级；高危操作被拦截后 Coordinator 最多 2 轮修订处置计划。
6. **输出**：生成处置报告（摘要 / 处置计划 / 相关案例 / 交接摘要），同时将报告导出、Excel 导出、预警发送、备注追加入队异步执行，任务状态经 SSE 推送前端。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3 · FastAPI · asyncio |
| LLM | DeepSeek API（OpenAI 兼容）· Kimi（Moonshot） |
| 检索 | Chroma 向量库 · 关键词匹配降级 |
| 存储 | SQLite（默认）· Redis · MySQL |
| 前端 | Vue 2.7 · Element UI · ECharts · axios |
| 工程 | pytest · uvicorn |

---

## 快速开始

```bash
# 1. 克隆并安装后端依赖
git clone https://github.com/XIEXU0706/AIopsAgent.git
cd AIopsAgent
pip install -r requirements.txt

# 2. 配置环境变量（不配 LLM Key 也能跑，走规则降级）
cp .env.example .env
# 编辑 .env，按需填入 DEEPSEEK_API_KEY / KIMI_API_KEY / MYSQL_DSN 等

# 3. 启动后端（默认 127.0.0.1:9092）
uvicorn app.main:app --reload --port 9092
# 或 python -m app.main

# 4.（可选）启动前端
cd frontend && npm install && npm run dev

# 5. 验证
curl http://127.0.0.1:9092/health
# 交互文档：http://127.0.0.1:9092/docs
```

### 环境变量（.env）

| 变量 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek Key，不配则走规则降级 | 空 |
| `KIMI_API_KEY` | Kimi Key（智能问答用） | 空 |
| `KIMI_CHAT_MODEL` | 对话模型 | `kimi-k2.6` |
| `EMBEDDING_BACKEND` | 知识库 embedding：`auto`（装了 sentence-transformers 用 BGE）/ `bge` / `hashing` | `auto` |
| `BGE_MODEL_NAME` | BGE 语义模型（512 维，CPU 可跑） | `BAAI/bge-small-zh-v1.5` |
| `LONG_TERM_BACKEND` | 长期记忆后端 `mysql` / `sqlite` | `mysql` |
| `MYSQL_DSN` | MySQL 连接串（不可用时自动降级 SQLite） | `mysql://root:123456@localhost:3306/aiopsAgent` |
| `CHAT_ARCHIVE_DAYS` | 会话归档保留天数，0=不归档 | `7` |
| `NOTIFICATION_WEBHOOK_URL` | 预警发送 Webhook，非空则真实 POST，否则仅存库 | 空 |

---

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/alerts` | 提交告警，触发多 Agent 处置 |
| `POST` | `/api/v1/webhook/alertmanager` | 接收 Prometheus Alertmanager v4 webhook 推送 |
| `GET` | `/api/v1/alerts` | 告警列表 |
| `GET` | `/api/v1/alerts/{alert_id}` | 告警详情（含处置报告、备注 notes、交接摘要） |
| `GET` | `/api/v1/alerts/{trace_id}/events` | SSE 事件流（任务状态实时推送） |
| `POST` | `/api/v1/chat/ask` | AI 智能问答（SSE 流式，分层记忆上下文） |
| `POST` / `GET` / `DELETE` | `/api/v1/sessions`… | 会话增删查 |
| `GET` | `/api/v1/knowledge/*` | 知识库（上传 / 文档 / 检索 / 统计） |
| `GET` | `/api/v1/notifications` | 预警通知记录 |
| `GET` | `/health` | 健康检查 + 已注册 Agent |

---

## 工程验证

- 本地 `pytest` 42+ 用例，覆盖 **Risk Safety / Agent Routing / Standard Skills / RAG / API / Tool Queue / Alertmanager Webhook** 七类核心链路。

### RAG 检索质量：哈希 vs 语义 embedding 对比实验

```bash
pip install sentence-transformers   # 语义后端（可选，CPU 可跑）
python scripts/rag_eval.py          # 四象限对比：{hashing, bge} × {exact, paraphrase}
```

两种评测口径：

| 口径 | query 来源 | 意义 |
|---|---|---|
| exact | 案例症状原文 | 同分布基线（历史口径，必然偏高） |
| paraphrase | 独立改写的运维口语描述（规避原文关键词） | 模拟真实告警与知识库措辞不一致，考察语义泛化 |

评测在独立临时目录建库，不污染生产数据。**关注点：paraphrase 口径下 bge 相对 hashing 的增益**——这是语义向量相对词法匹配的真实价值，可直接作为技术选型依据写入报告。

### 为什么选本地 bge 而非云端 DashScope embedding

本项目默认采用**本地 `bge-small-zh-v1.5`**，而非阿里 DashScope 等云端 embedding，是面向 AIOps 内网场景的权衡：

| 维度 | 本地 bge（已选） | 云端 DashScope |
|---|---|---|
| 网络 | 离线可用，契合内网/隔离环境 | 需访问公网，断网即废 |
| 数据隐私 | 故障文本不出本机 | 文本需发往第三方云 |
| 成本 | 模型一次性下载，零调用费 | 按 token 计费 |
| 兜底 | 无 sentence-transformers 时降级 hashing，连模型都不需 | 无网络则完全不可用 |
| 能力 | 小模型，语义弱于云端大模型 | 语义更强（text-embedding-v2/v3） |

**取舍结论**：AIOps 场景优先保障「数据不出内网 + 离线可用 + 零调用成本」，本地 bge + hashing 兜底为此服务；代价是语义能力弱于云端大模型。如需更强语义，可在 `knowledge_base.py` 的 `resolve_embedding_backend` 体系下新增 `DashScopeEmbeddingFunction`（实现统一的 `encode_documents / encode_query` 接口），`EMBEDDING_BACKEND` 配置即切换，无需改动检索链路。

### 压测基准（Locust）

```bash
pip install locust
uvicorn app.main:app --port 9092    # 建议（可选）无 LLM Key 模式：测纯工程吞吐

# 50 并发、每秒递增 5、持续 60s，CSV 输出含 P99
locust -f scripts/load_test.py --headless -u 50 -r 5 -t 60s \
    --host http://127.0.0.1:9092 --csv results/loadtest
```

压测场景：告警接入（`POST /api/v1/alerts`）、Alertmanager webhook、告警列表查询、健康检查基线。读 `results/loadtest_stats.csv` 的 `Requests/s`（QPS）与 `99%` 列（P99 延迟，ms）。

告警接入为 202 异步受理（BackgroundTasks 后台处置），**接口吞吐不受 LLM/处置耗时影响**——这是接入层与处置层解耦的验证点。注意：压测会产生真实告警处置数据，跑完可清理 `data/reports/`。

### Alertmanager 真实告警源对接

```bash
# 启动服务后，用内置模拟器推送 Prometheus 风格告警（mysql/redis/disk/cpu/http）
python scripts/mock_alertmanager.py --alert mysql          # 单发指定类型
python scripts/mock_alertmanager.py --count 20 --interval 0.5
python scripts/mock_alertmanager.py --loop --interval 2    # 持续告警流
```

真实 Alertmanager 只需在 `alertmanager.yml` 配置：

```yaml
receivers:
  - name: "mindbridge"
    webhook_configs:
      - url: "http://<host>:9092/api/v1/webhook/alertmanager"
        send_resolved: true
```

接入行为：firing 告警逐条进入多 Agent 处置链路（labels/annotations 全量保留，供检索 Agent 提取关键词）；resolved 通知过滤；重复 fingerprint 幂等受理。

---

## 目录结构

```
MindBridge-AIOps/
├── app/
│   ├── main.py                 # FastAPI 入口 & 路由注册
│   ├── config.py               # 全局配置（.env）
│   ├── harness/                # AIOpsAgentHarness（编排与治理核心）
│   ├── blackboard/             # CollaborationBlackboard（HEARSAY-II 黑板）
│   ├── agents/                 # Coordinator / LogAnalyze / Retrieval / Safety
│   ├── runtime/                # AgentRuntime / ExecutionContext / TraceManager
│   ├── skills/                 # AIOpsSkillRegistry + definitions/（SKILL.md）
│   ├── llm/                    # DeepSeekClient / KimiClient
│   ├── memory/                 # HierarchicalMemory + stores（Redis/MySQL/SQLite）
│   ├── mcp/                    # AsyncToolQueue + tools/（4 个工具 handler）
│   ├── services/               # alert_store / notification_store / knowledge_base
│   ├── models/                 # DispositionReport / Trace / Severity
│   └── api/                    # alert / chat / knowledge / notification 路由
├── frontend/                   # Vue 2.7 + Element UI + ECharts
├── scripts/                    # rag_eval.py（RAG 对比评测）/ mock_alertmanager.py（告警源模拟）/ load_test.py（Locust 压测）
├── requirements.txt
└── pytest.ini
```
