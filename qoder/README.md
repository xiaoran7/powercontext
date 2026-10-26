# ScholarPulse：基于 Qoder × OceanBase SeekDB × PowerContext 的论文精读与自适应闪卡复习助手

> **赛题归属**：2026 OceanBase AI 数据库大赛 · Qoder 赛题（使用 Qoder + seekdb + PowerContext 完成一个提升学习、工作、研究效率的项目）
> **分支目录**：`qoder/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、痛点分析与核心价值

科研人员与开发者在阅读大量计算机系统论文与技术文档时，常常面临三大效率瓶颈：
1. **跨文献概念检索割裂**：纯向量检索容易遗漏特定算法缩写与参数名（如 `VSAG`、`Delta HNSW`、`RRF k=60`），而纯关键词搜索无法理解跨章节的机制提问；
2. **上下文窗口噪声过大**：直接将整篇论文塞给大模型既超出窗口预算又容易稀释核心论据，缺乏字符级原文引用溯源；
3. **读后即忘、缺乏闭环复习**：精读笔记沉淀后缺少自动化的间隔重复闪卡（Spaced-Repetition Flashcards）与跨会话研究交接单。

**ScholarPulse** 通过 **Qoder + OceanBase SeekDB + PowerContext** 三者深度协同解决上述痛点：
- **Qoder 规则与静态站点能力**：通过 [`.qoder/rules/scholar_workflow.md`](./.qoder/rules/scholar_workflow.md) 定义四阶段科研 Agent 协同规范，并提供可直接一键发布至 **Qoder Sites** 的交互式静态学习门户 [`site/index.html`](./site/index.html)；
- **OceanBase SeekDB（`100.72.34.104:2881`）**：在 `qoder_scholar_store` 表上同时构建 `VECTOR INDEX idx_qoder_scholar_hnsw`（512 维余弦 HNSW）与 `FULLTEXT INDEX idx_qoder_scholar_fts`（IK 分词），通过单条 `UNION ALL` SQL 完成算法术语与语义的 RRF 混合召回；
- **PowerContext**：将论文段落采集为 `SourceRef(source_type="paper_section")`，在严格受限的 UTF-8 字节预算（`max_bytes`）内截取高相关证据区间并封装 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 信封，同步导出 `powercontext.handoff.v1` 跨会话研究交接单与自适应复习闪卡。

---

## 二、快速复现指南

### 1. 启动 ScholarPulse 服务

```bash
export SEEKDB_HOST="100.72.34.104"
export SEEKDB_PORT="2881"
export SEEKDB_USER="root"
export SEEKDB_PASS=""
export SEEKDB_DB="powercontext"

cd qoder
python server.py
```

浏览器打开 `http://127.0.0.1:8093` 即可使用可视化精读简报与复习闪卡工作台；静态目录 `qoder/site/` 亦可直接通过 Qoder Sites 一键发布为公开网页。

### 2. 运行自动化验收测试

```bash
pytest qoder/test_qoder.py -v
```

OB-P:xI1C-CXPJXT4ztVpZDY6_A
