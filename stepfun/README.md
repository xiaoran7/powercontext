# StepFun × OceanBase SeekDB：浏览器端侧 WebGPU/WASM 量化 Embedding 与单 SQL 混合检索系统

> **赛题归属**：2026 OceanBase AI 数据库大赛 · 阶跃星辰赛题（将 Embedding 塞进浏览器：WebGPU 端侧推理与混合检索）
> **分支目录**：`stepfun/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、方案核心亮点与六大硬性约束对齐

| 赛题硬性要求 | 本方案实现机制与证据锚点 |
| :--- | :--- |
| **1. 真实浏览器端侧推理（拒绝预计算与伪装端侧）** | 在 `static/index.html` 中通过 `@huggingface/transformers@3.3.3` 在浏览器本地加载 `Xenova/bge-small-zh-v1.5` ONNX 模型；每次录入与查询均在前端实时执行 `embedPipeline(text, { pooling: 'mean', normalize: true })` 产出 512 维单位向量，`server.py` / `server_seekdb_js.mjs` 均零模型依赖、零云端 Embedding API 调用。 |
| **2. WebGPU 硬件加速与 WASM 自动降级** | 前端启动时调用 `navigator.gpu.requestAdapter()` 探测 WebGPU 适配器并回显显卡型号；若浏览器或驱动不支持 WebGPU，自动无缝切换至 WebAssembly（WASM）后端，界面徽章实时显示当前后端。 |
| **3. 端侧量化对比（INT8 `q8` / FP16 / FP32）** | 页面顶部下拉框可切换 `q8` / `fp16` / `fp32`，以 `dtype` 参数显式传给 Transformers.js 加载对应 ONNX 变体；量化前后体积、吞吐与检索质量由浏览器内实测页 `/bench` 跑出，见 [`REPORT.md`](./REPORT.md)。 |
| **4. seekdb-js 接入 + 单条 SQL 混合检索** | 两条等价服务端：Python 版 `server.py`（pymysql）与 **官方 `seekdb` npm 包（seekdb-js）** 版 [`server_seekdb_js.mjs`](./server_seekdb_js.mjs)，对外 API 完全一致。二者都在 `stepfun_knowledge_base` 表上同时建立 VSAG HNSW 向量索引与 IK 全文索引，混合检索由单条 `UNION ALL` + `GROUP BY` + RRF 的 SQL 在 SeekDB 内核内完成。 |
| **5. 数据集分片切换、索引重建与会话状态管理** | 支持按 `collection_id` 切换数据集分片、通过 `POST /api/reindex` 刷新并校验双索引状态、通过 `/api/sessions/{session_id}` 追踪多轮查询上下文与端到端延迟轨迹。 |
| **6. 完整可运行 Web 系统与自动化测试** | 提供双栏实时交互 Web 工作台（`http://127.0.0.1:8088`）、量化实测页 `/bench`、`pytest` 自动化测试 `test_stepfun.py` 与 Node 自检 `benchmark/selfcheck_seekdb_js.mjs`。 |

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
|                     OceanBase SeekDB (本地 127.0.0.1:2881)                          |
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

> 应用默认连接**本机 seekdb（`127.0.0.1:2881`）**：若本地实例未运行，服务启动时会自动拉起，
> 无需手动配置任何数据库地址。也支持 `SEEKDB_HOST` 指向已有实例（含远程），见 `.env.example`。

### 0. 获取 seekdb（首次运行前）

任选一种方式，装好后 `seekdb` 在 PATH 中即可；也可用 `SEEKDB_BIN` 指定绝对路径：

```bash
# 方式 A：官方发行包（Ubuntu / Debian，推荐）
#   从 https://github.com/oceanbase/seekdb/releases 下载 seekdb_1.4.0-*.deb 后：
sudo dpkg -i seekdb_1.4.0-*.deb

# 方式 B：从源码编译
git clone https://github.com/oceanbase/seekdb && cd seekdb && bash build.sh

# 方式 C：已有其它位置的 seekdb 可执行文件
export SEEKDB_BIN=/path/to/seekdb

# 方式 D：不装本地实例，直接使用已有 seekdb（远程或别人提供的）
export SEEKDB_HOST=<host> SEEKDB_PORT=2881
```

### 1. 安装依赖并启动应用

Python 版（默认，`http://127.0.0.1:8088`）：

```bash
cd stepfun
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 可选：手动先起数据库（不执行也可以，应用会自动拉起）
bash scripts/start_seekdb.sh --daemon     # Windows: powershell -File scripts/start_seekdb.ps1 -Daemon

python server.py
```

Node 版（官方 seekdb-js SDK，`http://127.0.0.1:8089`，API 与 Python 版一致）：

```bash
cd stepfun
npm install
node server_seekdb_js.mjs
```

两个服务端任选其一即可，浏览器访问 `http://127.0.0.1:8088`（Python）或 `http://127.0.0.1:8089`（Node）。
页面顶部会显示当前推理后端（WebGPU / WASM）、量化精度与 SeekDB 连接模式（本地 / 远程）。

可用环境变量见 [`.env.example`](./.env.example)；常用项：

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `SEEKDB_HOST` | `127.0.0.1` | 数据库地址；设为远程地址即切换为远程模式（不会启动本地进程） |
| `SEEKDB_PORT` | `2881` | 数据库端口 |
| `SEEKDB_DB` | `powercontext` | 使用的 database，不存在时自动创建 |
| `SEEKDB_BIN` | 自动查找 | seekdb 可执行文件路径 |
| `SEEKDB_BASE_DIR` | `stepfun/seekdb_data` | 本地实例的数据 / 日志根目录 |
| `SEEKDB_AUTO_START` | `1` | 置 0 则只连接已有实例，不自动拉起 |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8088` | Python 服务端监听地址 |
| `NODE_APP_PORT` | `8089` | Node 服务端监听端口 |

### 2. 运行自动化验收测试

```bash
cd stepfun
pytest test_stepfun.py -v                 # Python 服务端全链路
node benchmark/selfcheck_seekdb_js.mjs    # Node / seekdb-js 库内混合检索自检
```

测试会在本地 seekdb 未运行时自动拉起实例，并校验：双索引建表、增量写入、**库内单 SQL 混合检索（HNSW + IK 全文 + RRF）**、会话状态、索引刷新与量化实测接口。

### 3. 量化与检索质量实测（`/bench`）

打开 `http://127.0.0.1:8088/bench`（Node 版为 `:8089/bench`），点击「开始实测」：

- 在**真实浏览器**中依次加载 INT8 / FP16 / FP32（以及 WASM 降级路径）模型，对固定语料做本地 vectorize；
- 每组配置都会重置知识库、重新写入，再逐条执行库内混合检索；
- 计算 Recall@5 / nDCG@5，并把逐条查询的原始数据写入 `benchmark/results/raw-*.json`，汇总到 `summary.json`；
- `/api/benchmark` 读取该汇总文件返回，不在服务端硬编码任何数字。

语料与查询集由 [`benchmark/build_corpus.py`](./benchmark/build_corpus.py) 生成，固定在 [`benchmark/corpus.json`](./benchmark/corpus.json)（48 篇文档 / 30 条查询，含人工标注的相关文档）。

详细实测结果、设备信息与口径说明见 [`REPORT.md`](./REPORT.md)。

详细量化精度、端到端延迟拆解与 Recall@K 评测结果请参阅 [`REPORT.md`](./REPORT.md)。

OB-P:xI1C-CXPJXT4ztVpZDY6_A
