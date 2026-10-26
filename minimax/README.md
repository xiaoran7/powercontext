# MiniMax × OceanBase SeekDB × PowerContext：规范驱动开发（SDD）六维上下文管理系统

> **赛题归属**：2026 OceanBase AI 数据库大赛 · MiniMax 赛题（为 SDD 开发范式设计并实现一套上下文管理系统）
> **分支目录**：`minimax/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、系统设计理念与六大必选上下文维度对齐

在 **SDD（Spec-Driven Development，规范驱动开发）** 范式中，AI Coding Agent 往往在多轮迭代中丢失「某段代码对应哪条 Spec」、「历史为什么否决了方案 B」、「修改模块前有哪些曾踩过的致命陷阱」。本系统基于 **MiniMax-M2.7 + OceanBase SeekDB（单 SQL 向量+全文双索引）+ PowerContext（证据预算装配与交接协议）** 构建了完整的六维 SDD 上下文管理系统：

| 必选上下文维度 | SeekDB 存储标识 (`dimension`) | 结构化元数据 (`metadata_json`) | PowerContext 证据映射 |
| :--- | :--- | :--- | :--- |
| **1. Spec 与代码位置映射** | `spec_code_map` | `file_path`, `symbol_name`, `start_line`, `end_line`, `status` | `SourceRef(source_type="sdd_record")` |
| **2. Spec 验收结果记录** | `spec_validation` | `test_case`, `status` (`PASSED`/`FAILED`), `assertion_detail`, `verified_at` | 单元测试门禁执行后自动回写更新 |
| **3. 关键设计决策及理由** | `decision_rationale` | `rationale`, `rejected_alternatives`, `impact_scope` | 防止 Agent 重蹈已被否决的设计方案 |
| **4. 已知陷阱与反模式** | `known_pitfall` | `symptom`, `root_cause`, `guardrail_rule`, `severity` | 修改代码前强制注入 `guardrail_rule` |
| **5. 活跃任务与依赖关系** | `task_dependency` | `depends_on`, `blocks`, `status`, `verification_gate` | 驱动多阶段任务拓扑排序与门禁校验 |
| **6. 模块间接口契约** | `interface_contract` | `provider_module`, `consumer_module`, `signature`, `invariants` | 保障跨模块重构时不破坏前置/后置不变量 |

---

## 二、五大核心 SDD 查询能力与一键复现指南

### 1. 一键运行完整 SDD 闭环演示（初始化 + 五大查询 + 目标项目 pytest 验收回写）

```bash
export SEEKDB_HOST="100.72.34.104"
export SEEKDB_PORT="2881"
export SEEKDB_USER="root"
export SEEKDB_PASS=""
export SEEKDB_DB="powercontext"

cd minimax
python sdd_cli.py demo
```

### 2. 单独验证五大核心查询

```bash
# 查询 1：指定 Spec 条目的代码映射位置（文件、符号、行号区间）与验收状态
python sdd_cli.py query --type 1 --target SPEC-GW-02

# 查询 2：当 Spec 或模块变更时，查询受影响的下游模块、接口契约与阻塞任务
python sdd_cli.py query --type 2 --target SPEC-GW-01

# 查询 3：通过 SeekDB 向量+全文单 SQL 混合检索历史架构决策理由与被否决方案
python sdd_cli.py query --type 3 --target "为什么要先检查熔断器状态再扣减配额？"

# 查询 4：修改模块前拉取该模块的已知陷阱 (Pitfalls) 与强制防线 (Guardrails)
python sdd_cli.py query --type 4 --target gateway.circuit_breaker

# 查询 5：为执行任务的 Agent 装配字节预算受限的 PreparedContext 信封与 Handoff 交接单
python sdd_cli.py query --type 5 --target SPEC-GW-02 --task-id TASK-GW-102 --max-bytes 1800
```

---

## 三、目标项目演进展示（`target_project/` & `snapshots/`）

本目录包含在 SDD 系统指导下完成多阶段功能演进的真实目标项目 **AI Token 速率限制与三态熔断网关（`target_project/gateway.py`）**：
- **演进前快照**：[`snapshots/before/gateway.py`](./snapshots/before/gateway.py)（初始仅支持固定计数器，存在窗口交界双倍突发流量与拒绝请求污染配额缺陷）。
- **演进后快照**：[`snapshots/after/gateway.py`](./snapshots/after/gateway.py)（完整落地 `SPEC-GW-01` 滑动窗口双桶限流、`SPEC-GW-02` 三态熔断器、`SPEC-GW-03` 可观测性响应头，并严格遵循 `DEC-GW-01`/`DEC-GW-02` 与 `PIT-GW-01`/`PIT-GW-02` 防线）。
- **完整代码演进 Diff**：[`snapshots/sdd_evolution.diff`](./snapshots/sdd_evolution.diff)。

### 运行自动化测试套件（同时验收 SDD 引擎与目标项目）

```bash
pytest minimax/test_minimax_sdd.py minimax/target_project/test_gateway.py -v
```

OB-P:xI1C-CXPJXT4ztVpZDY6_A
