# Qoder 开发过程记录（Qoder Development Log）

> **参赛项目**：ScholarPulse——基于 Qoder × OceanBase SeekDB × PowerContext 的论文精读与自适应闪卡教练
> **Qoder 工作流规则文件**：[`.qoder/rules/scholar_workflow.md`](./.qoder/rules/scholar_workflow.md)
> **线上地址（任何人可访问）**：<https://scholar-pulse-noif6uwyn35.qoder.zone>

---

## 一、Qoder 在项目开发各环节中的实际工作记录

### 环节 1：需求拆解与架构规划（Task Decomposition）
- **输入自然语言需求**：
  > “针对科研文献阅读中‘专业缩写词检索不准、长文塞入窗口超预算、读后缺乏针对薄弱点的间隔复习’三大痛点，结合 OceanBase SeekDB 与 PowerContext 设计一套可运行且可发布到 Qoder Sites 的科研提效系统。”
- **Qoder 拆解产出**：
  1. 制定 `.qoder/rules/scholar_workflow.md` 四阶段科研智能体协同规范；
  2. 设计 `qoder_scholar_store` 表结构，融合 512 维 VSAG HNSW 向量索引与 IK 全文索引；
  3. 设计基于 PowerContext `ArtifactAddress(family="learner_memory")` 的学习者薄弱知识点记忆读写接口与闪卡动态置顶算法；
  4. 设计同时支持本地 FastAPI 后端与 Qoder Sites 托管双模运行的交互式前端 `site/index.html`。

### 环节 2：核心功能编码实现（Coding with Qoder）
- **模块 1（`scholar_engine.py`）**：
  - 实现 `ensure_schema` / `seed_corpus` / `init_and_seed_seekdb` 与 `ingest_research_note`；
  - 实现 `hybrid_search_papers`：通过单条 `UNION ALL` SQL 同时执行 `COSINE_DISTANCE(embedding, %s)` 与 `MATCH(...) AGAINST(...)`，并在服务端完成 RRF（$k=60$）融合；
  - 实现 `save_learner_memory`、`get_learner_memory` 与 `build_research_brief_and_flashcards`：将学习者的薄弱知识点写入 SeekDB，并在生成 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 简报与复习闪卡时自动置顶薄弱主题。
- **模块 2（`server.py` & `site/index.html`）**：
  - 提供 `/status`、`/init`、`/reset`、`/memory`、`/ingest`、`/brief` 接口，并同时挂在 `/api/scholar/*` 与 `/functions/v1/app/api/scholar/*` 两套前缀下，使同一份前端在本地与 Qoder Sites 上行为一致；
  - 构建响应式单页科研工作台 `site/index.html`，支持切换薄弱知识点并实时预览 `PreparedContext V1` 简报、MemoryCitation、概念图谱边集与置顶闪卡。

### 环节 3：联调测试与缺陷修复（Debugging & Verification）
- **调试记录 1**：在验证跨会话学习者记忆时，确保 `hybrid_search_papers` 过滤 `topic != 'learner_memory'`，防止用户画像记录混入论文段落召回结果；
- **调试记录 2**：发布前对三个文件做了一轮完整审计，对照远端 SeekDB 实例逐条复核混合检索、记忆复用与闪卡置顶，修复了以下实际缺陷：
  1. **前端每次请求都覆写学习者记忆**：`site/index.html` 在生成简报前无条件提交 `mastered_doc_ids: []` 与单元素 `weak_topics`，会把已持久化的画像清空，直接破坏「跨会话记忆复用」。改为按并集增量提交，并由服务端 `_merge_memory` 与已存画像合并（新增 `test_learner_memory_is_merged_not_clobbered` 回归）。
  2. **薄弱知识点只能精确匹配主题 slug**：学习者写「RRF」「HNSW」等自然写法时置顶会静默失效。新增 `_weak_hit_ids`：主题精确/近义匹配优先，回落到论文原文关键词命中。
  3. **自适应复习间隔被丢弃**：闪卡的 `interval_days` / `ease` 存了却没有透出，「自适应」只剩排序。现在透传并按优先级推导 `next_review_days`（薄弱 1 天 / 常规 3 天 / 已掌握最长）。
  4. **概念图谱边集语义错误**：原实现把检索结果中相邻的两篇直接连边，并用后者主题命名关系，会把不同主题的文献错误关联。改为只连接共享同一主题的文献（`shares_topic(...)`）。
  5. **已掌握与薄弱点冲突时的优先级未定义**：改为「已掌握优先」，因为它是针对单篇文献的显式信号，不应把已掌握的文献推到队首。
  6. **`GET /status` 会写数据**：拆分为只读 `get_status()`（建表 + 统计）与写入型 `init_and_seed_seekdb()`。
  7. **模块脚本下的内联事件处理器失效**：前端改为 `type="module"` 后 `runBrief` 不再挂在 `window`，`onclick="runBrief()"` 点击无响应。改为 `addEventListener` 绑定，并加断言禁止内联 `onclick`/`onchange`。
  8. **`requirements.txt` 缺 `powercontext`**：未安装时制品地址退化为占位字符串，与文档记录的真实格式不符。
- **调试记录 3**：为 Qoder Sites 静态托管补齐 `functions/`（Edge Function 版引擎 + 业务路由）与 `schema.sql`，并抽出 `functions/engine.mjs` 作为浏览器与 Function 共用的唯一引擎实现；用 `dev/embedding-parity.mjs` 验证浏览器侧 SHA-256 向量与 Python 端 **逐位一致**（`maxDiff = 0`）。

### 环节 3.5：交互界面改版（UI Refresh）
根据评审反馈对 `site/index.html` 做了一轮整体改版：
1. **字体渲染**：原字体栈缺少中文回退，中文段落落到不可控的默认字体。新增包含 `PingFang SC / Microsoft YaHei UI / Source Han Sans SC / Noto Sans CJK SC` 的 CJK 感知字体栈，并为代码面板单独指定含 CJK 回退的等宽栈，正文行高提升到 1.75。
2. **组件现代化**：原生 `<select>` 换成自研下拉组件（浮层面板、选中态、键盘与外部点击关闭）；原生 `<pre>` 换成带标题栏、字节计数与复制按钮的代码面板，并对信封内容做轻量语法着色（转义后再着色，避免注入）；按钮、输入框、标签统一为同一套圆角 / 描边 / 状态规范。
3. **视觉中心对齐**：顶栏品牌标记改为与眉标顶端对齐；研究问题与操作区拆成两行，主按钮与下拉控件同行右对齐；左右两栏与指标条统一使用同一条左边界与 18px 间距节奏。
4. **去掉硬编码**：原先三张写死「120 min → 18 min / 91.4% / 100%」的指标卡与写死的主题下拉选项全部移除，改为由本次检索结果实时推导——双通道命中分布、证据区间条数、字节预算占用率、薄弱点置顶比例；下拉选项与计数由语料 `DEFAULT_RESEARCH_CORPUS` 推导。
5. **排版层次**：建立 `12 / 14 / 16 / 19 / 24 / 34px` 六级字号阶梯与 4 档字重，标题加 `text-wrap: balance` 避免孤字，摘要用 `text-wrap: pretty`，卡片标题、指标标签、区块小标题形成清晰的三级层次。

### 环节 3.6：功能补齐与去硬编码（Feature Completion）
评审反馈「没有导入论文的入口、界面仍是写死数据、交互缺乏动效」，据此完成四项工作：

1. **导入论文入口**：新增侧滑抽屉，支持拖拽 / 选择 `.md`/`.txt` 或粘贴正文；按 Markdown 标题层级自动切分为可检索章节（无标题层级时按空行分段），逐章派生结论与闪卡问答，入库前可逐条编辑；后端新增 `POST /ingest/batch` 批量入库接口。
2. **全面去硬编码**：把原先内嵌在 `scholar_engine.py` 与 `functions/engine.mjs` 中的示例语料抽到唯一数据源 `functions/corpus.json`，Python 端、Edge Function 端与浏览器端读取同一份文件；界面上的主题下拉项、篇数、指标数值全部由语料与本次检索结果实时推导，示例问题改为取自语料首条结论。
3. **新增功能**：
   - 文献库：实时列出全部论文与章节，支持过滤，点击即把检索聚焦到该论文（后端 `scope_doc_ids` 贯穿到单条 SQL 的 `doc_id IN (...)` 过滤）；
   - 间隔复习会话：卡片逐张评分（忘记 / 困难 / 良好 / 掌握），按 SM-2 推进间隔天数、难度系数、连击与到期时间，评分为「忘记」的卡片立即回到期队列首位；新增 `POST /review` 接口与 `cards` 画像字段；
   - 概念图谱与引用区改为随结果实时渲染，并透出下次复习天数与到期状态。
4. **动效与交互**：指标数字滚动动画、卡片与列表交错入场、代码面板扫描态、下拉浮层弹出动画、抽屉 / 弹层滑入、Toast 提示、按钮按压反馈、进度条过渡，全部遵循 `prefers-reduced-motion`。

期间修复了三个实际缺陷：论文切分对数组调用 `.trim()` 导致解析失败、后台标签页下 `requestAnimationFrame` 被节流导致指标数值永远停在初值、语料加载完成后文献库未重新渲染。

### 环节 3.7：深度研发与全面消除 Demo 感（Deep Engineering & Synthesis Overhaul）
针对评委与用户提出的「项目像玩具 Demo、输出模板化硬编码、专业深度不足」的深度反馈，系统进行了工业级科研工作台重构：

1. **学术语料库扩充至 16 篇权威文献**：
   - 全面覆盖计算机系统与人工智能五大核心领域：
     - **分布式数据库与共识**：OceanBase（分区级 Multi-Paxos、LSM-Tree 每日大合并）、Google Spanner（TrueTime 不确定性窗口 [earliest, latest] 与 Commit Wait 外部一致性）、Raft（日志完整性与选票安全约束）；
     - **高维向量检索与混合索引**：OceanBase SeekDB（Delta / Snapshot 双层 HNSW 架构、AVX-512 / ARM SVE SIMD 距离核函数）、Facebook Faiss（IVF-PQ 倒排量化与 ADC 距离查表）、HNSW（指数衰减分层跳表图拓扑）；
     - **大模型计算与审慎推理**：Transformer（缩放点积注意力与 $1/\sqrt{d_k}$ 梯度平抑）、FlashAttention（GPU SRAM Tiling 分块与 IO-Awareness 在线 Softmax）、Tree of Thoughts（思维树启发式剪枝与回溯探索）；
     - **RAG 与确定性上下文工程**：PowerContext（UTF-8 字符级证据切片与 `MemoryCitation`）、RAG（RAG-Sequence 宏观联合与 RAG-Token 微观动态融合）、RRF（倒数排名融合分数校准与异构量纲抹平）、ContextPackDelta（多轮增量上下文传输与 SHA-256 完整性摘要）；
     - **自适应记忆与认知遗忘模型**：SuperMemo SM-2（记忆留存衰减函数与 Easiness Factor 动态自适应）。

2. **章节意图分类与高质量问答派生（Intent-Aware Derivation）**：
   - 彻底废除原先单一模板（`f"{paper_title} 的「{heading}」说明了什么？"`）；
   - 引入 `_detect_section_intent`（机制 mechanism、算法推导 algorithm、技术权衡 tradeoff、核心概念 concept）四分类引擎；
   - 针对不同意图智能派生深入学术本质的问答与关键提炼，并提取多句论据链作为详细答案。

3. **跨文献 AI 综合学术综述（Research Synthesis）**：
   - 新增 `generate_research_synthesis` / `generateResearchSynthesis`：
     - **高层学术结论（Executive Summary）**：综合召回文献提炼跨学科核心洞察；
     - **论点论据链与交互式引用徽标**：在段落中内嵌可点击的 `[PAPER-ID:§Section]` 引用徽标，点击即唤起文献精读弹窗定位证据；
     - **核心架构指标权衡对比（Trade-Offs）**：深入分析一致性 vs 吞吐量（Paxos / TrueTime）、检索精度 vs 显存带宽（HNSW / IVF-PQ）、计算复杂度 vs 访存 IO（FlashAttention SRAM）、全量窗口 vs 严格预算（PowerContext）等底层工程权衡；
     - **开放研究课题（Open Questions）**：提炼跨会话增量记忆复用与极端预算压缩等前沿开放课题。

4. **3D 交互式透视翻转闪卡与 Anki (.tsv) 导出**：
   - 闪卡采用 3D 透视翻转动效（`perspective: 1200px`，`transform-style: preserve-3d`），正反面平滑翻转；
   - 闪卡背面提供详尽学术解答与内联 SM-2 评分按钮（忘记 / 困难 / 良好 / 掌握），评分即时写入学习者画像并触发下一次自适应调度；
   - 新增 **「导出 Anki (.tsv)」** 功能：一键生成符合 Anki 导入标准的制表符文本文件（包含问题、答案、主题标签、文献 ID），方便科研人员同步至移动设备背诵。

5. **交互式 SVG 概念拓扑图谱**：
   - 卡片 04 新增「拓扑图谱」与「边集列表」双视图切换；
   - 采用响应式 SVG 拓扑画布，按五大领域主题色彩自动编码节点；
   - 提供节点悬浮卡片（展示论文名称、主题、章节与核心提炼）与悬浮高亮连线；点击节点即可一键将全站检索聚焦到该论文。

6. **一键填入经典学术文献预设**：
   - 导入论文抽屉新增 5 篇经典预设芯片（OceanBase 分布式架构、Google Spanner TrueTime、FlashAttention SRAM 计算、HNSW 图索引、PowerContext 确定性上下文）；
   - 评委与用户无需寻找 Markdown 文件，点击任一预设即可自动填入标准学术论文全文、自动选择主题并触发实时章节切分与闪卡生成，极大提升演示与评估体验。

7. **全篇文献精读弹窗（Paper Reader Modal）**：
   - 任何地方点击文献卡片、章节行或综述中的引用徽标，即可唤出全屏精读弹窗；
   - 自动在原文中高亮标注本次检索命中的证据切片 `[start:end]`，并展示核心提炼与配套闪卡。

8. **严格双端等价与质量保证**：
   - Python 后端（FastAPI + 远程 SeekDB）与 Edge/Browser JS 引擎保持 100% 算法对齐；
   - `node dev/embedding-parity.mjs` 校验 512 维向量最大差值 `maxDiff = 0`，SHA-256 与 SM-2 调度逐位一致；
   - `node dev/function-check.mjs` 30/30 项自检全部通过；
   - `pytest test_qoder.py` 11/11 项集成测试在远程 Tokyo VPS SeekDB（`100.72.34.104:2881`）上全部通过。

### 环节 4：Qoder Sites 发布步骤（实际执行）
1. 在 Qoder 客户端中打开 `qoder/` 目录（发布目录为 `qoder/dist/`，由 `node dev/build-dist.mjs` 生成）；
2. 通过 **Qoder Sites** 创建站点（`slug: scholar-pulse`），取得 Project/Site 标识；
3. 申请开通数据库与存储服务时，账号订阅计划返回「仅支持部署静态网站」，因此本次发布采用**静态托管**形态：前端在浏览器内执行与后端同源的 `engine.mjs` 检索与装配算法，右上角徽标明确标注「浏览器本地计算」，不冒充服务端结果；
4. 发布并确认 `published: true`、`readiness: publication_completed`；
5. 将站点可见范围设置为 **「任何人都可访问」**（`mode: public`，`confirmPublic: true`，`auth_integration_status: not_required`），无痕窗口可直接打开；
6. 在大赛测试平台填写发布后的 Qoder Sites 网址、代码仓库地址与个人 Qoder 账号 UID。
