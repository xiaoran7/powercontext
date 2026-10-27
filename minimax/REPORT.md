# 规有所依：面向软件持续演进的 SDD 上下文管理系统（一页技术说明）

## 一、模块结构（Module Architecture）

本系统围绕 **规格驱动开发（Spec-Driven Development, SDD）** 范式构建，由以下四个核心模块组成：
1. **工程资料存储与双索引检索层（`MiniMaxSDDContextEngine` + OceanBase SeekDB）**：
   - 在 SeekDB（`minimax_sdd_context_store` 表）中统一持久化 **6 类 SDD 工程上下文维度**：规格与代码映射（`spec_code_map`）、测试验收记录（`spec_validation`）、设计决策及理由（`decision_rationale`）、已知陷阱与防线（`known_pitfall`）、任务依赖与进展状态（`task_dependency`）、模块间接口契约（`interface_contract`）。
   - 同时建立 `VECTOR INDEX idx_minimax_sdd_hnsw`（VSAG HNSW 余弦距离）与 `FULLTEXT INDEX idx_minimax_sdd_fts`（IK 分词），支持通过单条 `UNION ALL` SQL 进行混合召回与 RRF（$k=60$）排序，并支持通过 `upsert_sdd_record` 动态更新规格后立即生效。内置本地 SeekDB 引导器（`seekdb_bootstrap.py`）确保干净环境零门槛运行。
2. **PowerContext 制品装配与任务状态复用层（`PowerContext` Integration）**：
   - 使用 `SourceRef` 锚定每条规格、代码与测试来源；
   - 使用 `powercontext.artifacts.Artifact` 与 `ArtifactDraft` 标准数据结构校验持久化任务状态，生成标准的 `ArtifactAddress(scope_id=..., artifact=ArtifactRef(family="task_state", ...))`；
   - 在严格 UTF-8 字节预算（`max_bytes`）内装配 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1 ... END_POWERCONTEXT_PREPARED_CONTEXT_V1` 标准信封，显式输出 4 大板块：任务状态复用、目标代码位置与修改符号、验证与回归测试集、架构决策与防线规则，并输出 `powercontext.handoff.v1` 交接制品。
3. **MiniMax 开发助手协同层（`MiniMax-M2.7` Agent Integration）**：
   - 提供 `apply_minimax_code_patch` 与 CLI `apply` 命令，将装配好的 SDD 上下文提供给 `MiniMax-M2.7` 模型执行对目标工程 `target_project/gateway.py` 的代码修改，自动执行全套单元测试门禁并回写最新验证结论至 SeekDB 与 PowerContext。
4. **可验证目标业务工程（`target_project/` & `snapshots/`）**：
   - 目标工程为 **AI Token 速率限制与三态熔断网关（`TokenBucketGateway`）**，提供修改前快照 `snapshots/before/gateway.py`、修改后快照 `snapshots/after/gateway.py`、统一差异补丁 `snapshots/sdd_evolution.diff` 与一致性测试套件 `target_project/test_gateway.py`。

---

## 二、上下文选择方式（Context Selection Strategy）

针对一项具体开发任务（如 `TASK-GW-102` / `SPEC-GW-02`），模块按确定性+语义混合的三级策略筛选并组装上下文：
1. **确定性目标规格与代码符号锚定**：绝对优先提取目标 `spec_id` 对应的 `spec_code_map` 条目，显式输出需要修改的文件路径（`target_project/gateway.py`）、函数符号（`TokenBucketGateway.record_upstream_outcome`）与行号区间（`L98-L148`），杜绝因模糊搜索排名丢失修改位置；
2. **状态复用与测试集注入**：自动读取 PowerContext 保存的前序任务状态（`completed_items`, `todo_items`, `verification_summary`），并注入目标功能测试与基准回归测试路径；
3. **SeekDB 向量 + 全文 RRF 混合检索**：对任务描述执行单 SQL 混合检索，召回相关的架构决策（如 `DEC-GW-02` 熔断优先于配额扣减）与已知反模式陷阱（如 `PIT-GW-01` 拒绝请求不得写入窗口队列、`PIT-GW-02` 半开探测失败需刷新 `opened_at`）；
4. **PowerContext 字节预算受控截断**：在 `max_bytes` 字节上限内截断辅助决策条目，生成带字符区间引用（`citation`）的紧凑上下文包。

---

## 三、三项核心技术的分工（Division of Responsibilities）

| 技术组件 | 在本系统中的实际职责与验证锚点 |
| :--- | :--- |
| **MiniMax (`MiniMax-M2.7`)** | 接收 SDD 上下文包，依据 `SPEC-GW-01`～`03`、`DEC-GW-01`～`02` 与 `PIT-GW-01`～`02` 完成 `target_project/gateway.py` 的代码演进，消除固定计数器缺陷并实现滑动窗口双桶限流与三态熔断器。 |
| **OceanBase SeekDB** | 存储规格、代码位置映射、测试路径、决策与陷阱资料，通过 `idx_minimax_sdd_hnsw` + `idx_minimax_sdd_fts` 提供实时单 SQL 混合查询，支持规格动态更新重导入及本地自动拉起引导。 |
| **PowerContext** | 采用 `ArtifactDraft` 与 `Artifact` 模型校验并持久化任务状态，生成带 `SourceRef` 与 `MemoryCitation` 的 `PreparedContext V1` 上下文信封，支撑跨进程重启后的状态无损复用。 |
