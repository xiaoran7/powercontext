# 端侧 AI 推理性能优化与 SeekDB 混合检索技术说明（一页技术报告）

> 本页所有性能与质量数字均来自**提交应用在真实浏览器中的实际运行**：
> 打开 `http://127.0.0.1:8088/bench` 点击「开始实测」后，前端把逐条查询结果写入
> `benchmark/results/raw-*.json` 并汇总到 `benchmark/results/summary.json`，
> 本页按该汇总填写。复现方式与设备信息见文末「复现与数据来源」。

## 一、模型选型与量化方案（Model & Quantization）

- **开源模型来源与版本**：HuggingFace 公开模型 **`Xenova/bge-small-zh-v1.5`**（`BAAI/bge-small-zh-v1.5` 的 ONNX 版本，4 层 Transformer，输出 **512** 维），仓库 revision `75c43b0`（2025-07-22）。端侧运行时为 **Transformers.js v3.3.3（`@huggingface/transformers`）+ ONNX Runtime Web**。
- **量化方案**：应用顶部下拉框直接切换 `q8`（INT8 动态量化，`model_quantized.onnx`）、`fp16`（`model_fp16.onnx`）、`fp32`（`model.onnx`），通过 Transformers.js 的 `dtype` 参数显式加载对应权重文件。
- **体积与压缩比（按 ONNX 权重实测字节数）**：详见下表 `模型体积` 列；`压缩比 = FP32 体积 / 该配置体积`。

## 二、端侧运行时、WebGPU 优先与 WASM 自动降级

1. **WebGPU 显式探测**：启动时调用 `navigator.gpu.requestAdapter()`，可用即向 `pipeline(..., { device: 'webgpu', dtype })` 显式注入后端，界面徽章显示 `WebGPU 已加速` 与显卡型号。
2. **WASM 自动降级**：无 `navigator.gpu` / 适配器为空 / WebGPU 管线初始化异常时，自动改用 `{ device: 'wasm' }` 重新加载，徽章切换为 `WASM CPU 降级模式`，核心流程（导入 → 向量化 → 写入 → 检索）不受影响。
3. **验证方式**：`/bench` 页的「推理设备」可强制指定 `webgpu` / `wasm`，两组结果都记录在汇总里。

## 三、SeekDB 存储、库内混合检索与状态管理

1. **接入方式**：提供两套等价服务端，均通过官方 seekdb-js 生态接入 MySQL 协议：
   - `server.py`（Python / pymysql，`http://127.0.0.1:8088`）
   - [`server_seekdb_js.mjs`](./server_seekdb_js.mjs)（**官方 npm 包 `seekdb` 的 `SeekdbClient`**，`http://127.0.0.1:8089`）
   > 题面写的 `seekdb-js` 即该 SDK 的项目名，npm 发布的包名为 `seekdb`（旧包名 `seekdb-js` 已于 2025-12-05 下架）。
2. **表结构与索引**：`stepfun_knowledge_base` 保存 `title`、`content`、`category`、`collection_id`、`embedding VECTOR(512)`、`quant_mode`、`device_source`、`client_ms`，并建立
   - `VECTOR INDEX idx_stepfun_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag)`
   - `FULLTEXT INDEX idx_stepfun_fts (title, content) WITH PARSER ik`
3. **库内混合检索（禁止客户端拼合）**：`POST /api/search` 的 `hybrid` 模式用**一条 SQL** 完成：
   向量子查询（`COSINE_DISTANCE ... APPROXIMATE` + `ROW_NUMBER()`）与全文子查询
   （`MATCH ... AGAINST` + `ROW_NUMBER()`）`UNION ALL` 后，在库内 `GROUP BY id`、
   `SUM(1.0/(60+rank)) AS rrf_score` 融合并 `ORDER BY rrf_score DESC LIMIT K`，客户端只接收最终结果。
4. **增量写入、索引刷新与会话状态**：`POST /api/documents`、`/api/documents/batch` 支持单条与批量写入；
   `POST /api/reindex` 读回 `SHOW INDEX` 与文档计数；`GET/DELETE /api/sessions/{id}` 维护多轮查询上下文。
   批量写入会临时调大 `ob_query_timeout`，避免 seekdb 默认 10s 查询超时。

## 四、性能与检索质量实测（由 `/bench` 在浏览器内产生）

<!-- BENCHMARK_RESULTS_START -->
- **测试环境**：`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/155.0.0.0 Safari/537.36`；WebGPU 可用：True；SeekDB 本地实例（127.0.0.1:2881）；语料 48 篇 / 查询 30 条，Top-K = 5。
- **运行时间**：2026-10-27T13:55:59.360Z → 2026-10-27T13:57:11.497Z（原始数据 `benchmark/results/raw-20261027T215711.json`）。

| 配置 | 模型体积 (MiB) | 压缩比 | 冷加载 (ms) | 向量化吞吐 (docs/s) | seekdb 检索 P50 (ms) | 端到端 P50 (ms) | Recall@5 | nDCG@5 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| INT8 (q8) + WEBGPU | 22.90 | 3.95x | 13982 | 10.7 | 21.49 | 96.30 | 0.7667 | 0.6749 |
| FP16 + WEBGPU | 45.30 | 2.00x | 14218 | 103.4 | 26.09 | 37.55 | 0.9667 | 0.9377 |
| FP32 + WEBGPU | 90.46 | 1.00x | 27967 | 136.4 | 24.03 | 35.50 | 0.9667 | 0.9377 |
| INT8 (q8) + WASM | 22.90 | 3.95x | 82 | 33.6 | 27.71 | 41.85 | 0.9667 | 0.9377 |

- **口径**：模型体积按 ONNX 权重实测字节数（MiB = 1024²）；压缩比以 FP32 为基线；冷加载包含权重下载/读取与执行图初始化（可用浏览器缓存，首次运行会明显更慢）；吞吐为 48 篇文档连续推理的均值；seekdb 检索为服务端单条混合 SQL 往返 P50；端到端 = 查询向量化 + 检索。每组配置在计时前先预热 3 次推理，避免 WebGPU 首次 shader 编译污染延迟数据。

> **关于 INT8 + WebGPU 的实测异常（如实记录）**：同一份语料下，INT8 动态量化在 WebGPU 后端的
> Recall@5 只有 0.7667（其余三组均为 0.9667），吞吐也只有 FP16 的约 1/10。该结果在两次独立运行中
> 可复现，属于 ONNX Runtime Web 的 WebGPU 执行器对动态量化权重的算子回退与精度损失，不是计时噪音。
> 因此应用默认策略为：**WebGPU 环境默认 FP16，WASM 环境默认 INT8**，用户仍可在页面顶部手动切换。
> 若只追求最小体积（22.9 MiB，压缩比 3.95x），INT8 + WASM 同时保住了 0.9667 的 Recall@5，是纯体积最优组合。

<!-- BENCHMARK_RESULTS_END -->

## 五、复现与数据来源

- **语料**：`benchmark/corpus.json`（48 篇中文技术文档、30 条查询、每条查询人工标注 1–2 篇相关文档），
  由 `benchmark/build_corpus.py` 生成，随仓库提交。
- **运行**：`python server.py` 后访问 `http://127.0.0.1:8088/bench`，点击「开始实测」。
  页面会对每组配置重置知识库、重新写入同一份语料，再逐条执行库内混合检索。
- **指标口径**：Recall@5 = 前 5 条命中人工标注相关文档的比例；nDCG@5 按二值相关度计算并做理想 DCG 归一化。
- **计时口径**：冷加载 = `pipeline()` 从调用到返回（可用浏览器缓存，首次运行会包含权重下载）；
  向量化吞吐 = 48 篇文档连续推理；seekdb 检索延迟 = 服务端单条混合 SQL 往返；端到端 = 查询向量化 + 检索。
- **原始数据**：`benchmark/results/raw-*.json` 保存每次运行的逐条查询结果（含排名、RRF 分数、各阶段耗时），
  `summary.json` 为汇总；`/api/benchmark` 原样读取该汇总返回，不在服务端硬编码任何数字。
