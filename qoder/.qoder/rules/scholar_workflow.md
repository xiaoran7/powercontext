# Qoder Multi-Agent Research & Study Workflow Rule (`scholar_workflow.md`)

## 1. 适用范围与目标
本规则定义了在 **Qoder IDE / Qoder CLI** 中开发与运行 **ScholarPulse（论文精读、概念图谱与自适应闪卡复习助手）** 的四步标准工作流，协同调用 **OceanBase SeekDB（`100.72.34.104:2881`）** 与 **PowerContext**。

## 2. 四阶段协同管线
1. **Stage 1 - 文献结构化采集 (`PaperIngestor`)**：
   - 将 PDF/Markdown 论文按章节解析为 `CAPTURED` Source（`SourceRef(source_type="paper_section", source_id=doc_id)`），同步提取 512 维特征向量并写入 SeekDB `qoder_scholar_store` 表。
2. **Stage 2 - 单条 SQL 向量+全文混合召回 (`SeekDBHybridRetriever`)**：
   - 针对研究问题同时触发 `VECTOR INDEX idx_qoder_scholar_hnsw`（`COSINE_DISTANCE`）与 `FULLTEXT INDEX idx_qoder_scholar_fts`（`MATCH ... AGAINST`），使用 RRF ($k=60$) 融合排序。
3. **Stage 3 - 字节预算受限的研究简报装配 (`PowerContextBriefAssembler`)**：
   - 在严格 `max_bytes` UTF-8 字节预算内筛选高相关原文区间 `[0:end_pos]`，输出带 `MemoryCitation` 的 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 信封。
4. **Stage 4 - 间隔重复闪卡与跨会话交接 (`FlashcardSynthesizer`)**：
   - 自动生成问答闪卡（Flashcards）与概念关联图谱，并导出 `powercontext.handoff.v1` 供下一次研究会话无缝续接。

OB-P:xI1C-CXPJXT4ztVpZDY6_A
