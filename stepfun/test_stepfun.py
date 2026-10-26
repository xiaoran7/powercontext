import math
from fastapi.testclient import TestClient
from server import app, EMBEDDING_DIM


client = TestClient(app)


def _make_unit_vector(seed_index: int, secondary_index: int = -1, secondary_weight: float = 0.25) -> list[float]:
    vec = [0.001] * EMBEDDING_DIM
    vec[seed_index % EMBEDDING_DIM] = 1.0
    if secondary_index >= 0:
        vec[secondary_index % EMBEDDING_DIM] = secondary_weight
    norm = math.sqrt(sum(x * x for x in vec))
    return [round(x / norm, 6) for x in vec]


def test_stepfun_status_and_dual_indexes():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert "100.72.34.104:2881" in data["seekdb_host"]
    assert any("HNSW" in idx for idx in data["indexes"])
    assert any("FULLTEXT" in idx for idx in data["indexes"])


def test_stepfun_insert_and_single_sql_hybrid_search():
    collection = "pytest_stepfun_hybrid"
    docs = [
        {
            "title": "SeekDB VSAG HNSW 向量索引内核指南",
            "content": "SeekDB 原生集成 VSAG 向量引擎，支持 COSINE_DISTANCE 与近似最近邻 HNSW 索引加速。",
            "category": "kernel",
            "collection_id": collection,
            "embedding": _make_unit_vector(10, 11),
            "quant_mode": "q8",
            "device_source": "webgpu",
            "client_ms": 6.5,
        },
        {
            "title": "浏览器端侧 WebGPU ONNX 量化推理实践",
            "content": "利用 Transformers.js 在浏览器 WebGPU 与 WASM 环境运行 Xenova/bge-small-zh-v1.5 INT8 量化模型。",
            "category": "stepfun",
            "collection_id": collection,
            "embedding": _make_unit_vector(20, 21),
            "quant_mode": "q8",
            "device_source": "webgpu",
            "client_ms": 7.1,
        },
        {
            "title": "PowerContext 跨会话证据预算装配协议",
            "content": "通过 BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1 协议信封与 MemoryCitation 实现字节级证据回查。",
            "category": "context",
            "collection_id": collection,
            "embedding": _make_unit_vector(30, 31),
            "quant_mode": "fp16",
            "device_source": "wasm",
            "client_ms": 18.2,
        },
    ]

    inserted_ids = []
    for doc in docs:
        r = client.post("/api/documents", json=doc)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "inserted"
        assert body["collection_id"] == collection
        inserted_ids.append(body["id"])

    # 1. 测试混合检索（向量指向 doc[0]，但关键词包含 doc[1] 的 WebGPU ONNX，验证双通道同时召回并融合）
    search_resp = client.post(
        "/api/search",
        json={
            "query_text": "WebGPU ONNX 量化推理 VSAG",
            "embedding": _make_unit_vector(10, 20, 0.6),
            "top_k": 3,
            "mode": "hybrid",
            "collection_id": collection,
            "session_id": "pytest-session-01",
            "client_ms": 6.8,
        },
    )
    assert search_resp.status_code == 200
    sdata = search_resp.json()
    assert sdata["mode"] == "hybrid"
    assert "SINGLE-SQL HYBRID" in sdata["executed_sql"]
    assert sdata["total_returned"] >= 2
    assert sdata["results"][0]["rrf_score"] > 0

    # 2. 测试会话状态管理与索引刷新接口
    sess_resp = client.get("/api/sessions/pytest-session-01")
    assert sess_resp.status_code == 200
    assert sess_resp.json()["turn_count"] >= 1

    reindex_resp = client.post("/api/reindex")
    assert reindex_resp.status_code == 200
    assert reindex_resp.json()["status"] == "reindexed"

    bench_resp = client.get("/api/benchmark")
    assert bench_resp.status_code == 200
    assert len(bench_resp.json()["quantization_comparison"]) == 3
