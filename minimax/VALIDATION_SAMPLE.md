# SDD 上下文管理系统：标准验证样例（Validation Sample）

## 1. 任务描述与目标

- **目标业务工程**：`target_project/gateway.py`（AI API Token 速率限制与熔断网关 `TokenBucketGateway`）。
- **要修改什么（Target Changes）**：
  1. **实现 `SPEC-GW-01`（滑动窗口双桶限流）**：将原本仅支持固定累加计数的 `TokenBucketGateway.check_and_consume` 升级为基于单调时钟注入（`clock_fn`）与双端队列（`deque`）的滑动窗口限流器，同时遵循 `PIT-GW-01`（请求被拒时严禁将失败请求写入窗口队列）；
  2. **实现 `SPEC-GW-02`（三态熔断器）**：实现 `CLOSED -> OPEN -> HALF_OPEN -> CLOSED` 状态机（`record_upstream_outcome`），遵循 `DEC-GW-02`（入口处先检查熔断状态，`OPEN` 态直接拒绝且不扣减租户 Token 配额）与 `PIT-GW-02`（`HALF_OPEN` 探测失败时刷新 `_opened_at` 时间戳）；
  3. **实现 `SPEC-GW-03`（可观测性响应头导出）**：完善 `RateLimitDecision.to_headers()`，返回标准字符串键值字典。
- **哪些原有行为必须保持（Regression Requirements）**：
  - 在未触发熔断且未超配额的正常请求路径下，`check_and_consume(tenant_id, requested_tokens)` 必须准确扣减配额并返回正确的 `remaining_tokens`（由回归测试 `test_regression_basic_token_consumption` 验证，修改前后均必须通过）。

---

## 2. 上下文中必须包含的规格、代码与测试来源清单

| 类别 | 唯一标识 (`record_id`) | 对应源文件 / 符号 / 测试路径 |
| :--- | :--- | :--- |
| **适用规格条目** | `SPEC-GW-01`, `SPEC-GW-02`, `SPEC-GW-03` | 滑动窗口双桶限流、三态熔断器、可观测性 Header 契约 |
| **设计决策与防线** | `DEC-GW-01`, `DEC-GW-02`, `PIT-GW-01`, `PIT-GW-02` | 单调时钟注入、熔断优先评估、拒绝不写窗口、半开失败刷新时间戳 |
| **需修改的代码文件与符号** | `SPEC-GW-01`～`03` 映射 | `target_project/gateway.py`：`RateLimitDecision.to_headers` (L18-L35)、`TokenBucketGateway.check_and_consume` (L42-L96)、`TokenBucketGateway.record_upstream_outcome` (L98-L148) |
| **验证与回归测试** | `VAL-GW-01`～`03` | `target_project/test_gateway.py`：`test_regression_basic_token_consumption`、`test_token_quota_and_sliding_window_eviction`、`test_circuit_breaker_state_transitions`、`test_gateway_headers_contract` |

---

## 3. 操作步骤、预期结果与重置方法

### 步骤 1：重置并初始化初始数据
```bash
cd minimax
python -c "from sdd_engine import MiniMaxSDDContextEngine; print(MiniMaxSDDContextEngine().init_and_seed_seekdb(force_reset=True))"
```
- **预期结果**：远端 SeekDB（`100.72.34.104:2881`）中的 `minimax_sdd_context_store` 表被清空并重新导入全部 6 大维度的初始规格、代码映射与测试记录，`all_six_dimensions_present: True`。

### 步骤 2：查询并装配开发任务上下文
```bash
python sdd_cli.py query --type 5 --target SPEC-GW-02 --task-id TASK-GW-102 --max-bytes 1800
```
- **预期结果**：输出包含 `SPEC-GW-02`、`DEC-GW-02`、`PIT-GW-02`、`target_project/gateway.py` 及 `test_gateway.py` 的 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 信封，且字节数严格 $\le 1800$。

### 步骤 3：验证规格动态更新与重新导入
```bash
python -c "
from sdd_engine import MiniMaxSDDContextEngine
eng = MiniMaxSDDContextEngine()
eng.upsert_sdd_record('SPEC-GW-02', 'spec_code_map', 'SPEC-GW-02', 'gateway.circuit_breaker', '三态熔断器(动态更新冷却阈值30s)', '支持自定义冷却阈值 recovery_timeout_sec=30.0 并在半开态自动探测。', {'file_path': 'target_project/gateway.py', 'symbol_name': 'TokenBucketGateway.record_upstream_outcome'})
print(eng.query_1_spec_location_and_status('SPEC-GW-02')['code_mappings'][0]['title'])
"
```
- **预期结果**：输出立即变为更新后的标题 `三态熔断器(动态更新冷却阈值30s)`，证明模块实时读取数据库最新内容而非返回固定答案。

### 步骤 4：验证任务状态保存与重启后复用
```bash
python -c "
from sdd_engine import MiniMaxSDDContextEngine
eng1 = MiniMaxSDDContextEngine()
eng1.save_task_state('TASK-GW-102', 'SPEC-GW-02', ['SPEC-GW-01已落地'], ['完成SPEC-GW-03响应头验证'], 'pytest 4/4 passed')
# 模拟重启：新建独立引擎实例读取状态
eng2 = MiniMaxSDDContextEngine()
print(eng2.load_task_state('TASK-GW-102'))
"
```
- **预期结果**：新实例 `eng2` 完整读取到 `TASK-GW-102` 的已完成事项、待办事项与验证结果。

### 步骤 5：对比修改前（`snapshots/before`）与修改后（`snapshots/after`）同一测试集结果
```bash
pytest minimax/test_minimax_sdd.py -v
```
- **预期结果**：同一测试集 `test_gateway.py` 在 `snapshots/before` 上回归测试通过、3 个新特性测试因目标行为未实现而断言失败（退出码 `1`）；在修改后的 `target_project`（即 `snapshots/after`）上 4 项测试全部通过（退出码 `0`）。
