# 规有所依：面向软件持续演进的 SDD 上下文管理系统（一页技术说明）

## 一、模块结构（Module Architecture）

本系统围绕 **规格驱动开发（Spec-Driven Development, SDD）** 范式构建，由以下四个核心模块组成：
1. **工程资料存储与双索引检索层（`MiniMaxSDDContextEngine` + OceanBase SeekDB）**：
   - 在 SeekDB（`minimax_sdd_context_store` 表）中统一持久化 **6 类 SDD 工程上下文维度**：规格与代码映射（`spec_code_map`）、测试验收记录（`spec_validation`）、设计决策及理由（`decision_rationale`）、已知陷阱与防线（`known_pitfall`）、任务依赖与进展状态（`task_dependency`）、模块间接口契约（`interface_contract`）。
   - 同时建立 `VECTOR INDEX idx_minimax_sdd_hnsw`（VSAG HNSW 余弦距离）与 `FULLTEXT INDEX idx_minimax_sdd_fts`（IK 分词），支持通过单条 `UNION ALL` SQL 进行混合召回与 RRF（$k=60$）排序，并支持通过 `upsert_sdd_record` 动态更新规格后立即生效。
2. **PowerContext 上下文装配与任务状态复用层（`PowerContext` Integration）**：
   - 使用 `SourceRef` 锚定每条规格、代码与测试来源；
   - 在严格 UTF-8 字节预算（`max_bytes`）内装配 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1 ... END_POWERCONTEXT_PREPARED_CONTEXT_V1` 标准信封与 `powercontext.handoff.v1` 交接制品；
   - 提供 `save_task_state` 与 `load_task_state`，利用 `ArtifactAddress(scope_id=..., artifact=ArtifactRef(family="task_state", ...))` 保存已完成事项、待办事项和验证结果，支持模块进程重启后完整读取并复用于下一阶段开发任务。
3. **MiniMax 开发助手协同层（`MiniMax-M2.7` Agent Integration）**：
   - 将装配好的 SDD 上下文提供给 `MiniMax-M2.7` 模型完成对目标工程 `target_project/gateway.py` 的代码修改，并记录完整的上下文输入与补丁生成过程（见 [`TECH_USAGE_LOG.md`](./TECH_USAGE_LOG.md)）。
4. **可验证目标业务工程（`target_project/` & `snapshots/`）**：
   - 目标工程为 **AI Token 速率限制与三态熔断网关（`TokenBucketGateway`）**，提供修改前快照 `snapshots/before/gateway.py`、修改后快照 `snapshots/after/gateway.py`、统一差异补丁 `snapshots/sdd_evolution.diff` 与一致性测试套件 `target_project/test_gateway.py`。

---

## 二、上下文选择方式（Context Selection Strategy）

针对一项具体开发任务（如 `TASK-GW-102` / `SPEC-GW-02`），模块按三级优先级筛选并组装上下文：
1. **精确规格与模块锚定**：优先匹配与目标 `spec_id` 或 `module_name` 直接绑定的规格条目、代码符号位置（文件 + 起始/结束行号）、关联回归与功能测试路径；
2. **SeekDB 向量 + 全文 RRF 混合召回**：对自然语言任务描述执行单 SQL 混合检索，召回相关的架构决策（如 `DEC-GW-02` 熔断优先于配额扣减）与已知反模式陷阱（如 `PIT-GW-01` 拒绝请求不得写入窗口队列、`PIT-GW-02` 半开探测失败需刷新 `opened_at`）；
3. **PowerContext 字节预算截断与状态注入**：自动合并已保存的前序任务状态（已完成事项、待办事项、上次测试结果），在 `max_bytes` 字节上限内生成带字符区间引用（`citation`）的紧凑上下文包。

---

## 三、三项核心技术的分工（Division of Responsibilities）

| 技术组件 | 在本系统中的实际职责与验证锚点 |
| :--- | :--- |
| **MiniMax (`MiniMax-M2.7`)** | 接收 SDD 上下文包，依据 `SPEC-GW-01`～`03`、`DEC-GW-01`～`02` 与 `PIT-GW-01`～`02` 完成 `target_project/gateway.py` 的代码修改，消除固定计数器缺陷并实现滑动窗口双桶限流与三态熔断器。 |
| **OceanBase SeekDB** | 存储规格、代码位置映射、测试路径、决策与陷阱资料，通过 `idx_minimax_sdd_hnsw` + `idx_minimax_sdd_fts` 提供实时混合查询，并在规格动态更新重导入后实时更新检索结果。 |
| **PowerContext** | 构造带 `SourceRef` 与 `MemoryCitation` 的 `PreparedContext V1` 开发上下文信封，持久化保存并跨重启复用任务进展状态（`completed_items` / `todo_items` / `verification_summary`）。 |
