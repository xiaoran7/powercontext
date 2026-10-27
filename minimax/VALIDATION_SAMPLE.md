# SDD 上下文管理系统：标准验证样例（Validation Sample）

## 1. 任务描述与修改范围明确性

- **目标业务工程**：`target_project/gateway.py`（AI API Token 速率限制与三态熔断网关 `TokenBucketGateway`）。
- **要修改什么（Target Changes - 明确的局部修改）**：
  1. **实现 `SPEC-GW-01`（滑动窗口双桶限流）**：将原本仅支持固定累加计数的 `TokenBucketGateway.check_and_consume` 升级为基于单调时钟注入（`clock_fn`）与双端队列（`deque`）的滑动窗口限流器，同时遵循 `PIT-GW-01`（请求被拒时严禁将失败请求写入窗口队列造成永久饥饿）；
  2. **实现 `SPEC-GW-02`（三态熔断器）**：实现 `CLOSED -> OPEN -> HALF_OPEN -> CLOSED` 状态机（`record_upstream_outcome`），遵循 `DEC-GW-02`（入口处先检查熔断状态，`OPEN` 态直接短路拒绝且不扣减租户 Token 配额）与 `PIT-GW-02`（`HALF_OPEN` 探测失败时重置 `_opened_at` 时间戳并维持 `OPEN` 态）；
  3. **实现 `SPEC-GW-03`（可观测性响应头导出）**：完善 `RateLimitDecision.to_headers()`，返回标准字符串键值字典，包含 `X-RateLimit-Allowed`、`X-RateLimit-Remaining-Tokens` 与 `X-Circuit-State`。
- **哪些原有行为必须保持（Regression Requirements - 严格保持向下兼容）**：
  - 在未触发熔断且未超配额的正常请求路径下，`check_and_consume(tenant_id, requested_tokens)` 必须准确扣减配额并返回正确的 `remaining_tokens`（由回归测试 `test_regression_basic_token_consumption` 验证，修改前后均必须 100% 通过）。

---

## 2. 上下文中必须包含的规格、代码与测试来源清单（对齐 2.3 节四项核心定位）

| 类别 | 唯一标识 (`record_id`) | 对应源文件 / 符号 / 测试路径 | 作用与验证要求 |
| :--- | :--- | :--- | :--- |
| **1. 适用规格条目** | `SPEC-GW-01`, `SPEC-GW-02`, `SPEC-GW-03` | 滑动窗口双桶限流、三态熔断器、可观测性 Header 契约 | 明确功能行为边界与状态机规范 |
| **2. 需修改代码与符号** | `SPEC-GW-01`～`03` 映射 | `target_project/gateway.py`：`RateLimitDecision.to_headers` (L18-L35)、`TokenBucketGateway.check_and_consume` (L42-L96)、`TokenBucketGateway.record_upstream_outcome` (L98-L148) | 精准定位需编辑的文件、函数符号与行号区间 |
| **3. 功能与回归测试** | `VAL-GW-01`～`03`、`VAL-REG` | `target_project/test_gateway.py`：`test_circuit_breaker_state_transitions`、`test_token_quota_and_sliding_window_eviction`、`test_gateway_headers_contract`、`test_regression_basic_token_consumption` | 验证新特性落地及原有业务逻辑兼容 |
| **4. 状态复用与反模式防线** | `STATE-TASK-GW-102`、`DEC-GW-02`、`PIT-GW-01`、`PIT-GW-02` | `load_task_state` 读取的已完成/待办清单；熔断优先、拒绝不污染窗口、半开失败刷新时间戳 | 跨重启传递任务进展并阻断常见设计陷阱 |

---

## 3. 操作步骤、预期结果与重置方法（评审验证流程）

### 步骤 1：重置并初始化初始数据
```bash
cd minimax
python sdd_cli.py init --reset
```
- **预期结果**：SeekDB（默认 `127.0.0.1:2881`）中的 `minimax_sdd_context_store` 表被清空并重新导入全部 6 大维度的初始基准数据，输出 `all_six_dimensions_present: true`。

### 步骤 2：查询并装配开发任务上下文
```bash
python sdd_cli.py query --type 5 --target SPEC-GW-02 --task-id TASK-GW-102 --max-bytes 2400
```
- **预期结果**：输出标准 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 信封，准确结构化呈现：
  - `=== 1. TASK STATE & REUSE ===`：输出已完成事项、待办事项与上次验证结论；
  - `=== 2. TARGET CODE LOCATIONS & SYMBOLS TO MODIFY ===`：显式输出目标文件 `target_project/gateway.py`、符号 `TokenBucketGateway.record_upstream_outcome` 与行号区间 `(L98-L148)`；
  - `=== 3. VERIFICATION & REGRESSION TESTS ===`：显式列出目标特性测试与回归测试路径；
  - `=== 4. ARCHITECTURAL DECISIONS & MANDATORY GUARDRAILS ===`：展示 RRF 混合检索召回的决策与防线。
  - 字节长度严格受限在 2400 字节之内。

### 步骤 3：调用 MiniMax 助手完成代码修改并验证
```bash
python sdd_cli.py apply --task-id TASK-GW-102 --target SPEC-GW-02
```
- **预期结果**：MiniMax 助手接收 SDD 上下文完成代码演进，覆盖写入 `target_project/gateway.py`，自动执行全套单元测试，输出 `tests_passed: true` 并通过 PowerContext 将任务标记为最新完成状态。

### 步骤 4：验证规格动态更新与重新导入（非固定死答案）
```bash
python -c "
from sdd_engine import MiniMaxSDDContextEngine
eng = MiniMaxSDDContextEngine()
eng.upsert_sdd_record(
    'SPEC-GW-02', 'spec_code_map', 'SPEC-GW-02', 'gateway.circuit_breaker',
    '上游模型服务三态熔断器 (动态更新冷却阈值30s)',
    '当连续上游失败次数达到 failure_threshold 时进入 OPEN 熔断状态；冷却时间 recovery_timeout_sec=30.0 到期后转入 HALF_OPEN 探测态。',
    {'file_path': 'target_project/gateway.py', 'symbol_name': 'TokenBucketGateway.record_upstream_outcome', 'start_line': 98, 'end_line': 148, 'status': 'IMPLEMENTED'}
)
q1 = eng.query_1_spec_location_and_status('SPEC-GW-02')
print('Query 1 updated:', q1['code_mappings'][0]['title'])
q5 = eng.query_5_assemble_task_context_pack('TASK-GW-102', 'SPEC-GW-02')
print('Query 5 updated contains:', '动态更新冷却阈值30s' in q5['prepared_context'])
"
```
- **预期结果**：Query 1 与 Query 5 输出均立即变为更新后的标题 `上游模型服务三态熔断器 (动态更新冷却阈值30s)`，证明模块实时检索数据库最新内容而非写死答案。

### 步骤 5：验证任务状态持久化保存与重启后复用
```bash
python -c "
from sdd_engine import MiniMaxSDDContextEngine
eng1 = MiniMaxSDDContextEngine()
eng1.save_task_state('TASK-GW-102', 'SPEC-GW-02', ['SPEC-GW-01已落地'], ['完成SPEC-GW-03响应头验证'], 'pytest 4/4 passed')
# 模拟重启：新建独立引擎实例读取状态
eng2 = MiniMaxSDDContextEngine()
loaded = eng2.load_task_state('TASK-GW-102')
print('Reused state:', loaded['state']['completed_items'], loaded['state']['powercontext_artifact']['family'])
"
```
- **预期结果**：新建实例 `eng2` 完整读取出通过 PowerContext Artifact 验证的持久化状态，`family: 'task_state'`，验证重启复用能力。

### 步骤 6：对比修改前（`snapshots/before`）与修改后（`target_project`）同一测试集结果
```bash
pytest test_minimax_sdd.py -v
```
- **预期结果**：同一测试集 `test_gateway.py` 在修改前快照（`snapshots/before`）上回归测试通过、目标特性测试失败（退出码 `1`）；在修改后代码上 4 项测试全部通过（退出码 `0`）。
