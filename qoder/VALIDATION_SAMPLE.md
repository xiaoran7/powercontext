# ScholarPulse 验证样例（Validation Sample）

## 1. 初始数据说明

系统初始化时（`POST /api/scholar/init` 或 `ScholarPulseEngine().init_and_seed_seekdb(force_reset=True)`），会自动在远端 OceanBase SeekDB（`100.72.34.104:2881`）的 `qoder_scholar_store` 表中构建 512 维余弦向量索引 `idx_qoder_scholar_hnsw` 与 IK 全文索引 `idx_qoder_scholar_fts`，并写入 4 篇核心计算机系统论文段落：
- `PAPER-VSAG-01`（主题：`vector_index`，Delta HNSW 与 Snapshot HNSW 双层架构）
- `PAPER-PC-02`（主题：`context_engineering`，UTF-8 字节预算下的证据子区间筛选）
- `PAPER-RRF-03`（主题：`hybrid_rag`，BM25 与余弦距离的 RRF 倒数排名融合）
- `PAPER-DELTA-04`（主题：`context_engineering`，多轮会话增量上下文包传输）

> 所有接口同时提供两套等价前缀：`/api/scholar/*`（本地 FastAPI）与 `/functions/v1/app/api/scholar/*`（Qoder Sites Edge Function 契约）。下例统一使用后者。

---

## 2. 按步骤执行的验证流程与预期结果

### 步骤 1：重置并初始化文献库
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/reset
```
- **预期结果**：返回 `{"status": "online", "total_sections": 4}`。

### 步骤 2：写入学习者薄弱知识点与复习进度记忆（PowerContext 实际发挥作用）
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/memory \
  -H "Content-Type: application/json" \
  -d '{
    "learner_id": "scholar-01",
    "weak_topics": ["hybrid_rag"],
    "mastered_doc_ids": ["PAPER-VSAG-01"],
    "preferred_depth": "deep-derivation",
    "review_note": "需要重点强化 RRF 倒数排名融合公式推导"
  }'
```
- **预期结果**：返回 `status: "saved"`，并生成 PowerContext 地址 `scope_id='scope:qoder:scholar-pulse' artifact=ArtifactRef(family='learner_memory', artifact_id='scholar-01', revision=1)` 持久化至 SeekDB。

### 步骤 3：跨实例读取学习者记忆（验证重启后记忆可核对复用）
```bash
curl http://127.0.0.1:8093/functions/v1/app/api/scholar/memory/scholar-01
```
- **预期结果**：服务端通过新建独立 `ScholarPulseEngine()` 实例从 SeekDB 读取并返回 `weak_topics: ["hybrid_rag"]` 与 `mastered_doc_ids: ["PAPER-VSAG-01"]`。

### 步骤 4：执行混合检索并生成个性化精读简报与自适应复习闪卡
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/brief \
  -H "Content-Type: application/json" \
  -d '{
    "research_question": "SeekDB Delta HNSW 与 RRF 混合检索在字节预算下如何协同？",
    "max_bytes": 1400,
    "learner_id": "scholar-01"
  }'
```
- **预期结果**：
  1. **SeekDB 混合检索记录**：单条 `UNION ALL` SQL 同时召回 `PAPER-VSAG-01`、`PAPER-PC-02`、`PAPER-RRF-03`；
  2. **PowerContext 上下文信封**：`prepared_context` 以 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 开头，包含 `learner_memory: id=scholar-01 weak_topics=hybrid_rag depth=deep-derivation`，且 `prepared_context_bytes <= 1400`；
  3. **自适应复习闪卡排序**：属于薄弱主题 `hybrid_rag` 的 `PAPER-RRF-03` 闪卡自动排在第一位（`priority: "high_weak_topic"`，`next_review_days: 1`），而已掌握的 `PAPER-VSAG-01` 标记为 `mastered: true`（`priority: "low_mastered"`，复习间隔最长）。

### 步骤 5：验证记忆「并集合并」而非覆盖（回归要点）
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/brief \
  -H "Content-Type: application/json" \
  -d '{
    "research_question": "RRF 倒数排名融合与 HNSW 检索如何协同",
    "max_bytes": 1400,
    "learner_id": "scholar-01",
    "memory": {
      "weak_topics": ["vector_index"],
      "mastered_doc_ids": [],
      "preferred_depth": "standard",
      "review_note": ""
    }
  }'
```
- **预期结果**：`reused_learner_memory.weak_topics` 为 `["hybrid_rag", "vector_index"]`（与步骤 2 已存画像取并集），`mastered_doc_ids` 仍为 `["PAPER-VSAG-01"]`（未被请求里的空数组清空），`review_note` 保留步骤 2 的记录；简报信封内回写 `weak_topics=hybrid_rag,vector_index`。
- **说明**：客户端重复提交画像只会**新增**薄弱点，不会抹掉历史记忆；已掌握文献因是更具体的信号，优先级高于主题级薄弱点，仍被排到队尾。

### 步骤 6：验证文献库与导入
```bash
curl http://127.0.0.1:8093/functions/v1/app/api/scholar/corpus
```
- **预期结果**：返回 `documents`（章节列表）、`papers`（按论文聚合）与 `topics`（主题分布），且不含 `topic='learner_memory'` 的画像行。

导入一篇论文（等价于在网页「导入论文」抽屉中粘贴正文后自动切分的结果）：
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [{
      "doc_id": "PAPER-IMPORT-01",
      "paper_title": "Imported Paper For Scoped Retrieval",
      "section": "Section 1.1: Scoped Retrieval",
      "topic": "agent_workflow",
      "content": "A freshly imported section used to verify scoped retrieval.",
      "key_takeaway": "导入章节可被聚焦检索命中。",
      "flashcard": {"question": "导入的章节能被检索到吗？", "answer": "可以。", "interval_days": 1, "ease": 2.5}
    }]
  }'
```
- **预期结果**：返回 `{"status": "ingested", "count": 1}`，再次调用 `/corpus` 可看到新章节。

### 步骤 7：验证聚焦检索
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/brief \
  -H "Content-Type: application/json" \
  -d '{
    "research_question": "聚焦检索 导入章节",
    "max_bytes": 1400,
    "learner_id": "scope-test-01",
    "scope_doc_ids": ["PAPER-IMPORT-01"]
  }'
```
- **预期结果**：`flashcards` 中只出现 `PAPER-IMPORT-01`，其余文献被排除。

### 步骤 8：验证间隔复习评分推进
```bash
curl -X POST http://127.0.0.1:8093/functions/v1/app/api/scholar/review \
  -H "Content-Type: application/json" \
  -d '{"learner_id": "review-test-01", "doc_id": "PAPER-RRF-03", "grade": 5}'
```
- **预期结果**：返回 `card` 中 `reps=1`、`interval_days=1`、`ease=2.6`、`due_at` 已写入；再以 `grade=4` 调用一次，`reps=2`、`interval_days=3`；以 `grade=0` 调用则 `reps=0`、`lapses=1` 且 `due_at` 立即到期（重新排入到期队列）。评分非法（如 `grade=2`）时返回 400。

### 步骤 9：验证公开站点
用无痕窗口打开 <https://scholar-pulse-noif6uwyn35.qoder.zone>：
- **预期结果**：无需登录即可打开；页面自动生成 `PreparedContext V1` 简报，薄弱主题闪卡带「薄弱点置顶」标记并显示下次复习天数；点击「导入论文」可粘贴正文并自动切分章节后入库，文献库即时新增条目；点击「开始复习」进入逐张评分会话，评分后进度条与间隔同步更新。右上角徽标显示当前检索执行位置（`服务端检索` 或 `浏览器本地计算`）。
