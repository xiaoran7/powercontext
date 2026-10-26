from pathlib import Path
from fastapi.testclient import TestClient
from server import app
from sdd_engine import MiniMaxSDDContextEngine


client = TestClient(app)


def test_six_mandatory_sdd_dimensions_in_seekdb():
    """验证全部 6 大必选 SDD 上下文维度均已在远端 SeekDB 中持久化并建立双索引"""
    resp = client.get("/api/sdd/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "initialized"
    assert data["all_six_dimensions_present"] is True
    for dim in MiniMaxSDDContextEngine.REQUIRED_DIMENSIONS:
        assert data["dimension_counts"].get(dim, 0) >= 1


def test_five_core_sdd_queries_and_powercontext_budget():
    """逐一验证赛题要求的 5 大核心查询与 PowerContext PreparedContext 字节预算控制"""
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
    assert len(q2["affected_contracts"]) >= 1
    assert len(q2["dependent_tasks"]) >= 1

    # Query 3: 历史设计决策理由与被否决方案
    q3 = client.post(
        "/api/sdd/query",
        json={"query_type": 3, "target": "为什么要先检查熔断器状态再扣减配额"},
    ).json()
    assert q3["query_type"] == "3_decision_rationale"
    assert len(q3["decisions"]) >= 1
    assert "rejected_alternatives" in q3["decisions"][0]["metadata"]

    # Query 4: 模块已知陷阱与强制防线规则
    q4 = client.post(
        "/api/sdd/query",
        json={"query_type": 4, "target": "gateway.circuit_breaker"},
    ).json()
    assert q4["query_type"] == "4_module_pitfalls_and_guardrails"
    assert len(q4["mandatory_guardrails"]) >= 1

    # Query 5: 任务级 PreparedContext 字节预算装配与 Handoff
    max_bytes = 1200
    q5 = client.post(
        "/api/sdd/query",
        json={
            "query_type": 5,
            "target": "SPEC-GW-02",
            "task_id": "TASK-GW-102",
            "max_bytes": max_bytes,
        },
    ).json()
    assert q5["query_type"] == "5_task_prepared_context_and_handoff"
    assert q5["prepared_context"].startswith("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert q5["prepared_context"].endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert q5["prepared_context_bytes"] <= max_bytes
    assert q5["handoff"]["schema"] == "powercontext.handoff.v1"
    assert len(q5["handoff"]["citations"]) >= 1


def test_target_project_verification_and_snapshots():
    """验证目标项目单元测试全部通过、结果同步回写 SeekDB，且演进前后快照与 Diff 完整存在"""
    base = Path(__file__).parent
    assert (base / "snapshots" / "before" / "gateway.py").exists()
    assert (base / "snapshots" / "after" / "gateway.py").exists()
    diff_file = base / "snapshots" / "sdd_evolution.diff"
    assert diff_file.exists()
    assert "@@" in diff_file.read_text(encoding="utf-8")

    v_resp = client.post("/api/sdd/verify-target")
    assert v_resp.status_code == 200
    v_data = v_resp.json()
    assert v_data["target_project_tests_passed"] is True
