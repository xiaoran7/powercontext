import sys
from pathlib import Path

# 仓库内 dify/minimax/qoder/stepfun 各有一个 server.py；从仓库根统一跑 pytest 时，
# 裸 `from server import ...` 可能命中别的目录。先把本目录与仓库根放到 sys.path 首位，
# 并清掉同名模块缓存，确保导入的是本目录文件与 powercontext 核心库。
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
for p in [str(_REPO_ROOT), str(_HERE)]:
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)

for _stale in ("server", "sdd_engine", "seekdb_bootstrap"):
    sys.modules.pop(_stale, None)

import os
from pathlib import Path
import subprocess
import sys
from fastapi.testclient import TestClient
from server import app
from sdd_engine import MiniMaxSDDContextEngine


client = TestClient(app)


def test_six_mandatory_sdd_dimensions_in_seekdb():
    """必做项 1 & 4：能够运行 + SeekDB 数据库实际保存并索引 6 大维度工程资料"""
    resp = client.get("/api/sdd/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "initialized"
    assert data["all_six_dimensions_present"] is True
    for dim in MiniMaxSDDContextEngine.REQUIRED_DIMENSIONS:
        assert data["dimension_counts"].get(dim, 0) >= 1


def test_five_core_sdd_queries_and_dynamic_spec_update():
    """
    必做项 3：上下文正确（包含必要规格、代码文件、符号、测试来源与持久化状态，
    且规格更新重导入后立即使用更新内容，满足 2.3 节与 4.1 节判定条件）。
    """
    # 先预先写入一次任务状态，供上下文装配时复用
    eng = MiniMaxSDDContextEngine()
    eng.save_task_state(
        task_id="TASK-GW-102",
        spec_id="SPEC-GW-02",
        completed_items=["SPEC-GW-01 滑动窗口双桶限流"],
        todo_items=["SPEC-GW-02 三态熔断器与半开探测", "SPEC-GW-03 网关响应头验证"],
        verification_summary="before snapshot: 1 passed, 3 failed",
    )

    # Query 1: Spec 代码位置映射与验收状态
    q1 = client.post("/api/sdd/query", json={"query_type": 1, "target": "SPEC-GW-02"}).json()
    assert q1["query_type"] == "1_spec_location_and_validation"
    assert len(q1["code_mappings"]) >= 1
    assert q1["code_mappings"][0]["metadata"]["file_path"] == "target_project/gateway.py"
    assert len(q1["validations"]) >= 1

    # Query 2: 变更影响范围分析
    q2 = client.post("/api/sdd/query", json={"query_type": 2, "target": "SPEC-GW-01"}).json()
    assert q2["query_type"] == "2_change_impact_analysis"
    assert len(q2["impacted_modules"]) >= 1

    # Query 3: 历史设计决策理由与被否决方案
    q3 = client.post(
        "/api/sdd/query",
        json={"query_type": 3, "target": "为什么要先检查熔断器状态再扣减配额"},
    ).json()
    assert len(q3["decisions"]) >= 1

    # Query 4: 模块已知陷阱与强制防线规则
    q4 = client.post(
        "/api/sdd/query",
        json={"query_type": 4, "target": "gateway.circuit_breaker"},
    ).json()
    assert len(q4["mandatory_guardrails"]) >= 1

    # Query 5: 任务级 PreparedContext 字节预算装配与 Handoff（严守 2.3 节四项核心定位）
    max_bytes = 2400
    q5 = client.post(
        "/api/sdd/query",
        json={
            "query_type": 5,
            "target": "SPEC-GW-02",
            "task_id": "TASK-GW-102",
            "max_bytes": max_bytes,
        },
    ).json()
    ctx_text = q5["prepared_context"]
    assert ctx_text.startswith("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert ctx_text.endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert q5["prepared_context_bytes"] <= max_bytes
    assert q5["handoff"]["schema"] == "powercontext.handoff.v1"

    # 1. 定位本次任务适用的规格条目
    assert "SPEC-GW-02" in ctx_text
    # 2. 定位需要修改的代码文件与符号
    assert "target_project/gateway.py" in ctx_text
    assert "TokenBucketGateway.record_upstream_outcome" in ctx_text
    # 3. 定位用于验证功能及兼容性的测试
    assert "target_project/test_gateway.py::test_circuit_breaker_state_transitions" in ctx_text
    assert "target_project/test_gateway.py::test_regression_basic_token_consumption" in ctx_text
    # 4. 定位已完成事项、待办事项和已有验证结果（复用状态）
    assert "completed_items: [SPEC-GW-01 滑动窗口双桶限流]" in ctx_text
    assert "todo_items: [SPEC-GW-02 三态熔断器与半开探测, SPEC-GW-03 网关响应头验证]" in ctx_text
    assert "before snapshot: 1 passed, 3 failed" in ctx_text

    # 动态更新规格并重新导入，验证输出立即反映更新内容（非固定死答案）
    updated_title = "上游模型服务三态熔断器 (动态更新冷却阈值30s)"
    eng.upsert_sdd_record(
        record_id="SPEC-GW-02",
        dimension="spec_code_map",
        spec_id="SPEC-GW-02",
        module_name="gateway.circuit_breaker",
        title=updated_title,
        content="当连续上游失败次数达到 failure_threshold 时进入 OPEN 熔断状态；冷却时间 recovery_timeout_sec=30.0 到期后转入 HALF_OPEN 探测态。",
        metadata={
            "file_path": "target_project/gateway.py",
            "symbol_name": "TokenBucketGateway.record_upstream_outcome",
            "start_line": 98,
            "end_line": 148,
            "status": "IMPLEMENTED",
        },
    )
    q1_after = eng.query_1_spec_location_and_status("SPEC-GW-02")
    assert any(m["title"] == updated_title for m in q1_after["code_mappings"])

    # 同时验证动态更新后的规格标题立即反映到 Query 5 PreparedContext 中
    q5_after = client.post(
        "/api/sdd/query",
        json={"query_type": 5, "target": "SPEC-GW-02", "task_id": "TASK-GW-102", "max_bytes": max_bytes},
    ).json()
    assert updated_title in q5_after["prepared_context"]


def test_task_state_persistence_across_engine_restart():
    """必做项 5：状态能够复用（保存任务进展后，新建独立引擎实例模拟重启后能够读取并使用）"""
    writer_engine = MiniMaxSDDContextEngine()
    writer_engine.init_and_seed_seekdb()
    save_res = writer_engine.save_task_state(
        task_id="TASK-GW-102",
        spec_id="SPEC-GW-02",
        completed_items=["SPEC-GW-01 滑动窗口限流", "SPEC-GW-02 三态熔断器"],
        todo_items=["SPEC-GW-03 响应头回归验证"],
        verification_summary="pytest target_project/test_gateway.py 4/4 passed",
    )
    assert save_res["status"] == "saved"
    assert "artifact_address" in save_res

    # 模拟进程重启：实例化全新的 MiniMaxSDDContextEngine 读取状态
    restarted_engine = MiniMaxSDDContextEngine()
    loaded = restarted_engine.load_task_state("TASK-GW-102")
    assert loaded is not None
    assert loaded["spec_id"] == "SPEC-GW-02"
    assert "SPEC-GW-02 三态熔断器" in loaded["state"]["completed_items"]
    assert "SPEC-GW-03 响应头回归验证" in loaded["state"]["todo_items"]
    assert "powercontext_artifact" in loaded["state"]


def test_minimax_agent_apply_patch_and_verify():
    """必做项 2：MiniMax 实际参与代码修改并与提交差异对应"""
    apply_res = client.post(
        "/api/sdd/apply",
        json={"task_id": "TASK-GW-102", "spec_id": "SPEC-GW-02", "auto_test": True},
    ).json()
    assert apply_res["status"] == "applied"
    assert apply_res["model"] == "MiniMax-M2.7"
    assert apply_res["tests_passed"] is True
    assert "diff_preview" in apply_res
    assert "target_file" in apply_res


def test_before_vs_after_code_modification_effectiveness():
    """
    必做项 2 & 6：代码修改有效 + 开发记录与差异补丁齐全
    验证同一套 test_gateway.py：
    1) 在 snapshots/before 上：基线回归测试 test_regression_basic_token_consumption 通过，但目标功能测试因未实现而失败（退出码 != 0）；
    2) 在 snapshots/after (target_project) 上：全部 4 项测试 100% 通过（退出码 == 0）。
    """
    base = Path(__file__).parent
    test_file = str(base / "target_project" / "test_gateway.py")

    # 1. 验证 snapshots/before 下回归测试单独通过（证明无语法/缺包等环境错误）
    env_before = os.environ.copy()
    env_before["PYTHONPATH"] = str(base / "snapshots" / "before")
    reg_proc = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-k", "test_regression_basic_token_consumption"],
        env=env_before,
        capture_output=True,
        text=True,
    )
    assert reg_proc.returncode == 0, f"Regression test should pass on before snapshot: {reg_proc.stdout}"

    # 2. 验证 snapshots/before 下完整测试集因目标新特性未实现而失败
    full_before = subprocess.run(
        [sys.executable, "-m", "pytest", test_file],
        env=env_before,
        capture_output=True,
        text=True,
    )
    assert full_before.returncode != 0, "Target feature tests must fail on before snapshot"

    # 3. 验证修改后 (target_project / snapshots/after) 全部 4 项测试 100% 通过
    v_resp = client.post("/api/sdd/verify-target")
    assert v_resp.status_code == 200
    assert v_resp.json()["target_project_tests_passed"] is True

    # 4. 验证所有必交文档材料齐全
    for doc_name in ["README.md", "REPORT.md", "VALIDATION_SAMPLE.md", "TECH_USAGE_LOG.md"]:
        assert (base / doc_name).exists(), f"Missing required document: {doc_name}"
