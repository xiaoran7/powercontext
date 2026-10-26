# ScholarPulse：用 Qoder 打造的论文精读与自适应闪卡教练（运行说明）

> **赛题归属**：2026 OceanBase AI 数据库大赛 · Qoder 赛题（20 分）
> **分支目录**：`qoder/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`
> **线上地址（任何人可访问）**：<https://scholar-pulse-noif6uwyn35.qoder.zone>

---

## 一、提交材料清单导航（对齐赛题第 4 节要求）

| 提交材料类别 | 对应仓库路径 / 说明 |
| :--- | :--- |
| **1. 项目网站源码（Qoder Sites）** | [`site/index.html`](./site/index.html) + [`site/engine.js`](./site/engine.js) + [`site/corpus.json`](./site/corpus.json)（同一份前端同时驱动本地 FastAPI 与 Qoder Sites；已发布为公开站点，见上方线上地址） |
| **2. 完整项目代码** | [`scholar_engine.py`](./scholar_engine.py)、[`server.py`](./server.py)、[`functions/`](./functions)（Qoder Sites Edge Function 版引擎）、[`schema.sql`](./schema.sql)、[`requirements.txt`](./requirements.txt)、[`.env.example`](./.env.example) |
| **3. 运行说明** | [`README.md`](./README.md)（本文档） |
| **4. 验证样例** | [`VALIDATION_SAMPLE.md`](./VALIDATION_SAMPLE.md) 与 [`verification_sample.json`](./verification_sample.json) |
| **5. Qoder 开发记录** | [`QODER_DEV_LOG.md`](./QODER_DEV_LOG.md) 与 [`.qoder/rules/scholar_workflow.md`](./.qoder/rules/scholar_workflow.md) |
| **6. 使用效果材料** | [`USAGE_EFFECT_REPORT.md`](./USAGE_EFFECT_REPORT.md) |

### 已实现的产品功能

| 功能 | 说明 |
| :--- | :--- |
| **AI 跨文献综合综述** | 综合检索召回的多篇文献，提炼高层学术结论（Executive Summary）、论据链与可交互引用徽标 `[PAPER-ID:§Section]`、关键架构指标权衡对比（Trade-Offs）与开放研究课题（Open Questions）。 |
| **3D 翻转闪卡与 Anki 导出** | 闪卡支持 3D 透视平滑翻转，正面展示深度思考题，背面展示学术解答与内联 SM-2 评分按钮；支持一键导出符合 Anki 导入标准的 `.tsv` 制表符文件。 |
| **交互式概念拓扑图谱** | 响应式 SVG 拓扑画布，按 5 大领域主题色彩自动编码节点，支持悬浮卡片展示论文结论，点击节点即可一键将全站检索聚焦到该论文。 |
| **经典文献一键预设** | 导入抽屉提供 OceanBase LSM-Tree、Google Spanner TrueTime、FlashAttention SRAM 计算、HNSW 跳表图索引、PowerContext 上下文等 5 篇经典预设，点击即自动填入并解析。 |
| **文献全篇精读弹窗** | 点击任意引用徽标或文献列表即可唤起精读弹窗，并在原文中精确高亮标注本次检索命中的证据切片 `[start:end]`。 |
| **导入论文与意图分析** | 侧滑抽屉支持拖拽 / 选择 `.md`/`.txt` 或直接粘贴正文，按 Markdown 标题层级自动切分为可检索章节，按「机制/算法/权衡/概念」意图智能派生深入问答。 |
| **文献库** | 实时列出全部 16 篇权威论文与 18 个核心章节，按标题 / 章节过滤；点击任意论文即可把检索聚焦到该论文，检索结果与引用同步收敛。 |
| **间隔复习会话与键盘快捷键** | 沉浸式复习弹窗，支持空格翻转与数字键（1 忘记 / 2 困难 / 3 良好 / 4 掌握）盲操评分，按 SM-2 推进每张卡片的间隔天数、难度系数 `ease`、连击与到期时间。 |
| **自适应闪卡置顶** | 综合「已掌握文献」（单篇级、最具体）> 「到期卡片」> 「薄弱知识点主题」> 普通卡的优先级排序，并透出下次复习间隔。 |
| **PreparedContext V1 装配** | 在严格 UTF-8 字节预算内装配带 `MemoryCitation` 精确字符区间引用的证据信封，并导出 `powercontext.handoff.v1` 交接单。 |

---

## 二、三项核心技术的分工与实际作用（对齐赛题 2.2 节）

| 技术组件 | 在 ScholarPulse 中的具体分工与可核对作用 |
| :--- | :--- |
| **Qoder（必选）** | 承担需求拆解、规则定义（`.qoder/rules/scholar_workflow.md`）、前后端代码实现、缺陷审计与联调，并通过 **Qoder Sites** 发布公开可访问的站点 `scholar-pulse-noif6uwyn35.qoder.zone`（可见范围已设为「任何人可访问」，`auth_integration_status = not_required`）。 |
| **OceanBase SeekDB** | 在 `qoder_scholar_store` 表上同时建立 512 维 `VECTOR INDEX idx_qoder_scholar_hnsw`（VSAG 余弦距离）与 `FULLTEXT INDEX idx_qoder_scholar_fts`（IK 分词），通过单条 `UNION ALL` SQL 完成专业缩写词与自然语言提问的 RRF 混合检索。实测：`RRF 倒数排名融合` 一问中，IK 全文通道返回 `PAPER-RRF-03`（得分 1.281943），向量通道同篇余弦距离 0.884861 居首。 |
| **PowerContext** | 1) 通过 `save_learner_memory` / `get_learner_memory` 以 `ArtifactRef(family="learner_memory")` 制品地址持久化每位学习者的 `weak_topics` 与 `mastered_doc_ids`，跨会话自动置顶薄弱知识点复习闪卡；<br>2) 在严格 `max_bytes` UTF-8 字节预算内截取高相关原文区间并生成 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 简报与 `powercontext.handoff.v1` 交接单。 |

---

## 三、两种运行形态与 API 契约

前端只认一套路径前缀 `/functions/v1/app/api/scholar`，两条后端都提供它，因此 `site/index.html` 无需区分部署形态：

| 形态 | 后端 | 检索执行位置 | 学习者记忆持久化 |
| :--- | :--- | :--- | :--- |
| **本地 / 内网（生产引擎）** | `server.py`（FastAPI）+ **OceanBase SeekDB** | 单条 `UNION ALL` SQL：HNSW 向量通道 + IK 全文通道 + RRF(k=60) | SeekDB `qoder_scholar_store`（`topic='learner_memory'`） |
| **Qoder Sites（已发布）** | 当前订阅仅支持静态站点，前端在浏览器内执行 `functions/engine.mjs` 同源算法 | 浏览器内：512 维哈希向量余弦 + BM25 词法 + RRF(k=60) | 浏览器 `localStorage`（按访问者 `learner_id` 隔离） |

`functions/` 中的 Edge Function（`index.ts` / `routes.mjs` / `engine.mjs`）与 `schema.sql` 已按 Qoder Sites Function backend 规范实现并通过本地无凭据回归（`dev/function-check.mjs` 20/20 通过）；待账号开通数据库与存储服务后即可直接挂载启用，无需改动前端。

接口一览（前缀 `/functions/v1/app/api/scholar`，本地等价于 `/api/scholar`）：

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/status` | 只读探针：仅统计，不写语料 |
| POST | `/init` / `/reset` | 初始化语料 / 重置（含学习者记忆） |
| GET | `/corpus` | 文献库：章节列表、按论文聚合视图与主题分布 |
| POST | `/memory` | 保存学习者画像（与已存画像取并集，不覆盖） |
| GET | `/memory/{learner_id}` | 跨实例读取学习者画像 |
| POST | `/review` | 记录一次复习评分，按 SM-2 推进间隔与到期时间 |
| POST | `/ingest` / `/ingest/batch` | 单条或批量入库论文章节（自动生成 512 维向量与闪卡） |
| POST | `/brief` | 混合检索 + 字节预算装配 + 自适应闪卡（可传 `scope_doc_ids` 聚焦） |

> 语料的唯一数据源是 [`functions/corpus.json`](./functions/corpus.json)：Python 端、Edge Function 端与浏览器端读取同一份文件，代码中不再内嵌示例数据。

---

## 四、运行环境与从安装到启动的完整步骤

### 1. 环境要求与依赖安装
- Python `>= 3.10`
- OceanBase SeekDB 实例（支持 MySQL 协议连接）

```bash
pip install -r qoder/requirements.txt
```

> `requirements.txt` 含 `powercontext>=1.4.0`：安装后 `ArtifactAddress` / `ArtifactRef` 制品地址才会按 `VALIDATION_SAMPLE.md` 记录的真实格式渲染（`scope_id='…' artifact=ArtifactRef(family='learner_memory', …)`），否则会退化为本地占位字符串。

### 2. 配置数据库连接（参照 `qoder/.env.example`）
```bash
export SEEKDB_HOST="100.72.34.104"
export SEEKDB_PORT="2881"
export SEEKDB_USER="root"
export SEEKDB_PASS=""
export SEEKDB_DB="powercontext"
```

### 3. 启动服务并体验功能
```bash
cd qoder
python server.py
```
- 打开浏览器访问 `http://127.0.0.1:8093` 即可使用可视化精读简报与自适应复习闪卡工作台（本地形态走真实 SeekDB 检索）。
- `HOST` / `PORT` 环境变量可覆盖监听地址与端口（默认 `127.0.0.1:8093`）。

### 4. 运行自动化验收测试
```bash
pytest qoder/test_qoder.py -v          # 11 项：检索 / 记忆复用 / 闪卡置顶 / 预算守恒 / 文献库 / 导入 / 复习评分
node qoder/dev/function-check.mjs      # 30 项：Edge Function 业务路由无凭据回归
node qoder/dev/embedding-parity.mjs    # 校验浏览器与 Python 引擎向量、SM-2 调度、论文切分完全一致
```

### 5. 构建 Qoder Sites 发布目录
```bash
node qoder/dev/build-dist.mjs          # 生成 dist/index.html + dist/engine.js
```

OB-P:xI1C-CXPJXT4ztVpZDY6_A
