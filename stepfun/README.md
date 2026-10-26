# StepFun × OceanBase SeekDB：浏览器端侧 WebGPU/WASM 量化 Embedding 与单 SQL 混合检索系统

> **赛题归属**：2026 OceanBase AI 数据库大赛 · 阶跃星辰赛题（将 Embedding 塞进浏览器：WebGPU 端侧推理与混合检索）
> **分支目录**：`stepfun/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、方案核心亮点与六大硬性约束对齐

| 赛题硬性要求 | 本方案实现机制与证据锚点 |
| :--- | :--- |
| **1. 真实浏览器端侧推理（拒绝预计算与伪装端侧）** | 在 `static/index.html` 中通过 `@huggingface/transformers@3.3.3` 在浏览器本地加载 `Xenova/bge-small-zh-v1.5` ONNX 模型；每次录入与查询均在前端实时执行 `embedPipeline(text, { pooling: 'mean', normalize: true })` 产出 512 维单位向量，`server.py` 零模型依赖、零云端 Embedding API 调用。 |
| **2. WebGPU 硬件加速与 WASM 自动降级** | 前端启动时调用 `navigator.gpu.requestAdapter()` 探测 WebGPU 适配器并回显显卡型号；若浏览器或驱动不支持 WebGPU，自动无缝切换至多线程 WebAssembly（WASM）后端。 |
| **3. 端侧量化对比（INT8 `q8` vs FP16 `fp16` vs FP32）** | 支持 `q8`（24.2 MB）与 `fp16`（47.6 MB）量化模型加载与精度对比，详见 [`REPORT.md`](./REPORT.md) 与 `GET /api/benchmark` 接口。 |
| **4. SeekDB 单条 SQL 向量 + 全文混合检索** | 在 `stepfun_knowledge_base` 表上同时建立 `VECTOR INDEX idx_stepfun_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag)` 与 `FULLTEXT INDEX idx_stepfun_fts (title, content) WITH PARSER ik`，通过单条 `UNION ALL` SQL 在 SeekDB 内核一次往返完成向量近邻与 BM25 全文检索，并执行 RRF（Reciprocal Rank Fusion）融合排序。 |
| **5. 数据集分片切换、索引重建与会话状态管理** | 支持按 `collection_id` 切换数据集分片、通过 `POST /api/reindex` 刷新并校验双索引状态、通过 `/api/sessions/{session_id}` 追踪多轮查询上下文与端到端延迟轨迹。 |
| **6. 完整可运行 Web 系统与自动化测试** | 提供双栏实时交互 Web 工作台（`http://127.0.0.1:8088`）与 `pytest` 自动化测试套件 `test_stepfun.py`。 |

---

## 二、系统架构图

```
+-----------------------------------------------------------------------------------+
|                        用户浏览器 (Browser Edge Runtime)                           |
|  +---------------------------+    +--------------------------------------------+  |
|  | WebGPU / WASM 硬件探测     | -> | Transformers.js (ONNX Runtime Web)         |  |
|  | navigator.gpu.requestAdp  |    | Model: Xenova/bge-small-zh-v1.5 (q8/fp16)  |  |
|  +---------------------------+    +--------------------------------------------+  |
|                                                | 实时计算 512 维 Float32 归一化向量  |
+------------------------------------------------|----------------------------------+
                                                 v  HTTP JSON (query_text + vector)
+-----------------------------------------------------------------------------------+
|                     FastAPI 混合检索与会话网关 (server.py :8088)                    |
|  - POST /api/documents : 校验 512 维向量并写入指定 collection_id                     |
|  - POST /api/search    : 构造 SeekDB 单 SQL (UNION ALL) 向量+全文双通道查询 + RRF    |
|  - POST /api/reindex   : 核验并刷新 HNSW 向量索引与 IK 全文索引                      |
|  - GET  /api/sessions  : 维护多轮检索上下文轨迹与端侧/数据库耗时审计                   |
+------------------------------------------------|----------------------------------+
                                                 v  MySQL Protocol (PyMySQL)
+-----------------------------------------------------------------------------------+
|                     OceanBase SeekDB (100.72.34.104:2881)                         |
|  Table: stepfun_knowledge_base                                                    |
|  - VECTOR KEY idx_stepfun_hnsw (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, VSAG)|
|  - FULLTEXT KEY idx_stepfun_fts (title, content) WITH PARSER ik                   |
+-----------------------------------------------------------------------------------+
```

---

## 三、SeekDB 单条 SQL 混合检索核心实现

在 `POST /api/search`（`mode="hybrid"`）中，系统通过单条 `UNION ALL` SQL 同时下推集合过滤（`collection_id`）、VSAG HNSW 余弦向量检索与 IK 分词全文检索：

```sql
(
    SELECT
        id, title, content, category,
        COALESCE(collection_id, 'default') AS collection_id,
        COALESCE(quant_mode, 'q8') AS quant_mode,
        device_source, client_ms,
        'vector' AS channel,
        COSINE_DISTANCE(embedding, %s) AS distance,
        0.0 AS fts_score
    FROM stepfun_knowledge_base
    WHERE COALESCE(collection_id, 'default') = %s
    ORDER BY COSINE_DISTANCE(embedding, %s) APPROXIMATE
    LIMIT %s
)
UNION ALL
(
    SELECT
        id, title, content, category,
        COALESCE(collection_id, 'default') AS collection_id,
        COALESCE(quant_mode, 'q8') AS quant_mode,
        device_source, client_ms,
        'fts' AS channel,
        NULL AS distance,
        MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS fts_score
    FROM stepfun_knowledge_base
    WHERE COALESCE(collection_id, 'default') = %s
      AND MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) > 0
    ORDER BY fts_score DESC, id ASC
    LIMIT %s
)
```

随后对双通道召回结果按 **RRF（Reciprocal Rank Fusion，$k=60$）** 融合排序：

$$\text{RRF}(d) = \frac{1}{k + \text{rank}_{\text{vector}}(d)} + \frac{1}{k + \text{rank}_{\text{fts}}(d)}$$

---

## 四、快速复现与测试指南

### 1. 环境配置与服务启动

```bash
export SEEKDB_HOST="100.72.34.104"
export SEEKDB_PORT="2881"
export SEEKDB_USER="root"
export SEEKDB_PASS=""
export SEEKDB_DB="powercontext"

cd stepfun
python server.py
```

浏览器访问 `http://127.0.0.1:8088` 即可查看端侧 WebGPU/WASM 状态看板、一键装载竞赛测试数据集并执行实时混合检索。

### 2. 运行自动化验收测试

```bash
pytest stepfun/test_stepfun.py -v
```

详细量化精度、端到端延迟拆解与 Recall@K 评测结果请参阅 [`REPORT.md`](./REPORT.md)。

OB-P:xI1C-CXPJXT4ztVpZDY6_A
