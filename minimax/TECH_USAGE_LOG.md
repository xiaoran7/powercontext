# 技术使用记录与测试运行日志（Tech Usage Log）

## 一、MiniMax 参与代码修改开发记录

- **调用模型**：`MiniMax-M2.7`（通过 `sdd_engine.py` 中的 `apply_minimax_code_patch` 与 `_generate_minimax_sdd_guidance` 接入）
- **开发任务**：`TASK-GW-101` / `TASK-GW-102` / `TASK-GW-103`（将 `target_project/gateway.py` 从固定计数器演进为支持滑动窗口双桶限流、三态熔断器与标准响应头导出的生产级网关）
- **提供给 MiniMax 开发助手的 PowerContext 上下文输入**：
  ```text
  BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1
  scope_id: scope:minimax-sdd:token-gateway
  task_id: TASK-GW-102
  target_spec_id: SPEC-GW-02

  === 1. TASK STATE & REUSE ===
  - completed_items: [SPEC-GW-01 租户级滑动窗口双桶 Token 速率限制器]
  - todo_items: [SPEC-GW-02 上游模型服务三态熔断器 (CLOSED / OPEN / HALF_OPEN), SPEC-GW-03 可观测性响应头与降级原因契约导出]
  - last_verification: before snapshot: 1 passed, 3 failed

  === 2. TARGET CODE LOCATIONS & SYMBOLS TO MODIFY ===
  - target_file: target_project/gateway.py
  - target_symbol: TokenBucketGateway.record_upstream_outcome (L98-L148)
  - applicable_spec: [SPEC-GW-02] 上游模型服务三态熔断器 (CLOSED / OPEN / HALF_OPEN)
  - spec_requirement: 当连续上游失败次数达到 failure_threshold 时进入 OPEN 熔断状态；冷却时间 recovery_timeout_sec 到期后转入 HALF_OPEN 探测态，探测成功则恢复 CLOSED，失败则重新计时。

  === 3. VERIFICATION & REGRESSION TESTS ===
  - target_feature_test: target_project/test_gateway.py::test_circuit_breaker_state_transitions
  - target_feature_test_quota: target_project/test_gateway.py::test_token_quota_and_sliding_window_eviction
  - target_feature_test_headers: target_project/test_gateway.py::test_gateway_headers_contract
  - regression_test: target_project/test_gateway.py::test_regression_basic_token_consumption

  === 4. ARCHITECTURAL DECISIONS & MANDATORY GUARDRAILS ===
  - [decision_rationale|DEC-GW-02] 熔断器优先于配额扣减执行 (Circuit-First Evaluation): 决策理由：当上游模型处于 OPEN 熔断状态时，若先扣减租户 Token 配额再报错，会导致租户在故障期间无故损失配额。因此 check_and_consume 必须在入口处先检查熔断器状态，OPEN 态直接短路返回而不消耗 Token。 (citation=DEC-GW-02[0:150])
  - [known_pitfall|PIT-GW-02] 陷阱：HALF_OPEN 半开探测失败后未刷新 opened_at 时间戳: 症状：半开探测请求失败后，下一个请求仍然立即穿透进入上游，熔断冷却失效。根因：HALF_OPEN 失败回退至 OPEN 时未将 opened_at 更新为当前时钟。防线规则：HALF_OPEN 探测失败必须重置 opened_at = clock_fn() 并维持 OPEN 状态。 (citation=PIT-GW-02[0:150])
  - [interface_contract|CTR-GW-01] check_and_consume 网关准入契约: 接口签名：check_and_consume(tenant_id: str, requested_tokens: int) -> RateLimitDecision。不变量：1) requested_tokens > 0；2) 当 circuit_state == 'OPEN' ʱ remaining_tokens 不变且 allowed=False；3) 返回对象的 to_headers() 键值全部为 str。 (citation=CTR-GW-01[0:150])

  END_POWERCONTEXT_PREPARED_CONTEXT_V1
  ```
- **MiniMax 代码修改决策与提交补丁对应关系**：
  1. 依据 `SPEC-GW-03` 在 `RateLimitDecision.to_headers()`（`gateway.py:25-32`）中补齐 5 个标准 HTTP 响应头；
  2. 依据 `DEC-GW-01` 与 `SPEC-GW-01` 在 `TokenBucketGateway._evict_expired`（`gateway.py:63-68`）中使用 `collections.deque` 与注入的 `clock_fn` 驱逐过期窗口请求；
  3. 依据 `DEC-GW-02` 在 `check_and_consume`（`gateway.py:87-96`）入口处先检查 `self._current_circuit_state(now)`，`OPEN` 态直接返回 `allowed=False` 而不扣减租户配额；
  4. 依据 `PIT-GW-01` 在 `gateway.py:99-121` 中先完成请求数与 Token 总额双重检查，仅在通过后执行 `q.append((now, requested_tokens))`；
  5. 依据 `PIT-GW-02` 在 `record_upstream_outcome`（`gateway.py:141-144`）中当 `c_state == "HALF_OPEN"` 探测失败时重置 `self._opened_at = now` 并回到 `"OPEN"`。
  - 上述修改与 [`snapshots/sdd_evolution.diff`](./snapshots/sdd_evolution.diff) 完全一致。

---

## 二、OceanBase SeekDB 数据库查询记录

- **连接配置**：`127.0.0.1:2881`（Database: `powercontext`，Table: `minimax_sdd_context_store`，支持环境变量覆盖）
- **索引结构**：
  - `VECTOR KEY idx_minimax_sdd_hnsw (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG)`
  - `FULLTEXT KEY idx_minimax_sdd_fts (title, content) WITH PARSER ik`
- **实际执行的混合检索 SQL**：
  ```sql
  (
      SELECT record_id, dimension, spec_id, module_name, title, content,
             metadata_json, powercontext_source_ref, 'vector' AS channel,
             COSINE_DISTANCE(embedding, '[...]') AS distance, 0.0 AS fts_score
      FROM minimax_sdd_context_store
      WHERE scope_id = 'scope:minimax-sdd:token-gateway'
      ORDER BY COSINE_DISTANCE(embedding, '[...]') APPROXIMATE LIMIT 16
  )
  UNION ALL
  (
      SELECT record_id, dimension, spec_id, module_name, title, content,
             metadata_json, powercontext_source_ref, 'fts' AS channel,
             NULL AS distance, MATCH(title, content) AGAINST ('TASK-GW-102 SPEC-GW-02 ...' IN NATURAL LANGUAGE MODE) AS fts_score
      FROM minimax_sdd_context_store
      WHERE scope_id = 'scope:minimax-sdd:token-gateway'
        AND MATCH(title, content) AGAINST ('TASK-GW-102 SPEC-GW-02 ...' IN NATURAL LANGUAGE MODE) > 0
      ORDER BY fts_score DESC, record_id ASC LIMIT 16
  )
  ```

---

## 三、PowerContext 任务状态写入与跨实例重启复用记录

- **写入记录（实例 1）**：
  - 调用 `eng1.save_task_state("TASK-GW-102", "SPEC-GW-02", completed_items=["SPEC-GW-01"], todo_items=["SPEC-GW-02", "SPEC-GW-03"], verification_summary="pytest 1 passed, 3 failed")`
  - 使用 PowerContext `Artifact(family='task_state', artifact_id='task-gw-102', revision=1)` 校验并生成地址：
    `scope_id='scope:minimax-sdd:token-gateway' artifact=ArtifactRef(family='task_state', artifact_id='task-gw-102', revision=1)`
- **重启后读取复用记录（新建实例 2）**：
  - 调用 `eng2.load_task_state("TASK-GW-102")`
  - 返回完整状态结构：
    ```json
    {
      "task_id": "TASK-GW-102",
      "spec_id": "SPEC-GW-02",
      "title": "TASK-GW-102 持久化任务进展状态",
      "powercontext_source_ref": "source_type='sdd_record' source_id='STATE-TASK-GW-102'",
      "artifact_address": "scope_id='scope:minimax-sdd:token-gateway' artifact=ArtifactRef(family='task_state', artifact_id='task-gw-102', revision=1)",
      "state": {
        "completed_items": ["SPEC-GW-01"],
        "todo_items": ["SPEC-GW-02", "SPEC-GW-03"],
        "verification_summary": "pytest 1 passed, 3 failed",
        "saved_at": "2026-10-27T14:00:12Z",
        "powercontext_artifact": {
          "family": "task_state",
          "artifact_id": "task-gw-102",
          "revision": 1,
          "content_hash": "54bdedca5b9da793"
        }
      }
    }
    ```

---

## 四、修改前后同一测试集运行日志与退出码

### 1. 修改前代码版本（`snapshots/before/gateway.py`）运行 `test_gateway.py`
- **执行命令**：`PYTHONPATH=minimax/snapshots/before pytest minimax/target_project/test_gateway.py -v`
- **退出码（Exit Code）**：`1`（基线回归测试 `test_regression_basic_token_consumption` **PASSED**；3 项新增目标功能测试因滑动窗口、三态熔断器与响应头尚未实现而断言 **FAILED**）
- **日志摘要**：
  ```text
  target_project/test_gateway.py::test_regression_basic_token_consumption PASSED [ 25%]
  target_project/test_gateway.py::test_token_quota_and_sliding_window_eviction FAILED [ 50%]
  target_project/test_gateway.py::test_circuit_breaker_state_transitions FAILED [ 75%]
  target_project/test_gateway.py::test_gateway_headers_contract FAILED [100%]
  ========================= 3 failed, 1 passed =========================
  ```

### 2. 修改后代码版本（`target_project/gateway.py` / `snapshots/after/gateway.py`）运行 `test_gateway.py`
- **执行命令**：`PYTHONPATH=minimax/target_project pytest minimax/target_project/test_gateway.py -v`
- **退出码（Exit Code）**：`0`（1 项回归测试 + 3 项新增目标功能测试 **4/4 全部 PASSED**）
- **日志摘要**：
  ```text
  target_project/test_gateway.py::test_regression_basic_token_consumption PASSED [ 25%]
  target_project/test_gateway.py::test_token_quota_and_sliding_window_eviction PASSED [ 50%]
  target_project/test_gateway.py::test_circuit_breaker_state_transitions PASSED [ 75%]
  target_project/test_gateway.py::test_gateway_headers_contract PASSED [100%]
  ========================= 4 passed in 0.02s ==========================
  ```
