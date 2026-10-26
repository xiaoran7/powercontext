# 星海迷局：基于 Dify × OceanBase SeekDB × PowerContext 的 AI 赛博侦探与多轮推演剧场

> **赛题归属**：2026 OceanBase AI 数据库大赛 · Dify 赛题（Just for Fun：使用 Dify + seekdb + PowerContext 构建一个好玩的项目）
> **分支目录**：`dify/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、项目创意与核心玩法（Just for Fun!）

**《星海迷局：深空算力中枢失窃案》** 是一款将 **硬核向量数据库检索** 与 **上下文证据工程** 融入沉浸式文字推理的 **AI 赛博侦探多轮推演剧场（Interactive Cyber-Noir Detective RPG）**。

- **故事设定**：2026 年深秋，轨道空间站「星海七号」的核心量子向量密钥在凌晨 `02:15` 被盗。三名核心技术负责人——首席向量架构师**林维**、记忆中枢安全官**苏晴**、具身运维工程师**陈默**——各自给出了看似无懈可击的技术不在场证明。
- **为什么必须同时依赖 Dify + SeekDB + PowerContext？**
  1. **Dify（多节点剧情编排与外部知识库路由）**：通过可直接导入的 [`dify_cyber_detective_app.yml`](./dify_cyber_detective_app.yml) Chatflow 工作流，串联「玩家审讯输入 → SeekDB 外部知识库混合检索 → PowerContext 线索固化与预算装配 → 侦探主持人（GM）沉浸式对白生成」。
  2. **OceanBase SeekDB（专有技术术语 + 语义双路 RRF 混合检索）**：嫌疑人的不在场证明中包含大量精确函数名与架构术语（如 `dbms_vector.batch_knn`、`Delta HNSW`、`Scope`、`GOLD` 级轨迹校准）。单一向量检索容易漏掉精确标识符，单一全文检索难以理解玩家的自然语言质询；SeekDB 通过 **VSAG HNSW 余弦向量检索 + BM25 全文检索 + RRF（倒数排名融合）** 精准锁定破案卷宗。
  3. **PowerContext（多轮侦探取证生命周期与证据预算装配）**：
     - **Scope 隔离**：为每位玩家的侦探会话建立独立 `scope:cyber-detective:{session_id}`；
     - **Source 固化**：将每一轮玩家审讯与 SeekDB 命中的技术卷宗固化为带 SHA-256 摘要的 `CAPTURED` Source；
     - **Memory & Citation**：自动检测嫌疑人证词矛盾，生成带精确字符区间（`start`, `end`）的 `MemoryCitation` 原子记忆；
     - **PreparedContext 预算装配**：在严格受限的 UTF-8 字节预算（`max_bytes`）内，优先筛选已击穿证词矛盾的高价值铁证，组装 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 标准信封喂给 LLM；
     - **Handoff 结案交接**：当破案进度达到阈值时，一键生成带完整证据链溯源（`powercontext.handoff.v1`）的结案陈词。

---

## 二、系统架构图

```
+-----------------------------------------------------------------------------------+
|                   Dify Chatflow 编排引擎 (dify_cyber_detective_app.yml)            |
|  [Start: 玩家审讯指令 & 嫌疑人]                                                    |
|         |                                                                         |
|         +---> [Knowledge Retrieval 节点] ---> POST /retrieval (Dify 标准协议)      |
|         |                                           |                             |
|         +---> [HTTP Request 节点] ----------> POST /api/game/turn                 |
|         |                                           |                             |
|         +---> [LLM 赛博侦探 GM 节点] <--------------+                              |
+-----------------------------------------------------|-----------------------------+
                                                      v
+-----------------------------------------------------------------------------------+
|               FastAPI 侦探剧场与混合检索服务 (server.py :8089)                      |
|  +-----------------------------------+   +-------------------------------------+  |
|  | HybridSearchEngine (hybrid_engine)|   | CyberDetectiveContextEngine         |  |
|  | - SeekDB VSAG HNSW 向量检索        |   | - Scope / CAPTURED Source 证据固化   |  |
|  | - BM25 / IK 全文检索               |   | - 证词矛盾判定 & MemoryCitation      |  |
|  | - RRF (k=60) 倒数排名融合          |   | - PreparedContext 字节预算截断装配   |  |
|  |                                   |   | - Handoff (powercontext.handoff.v1) |  |
|  +-----------------+-----------------+   +------------------+------------------+  |
+--------------------|----------------------------------------|---------------------+
                     v                                        v
+-----------------------------------------------------------------------------------+
|                     OceanBase SeekDB (100.72.34.104:2881)                         |
|  Table: dify_knowledge_base (VECTOR(512) HNSW + FULLTEXT INDEX WITH PARSER ik)    |
+-----------------------------------------------------------------------------------+
```

---

## 三、快速复现指南

### 1. 启动 SeekDB + PowerContext 侦探后端与可视化双栏看板

```bash
export SEEKDB_HOST="100.72.34.104"
export SEEKDB_PORT="2881"
export SEEKDB_DATABASE="powercontext"
export SEEKDB_USER="root"
export SEEKDB_PASSWORD=""

cd dify
python server.py
```

访问 `http://127.0.0.1:8089` 可打开交互式检索对比与侦探验证面板。

### 2. 在 Dify 中导入工作流 DSL

1. 打开 Dify 控制台 → **工作室（Studio）** → **导入 DSL 文件**，选择本目录下的 [`dify_cyber_detective_app.yml`](./dify_cyber_detective_app.yml)。
2. 在 Dify **知识库（Knowledge）** → **连接外部知识库** 中配置：
   - **External Knowledge API Endpoint**: `http://127.0.0.1:8089/retrieval`
   - **API Key**: `dify-seekdb-2026-secret`
   - **Knowledge ID**: `seekdb-hybrid-kb`

### 3. 多轮验证样例执行与初始状态恢复

完整的多轮输入输出验证样本记录在 [`verification_sample.json`](./verification_sample.json) 中：
- **恢复初始状态（便于重复验证）**：
  ```bash
  curl -X POST http://127.0.0.1:8089/api/game/reset/sample-detective-2026
  ```
- **运行自动化验收测试**：
  ```bash
  pytest dify/test_dify.py -v
  ```

