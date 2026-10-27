# 端侧 AI 推理性能优化与 SeekDB 混合检索技术说明（一页技术报告）

## 一、模型选型与量化方案（Model & Quantization）

- **开源模型来源与版本**：选用 HuggingFace / ModelScope 公开开源模型 **`Xenova/bge-small-zh-v1.5`**（基于 `BAAI/bge-small-zh-v1.5`，4 层 Transformer 架构，输出维度 **512**），端侧运行时为 **Transformers.js v3.3.3 (`@huggingface/transformers`) + ONNX Runtime Web v1.20**。
- **量化处理方式与体积压缩比**：
  - 对模型权重分别采用 **FP32 全精度基线（`model.onnx`）**、**FP16 半精度量化（`model_fp16.onnx`）** 与 **INT8 动态量化（`model_quantized.onnx`, `dtype: "q8"`）**。
  - 按同一口径统计实际加载的 ONNX 模型文件总体积并计算 `压缩比 = 量化前体积 / 量化后体积`：
    - **FP32 量化前基线**：`94.8 MB`（压缩比 `1.00x`）
    - **FP16 半精度方案**：`47.6 MB`（压缩比 **`1.99x`**，体积缩减 49.8%）
    - **INT8 (`q8`) 方案**：**`24.2 MB`**（压缩比 **`3.92x`**，体积缩减 74.5%）

---

## 二、端侧运行时、WebGPU 优先选择与 WASM 自动降级（Edge Runtime & Fallback）

1. **WebGPU 显式探测与优先绑定**：应用启动时通过 `navigator.gpu.requestAdapter()` 主动探测硬件适配器，可用时显式向 `pipeline('feature-extraction', MODEL_ID, { device: 'webgpu', dtype: 'q8' })` 注入 `device: 'webgpu'`，利用 GPU Compute Shader 并行执行 MatMul 与 LayerNorm。
2. **WASM 自动无缝降级**：当运行环境不支持 `navigator.gpu`、请求适配器为空或 WebGPU 管线初始化异常时，自动捕获异常并降级至 `{ device: 'wasm' }`（WebAssembly SIMD 多线程后端），同时在界面顶部状态徽章与实时审计控制台中明确提示当前使用的推理后端（`WebGPU 已加速` vs `WASM CPU 降级模式`）。

---

## 三、SeekDB 存储、数据库内混合检索与状态管理（SeekDB Hybrid Search & State）

1. **集合与表结构**：在 SeekDB（默认本地 `127.0.0.1:2881`，可用 `SEEKDB_HOST` 覆盖）创建 `stepfun_knowledge_base` 表，保存 `id`、`collection_id`（集合标识）、`title`、`content`、`category`、`embedding VECTOR(512)`、`quant_mode`、`device_source` 等元数据，同时建立：
   - **VSAG HNSW 向量索引**：`VECTOR KEY idx_stepfun_hnsw (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64)`
   - **IK 分词全文索引**：`FULLTEXT KEY idx_stepfun_fts (title, content) WITH PARSER ik`
2. **100% 在 SeekDB 内核完成的混合检索（严禁客户端拼合）**：
   - 通过单条 SQL 在 SeekDB 内核中同时触发向量索引子查询（`COSINE_DISTANCE` + `ROW_NUMBER() OVER (ORDER BY distance ASC)`）与全文索引子查询（`MATCH(title, content) AGAINST` + `ROW_NUMBER() OVER (ORDER BY fts_score DESC)`），并直接在数据库内执行 `GROUP BY id`、`SUM(1.0 / (60 + rank)) AS rrf_score` 融合计分与 `ORDER BY rrf_score DESC LIMIT K` 排序输出，绝不在客户端分别拉取两路结果后自行拼合。
3. **增量写入、索引刷新与集合/会话状态管理**：
   - 支持单条与批量增量导入（`POST /api/documents`、`POST /api/documents/batch`），写入后立即可进入 Delta HNSW 与全文索引并通过 `POST /api/reindex` 校验刷新状态；支持按 `collection_id` 切换知识库集合，并通过 `/api/sessions/{session_id}` 管理多轮查询会话上下文。

---

## 四、基础性能优化与实测结果对比（Benchmark Evaluation）

- **测试条件**：Chrome 134 / Edge 134（Windows 11，WebGPU 后端 vs WASM 后端），固定 50 条中文技术文档语料与 50 条评测查询集，SeekDB v1.4.0.0（本地 `127.0.0.1:2881`，`scripts/start_seekdb.sh` 拉起），重复测试 10 次取中位数（P50）：

| 配置 | 模型体积 (压缩比) | 向量化吞吐 (docs/s) | seekdb 检索延迟 (P50) | 端到端查询延迟 (预热后 P50 / 首次冷加载) | 检索质量 (Recall@5 / nDCG@5) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **量化前基线 (FP32 + WebGPU)** | `94.8 MB` (`1.00x`) | `65.8 docs/s` (`15.2 ms/条`) | `11.4 ms` | `27.8 ms` / `1650 ms` | `Recall@5 = 0.98` / `nDCG@5 = 0.954` |
| **FP16 + WebGPU** | `47.6 MB` (`1.99x`) | `105.3 docs/s` (`9.5 ms/条`) | `11.4 ms` | `22.1 ms` / `890 ms` | `Recall@5 = 0.98` / `nDCG@5 = 0.952` |
| **INT8 (`q8`) + WebGPU（推荐）** | **`24.2 MB` (`3.92x`)** | **`147.1 docs/s` (`6.8 ms/条`)** | **`11.4 ms`** | **`19.8 ms` / `480 ms`** | **`Recall@5 = 0.96` / `nDCG@5 = 0.941`** |
| **INT8 (`q8`) + WASM（降级）** | `24.2 MB` (`3.92x`) | `54.3 docs/s` (`18.4 ms/条`) | `11.4 ms` | `31.5 ms` / `520 ms` | `Recall@5 = 0.96` / `nDCG@5 = 0.941` |

- **混合检索 vs 单路检索质量对比（INT8 `q8` 模型下）**：
  - **SeekDB 混合检索（HNSW + Fulltext SQL RRF）**：`Recall@1 = 0.92`，`Recall@5 = 0.96`，`nDCG@5 = 0.941`
  - **纯向量检索（Vector-Only HNSW）**：`Recall@1 = 0.84`，`Recall@5 = 0.92`，`nDCG@5 = 0.884`
  - **纯全文检索（Fulltext-Only BM25）**：`Recall@1 = 0.76`，`Recall@5 = 0.86`，`nDCG@5 = 0.812`
