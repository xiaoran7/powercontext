# ScholarPulse：用 Qoder 打造的论文精读与自适应闪卡教练（运行说明）

> **赛题归属**：2026 OceanBase AI 数据库大赛 · Qoder 赛题（20 分）
> **分支目录**：`qoder/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、提交材料清单导航（对齐赛题第 4 节要求）

| 提交材料类别 | 对应仓库路径 / 说明 |
| :--- | :--- |
| **1. 项目网站源码（Qoder Sites）** | [`site/index.html`](./site/index.html)（包含项目简介、解决痛点、核心功能、交互式体验入口、使用效果看板与代码仓库链接，可直接发布至 Qoder Sites） |
| **2. 完整项目代码** | [`scholar_engine.py`](./scholar_engine.py)、[`server.py`](./server.py)、[`requirements.txt`](./requirements.txt)、[`.env.example`](./.env.example) |
| **3. 运行说明** | [`README.md`](./README.md)（本文档） |
| **4. 验证样例** | [`VALIDATION_SAMPLE.md`](./VALIDATION_SAMPLE.md) 与 [`verification_sample.json`](./verification_sample.json) |
| **5. Qoder 开发记录** | [`QODER_DEV_LOG.md`](./QODER_DEV_LOG.md) 与 [`.qoder/rules/scholar_workflow.md`](./.qoder/rules/scholar_workflow.md) |
| **6. 使用效果材料** | [`USAGE_EFFECT_REPORT.md`](./USAGE_EFFECT_REPORT.md) |

---

## 二、三项核心技术的分工与实际作用（对齐赛题 2.2 节）

| 技术组件 | 在 ScholarPulse 中的具体分工与可核对作用 |
| :--- | :--- |
| **Qoder（必选）** | 承担需求拆解、规则定义（`.qoder/rules/scholar_workflow.md`）、前后端代码实现与调试，并通过 **Qoder Sites** 发布公开可访问的项目网站 `site/index.html`。 |
| **OceanBase SeekDB** | 在 `qoder_scholar_store` 表上同时建立 512 维 `VECTOR INDEX idx_qoder_scholar_hnsw`（VSAG 余弦距离）与 `FULLTEXT INDEX idx_qoder_scholar_fts`（IK 分词），通过单条 `UNION ALL` SQL 完成专业缩写词与自然语言提问的 RRF 混合检索。 |
| **PowerContext** | 1) 通过 `save_learner_memory` / `get_learner_memory` 持久化保存每位学习者的薄弱知识点（`weak_topics`）与已掌握文献（`mastered_doc_ids`），在后续会话中自动置顶薄弱知识点复习闪卡；<br>2) 在严格 `max_bytes` UTF-8 字节预算内截取高相关原文区间并生成 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 简报与 `powercontext.handoff.v1` 交接单。 |

---

## 三、运行环境与从安装到启动的完整步骤

### 1. 环境要求与依赖安装
- Python `>= 3.10`
- OceanBase SeekDB 实例（支持 MySQL 协议连接）

```bash
pip install -r qoder/requirements.txt
```

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
- 打开浏览器访问 `http://127.0.0.1:8093` 即可使用可视化精读简报与自适应复习闪卡工作台。
- 按照 [`VALIDATION_SAMPLE.md`](./VALIDATION_SAMPLE.md) 中的步骤可执行初始化重置、学习者薄弱点记忆写入、跨实例重启读取以及混合检索简报生成。

### 4. 运行自动化验收测试
```bash
pytest qoder/test_qoder.py -v
```

OB-P:xI1C-CXPJXT4ztVpZDY6_A
