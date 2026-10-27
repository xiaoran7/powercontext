import sys
from pathlib import Path

# 仓库内 dify/minimax/qoder/stepfun 各有一个 server.py；从仓库根统一跑 pytest 时，
# 裸 `from server import ...` 可能命中别的目录。先把本目录放到 sys.path 首位，
# 并清掉同名模块缓存，确保导入的是本目录文件。
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
for _stale in ("server", "seekdb_bootstrap"):
    sys.modules.pop(_stale, None)

import math

from fastapi.testclient import TestClient

from seekdb_bootstrap import config, port_open
from server import EMBEDDING_DIM, app, startup_event


client = TestClient(app)


def _ensure_seekdb():
    """按应用自身逻辑准备 seekdb（本地未运行时自动拉起），与真实启动路径一致。"""
    startup_event()
    cfg = config()
    assert port_open(cfg.host, cfg.port), f"seekdb 未就绪：{cfg.host}:{cfg.port}"
    return cfg


def _make_unit_vector(seed_index: int, secondary_index: int = -1, secondary_weight: float = 0.25) -> list[float]:
    vec = [0.001] * EMBEDDING_DIM
    vec[seed_index % EMBEDDING_DIM] = 1.0
    if secondary_index >= 0:
        vec[secondary_index % EMBEDDING_DIM] = secondary_weight
    norm = math.sqrt(sum(x * x for x in vec))
    return [round(x / norm, 6) for x in vec]


def test_stepfun_status_and_dual_indexes():
    cfg = _ensure_seekdb()
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert data["seekdb_host"] == f"{cfg.host}:{cfg.port}"
    assert data["seekdb_mode"] in {"local", "remote"}
    assert data["database"] == cfg.database
    assert any("HNSW" in idx for idx in data["indexes"])
    assert any("FULLTEXT" in idx for idx in data["indexes"])


def test_stepfun_insert_and_in_seekdb_hybrid_search():
    _ensure_seekdb()
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

    batch_resp = client.post(
        "/api/documents/batch",
        json={"collection_id": collection, "documents": docs},
    )
    assert batch_resp.status_code == 200
    assert batch_resp.json()["count"] == 3

    # 1. 验证完全在 SeekDB 内核完成的混合检索（ROW_NUMBER() + SUM(rrf_part) + GROUP BY id ORDER BY rrf_score DESC）
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
    assert "IN-SEEKDB HYBRID RRF" in sdata["executed_sql"]
    assert sdata["total_returned"] >= 2
    assert sdata["results"][0]["rrf_score"] > 0

    # 2. 验证会话状态管理、索引刷新与 4 档量化基准测试结果
    sess_resp = client.get("/api/sessions/pytest-session-01")
    assert sess_resp.status_code == 200
    assert sess_resp.json()["turn_count"] >= 1

    reindex_resp = client.post("/api/reindex")
    assert reindex_resp.status_code == 200
    assert reindex_resp.json()["status"] == "reindexed"

    bench_resp = client.get("/api/benchmark")
    assert bench_resp.status_code == 200
    bench = bench_resp.json()
    assert "measured" in bench
    assert isinstance(bench.get("runs"), list)
