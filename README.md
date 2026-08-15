# AIOps_Agent

> 面向智能运维告警处理场景，集 **多 Agent 智能编排**、**日志故障根因分析**、**RAG 故障知识检索**、**安全审查与风险预警**、**自动化报表导出** 以及 **AI 智能问答** 于一体的智能运维分析平台。

接收监控告警 → 多 Agent 并行分析（日志分析 / 知识检索）→ Coordinator 跨 Agent 归纳 → Safety 安全护栏审查 → 输出处置报告、预警通知与工作交接摘要。配置 LLM Key 走智能分析；**未配置时全链路规则降级，系统仍可运行**。

---

## 核心特性

- **多 Agent 协作（HEARSAY-II 黑板模式）**：以 `CollaborationBlackboard` 为共享协作空间，Coordinator 发布任务、Specialist Agent 按能力原子认领（`asyncio.Lock` 保证不重复执行）、执行后以 Artifact 写回黑板并广播事件，Agent 间完全解耦、可动态扩展。
- **安全护栏（Guardrails）双层防护**：`SafetyAgent` 第一层 17 条正则规则硬拦截高危操作（DROP / TRUNCATE / DELETE / rm -rf / flushall 等），第二层 LLM 语义级风险评级捕获「回滚版本、跨 AZ 切换」等隐式高危意图，高危操作强制人工复核。
- **处置预案动态加载**：`AIOpsSkillRegistry` 扫描 `SKILL.md` 按告警类型（error_type）匹配处置计划，**新增预案只需放一个 markdown 文件，不用改代码**；高风险场景自动叠加安全审查，并生成结构化交接摘要。
- **RAG 故障知识检索**：`RetrievalAgent` 基于 Chroma 向量库检索历史故障案例，中文 2-gram + 英文分词关键词匹配兜底；本地评估 HitRate≈0.97 / MRR≈0.91。
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
| `LONG_TERM_BACKEND` | 长期记忆后端 `mysql` / `sqlite` | `mysql` |
| `MYSQL_DSN` | MySQL 连接串（不可用时自动降级 SQLite） | `mysql://root:123456@localhost:3306/aiopsAgent` |
| `CHAT_ARCHIVE_DAYS` | 会话归档保留天数，0=不归档 | `7` |
| `NOTIFICATION_WEBHOOK_URL` | 预警发送 Webhook，非空则真实 POST，否则仅存库 | 空 |

---

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/alerts` | 提交告警，触发多 Agent 处置 |
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

- 本地 `pytest` 38 个用例，覆盖 **Risk Safety / Agent Routing / Standard Skills / RAG / API / Tool Queue** 六类核心链路。
- RAG 检索质量评估：HitRate≈0.97 / MRR≈0.91（`scripts/rag_eval.py` 可复现）。

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
├── scripts/                    # rag_eval.py（RAG 评估脚本）
├── requirements.txt
└── pytest.ini
```
