from pathlib import Path
import yaml
from fastapi.testclient import TestClient
from server import app


client = TestClient(app)


def test_dify_dsl_yaml_is_valid_and_complete():
    dsl_path = Path(__file__).parent / "dify_cyber_detective_app.yml"
    assert dsl_path.exists()
    dsl = yaml.safe_load(dsl_path.read_text(encoding="utf-8"))
    assert dsl["kind"] == "app"
    assert dsl["app"]["mode"] == "advanced-chat"
    node_types = [n["data"]["type"] for n in dsl["workflow"]["graph"]["nodes"]]
    assert "start" in node_types
    assert "knowledge-retrieval" in node_types
    assert "http-request" in node_types
    assert "llm" in node_types
    assert "answer" in node_types


def test_dify_external_knowledge_retrieval_and_three_way_compare():
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["seekdb_connected"] is True

    # 1. Dify External Knowledge API 标准契约验证
    ret_resp = client.post(
        "/retrieval",
        json={
            "knowledge_id": "seekdb-hybrid-kb",
            "query": "dbms_vector.batch_knn 批量向量检索与 HNSW 索引",
            "retrieval_setting": {"top_k": 4, "score_threshold": 0.01},
        },
        headers={"Authorization": "Bearer dify-seekdb-2026-secret"},
    )
    assert ret_resp.status_code == 200
    records = ret_resp.json()["records"]
    assert len(records) >= 1
    assert "content" in records[0]
    assert "score" in records[0]
    assert "rrf_raw_score" in records[0]["metadata"]

    # 2. 三路检索对比验证
    cmp_resp = client.post(
        "/api/compare",
        json={"query": "PowerContext 状态机 Scope 与 PreparedContext", "top_k": 3, "rrf_k": 60},
    )
    assert cmp_resp.status_code == 200
    cmp_data = cmp_resp.json()
    assert len(cmp_data["vector_only"]["results"]) >= 1
    assert len(cmp_data["bm25_only"]["results"]) >= 1
    assert len(cmp_data["hybrid"]["results"]) >= 1


def test_cyber_detective_multi_turn_powercontext_loop():
    session_id = "pytest-detective-session"
    max_bytes = 950

    # Turn 1: 质问林维（触发 HNSW / batch_knn 证词矛盾）
    t1 = client.post(
        "/api/game/turn",
        json={
            "session_id": session_id,
            "suspect_target": "lin_wei",
            "player_action": "调取 HNSW 快照与 dbms_vector.batch_knn 日志质问林维",
            "max_bytes": max_bytes,
        },
    )
    assert t1.status_code == 200
    d1 = t1.json()
    assert d1["contradiction_unlocked"] is True
    assert d1["captured_source"]["status"] == "CAPTURED"
    assert d1["prepared_context"].startswith("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert d1["prepared_context"].endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert d1["prepared_context_bytes"] <= max_bytes

    # Turn 2: 追问苏晴（触发 Scope / PreparedContext 证词矛盾，推进破案进度 >= 70%）
    t2 = client.post(
        "/api/game/turn",
        json={
            "session_id": session_id,
            "suspect_target": "su_qing",
            "player_action": "核查 Scope 权限与 PreparedContext 增量包追问苏晴",
            "max_bytes": max_bytes,
        },
    )
    assert t2.status_code == 200
    d2 = t2.json()
    assert d2["contradiction_unlocked"] is True
    assert d2["deduction_progress"] >= 70
    assert d2["ready_for_indictment"] is True
    assert d2["prepared_context_bytes"] <= max_bytes

    # 生成结案交接单 (PowerContext Handoff)
    ho = client.post(
        "/api/game/handoff",
        json={"session_id": session_id, "accused_suspect": "lin_wei"},
    )
    assert ho.status_code == 200
    hdata = ho.json()
    assert hdata["schema"] == "powercontext.handoff.v1"
    assert hdata["case_solved"] is True
    assert len(hdata["citations"]) == 2
