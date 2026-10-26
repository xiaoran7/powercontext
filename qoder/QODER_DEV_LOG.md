# Qoder 开发过程记录（Qoder Development Log）

> **参赛项目**：ScholarPulse——基于 Qoder × OceanBase SeekDB × PowerContext 的论文精读与自适应闪卡教练
> **Qoder 工作流规则文件**：[`.qoder/rules/scholar_workflow.md`](./.qoder/rules/scholar_workflow.md)

---

## 一、Qoder 在项目开发各环节中的实际工作记录

### 环节 1：需求拆解与架构规划（Task Decomposition）
- **输入自然语言需求**：
  > “针对科研文献阅读中‘专业缩写词检索不准、长文塞入窗口超预算、读后缺乏针对薄弱点的间隔复习’三大痛点，结合 OceanBase SeekDB 与 PowerContext 设计一套可运行且可发布到 Qoder Sites 的科研提效系统。”
- **Qoder 拆解产出**：
  1. 制定 `.qoder/rules/scholar_workflow.md` 四阶段科研智能体协同规范；
  2. 设计 `qoder_scholar_store` 表结构，融合 512 维 VSAG HNSW 向量索引与 IK 全文索引；
  3. 设计基于 PowerContext `ArtifactAddress(family="learner_memory")` 的学习者薄弱知识点记忆读写接口与闪卡动态置顶算法；
  4. 设计同时支持本地 FastAPI 后端与 Qoder Sites 纯静态托管双模运行的交互式前端 `site/index.html`。

### 环节 2：核心功能编码实现（Coding with Qoder）
- **模块 1（`scholar_engine.py`）**：
  - 实现 `init_and_seed_seekdb(force_reset)` 与 `ingest_research_note`；
  - 实现 `hybrid_search_papers`：通过单条 `UNION ALL` SQL 同时执行 `COSINE_DISTANCE(embedding, %s)` 与 `MATCH(...) AGAINST(...)`，并在服务端完成 RRF（$k=60$）融合；
  - 实现 `save_learner_memory`、`get_learner_memory` 与 `build_research_brief_and_flashcards`：将学习者的薄弱知识点写入 SeekDB，并在生成 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 简报与复习闪卡时自动置顶薄弱主题。
- **模块 2（`server.py` & `site/index.html`）**：
  - 提供 `/api/scholar/status`、`/api/scholar/reset`、`/api/scholar/memory`、`/api/scholar/ingest`、`/api/scholar/brief` 五大接口；
  - 构建响应式单页科研工作台 `site/index.html`，支持切换薄弱知识点并实时预览 `PreparedContext V1` 简报与置顶闪卡。

### 环节 3：联调测试与缺陷修复（Debugging & Verification）
- **调试记录 1**：在验证跨会话学习者记忆时，确保 `hybrid_search_papers` 过滤 `topic != 'learner_memory'`，防止用户画像记录混入论文段落召回结果；
- **调试记录 2**：为 `site/index.html` 增加静态回退计算引擎（`renderStaticFallback`），确保部署到 Qoder Sites 公开静态域名后，评审无需启动本地后端也能在网页上直接交互体验混合检索与薄弱点闪卡置顶效果。

### 环节 4：Qoder Sites 发布步骤
1. 在 Qoder 客户端中打开 `qoder/site/` 目录；
2. 通过 **Qoder Sites** 创建站点并发布 `qoder/site/index.html`；
3. 将站点可见范围设置为 **「任何人都可访问」**（使用个人账号发布，确保无痕浏览窗口可直接打开）；
4. 在大赛测试平台填写发布后的 Qoder Sites 网址、代码仓库地址与个人 Qoder 账号 UID。
