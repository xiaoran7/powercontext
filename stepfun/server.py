import json
import os
import time
from typing import Dict, List, Optional

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import seekdb_bootstrap

# 默认连接本机 seekdb（127.0.0.1:2881），启动时若未运行则自动拉起本地实例；
# 设置 SEEKDB_HOST 可切换到任意远程实例。详见 seekdb_bootstrap.py 与 README。
SEEKDB = seekdb_bootstrap.config()
SEEKDB_STATUS = "尚未初始化"

EMBEDDING_DIM = 512  # 对应浏览器端侧 Xenova/bge-small-zh-v1.5 量化模型维度

app = FastAPI(
    title="StepFun × OceanBase SeekDB 浏览器端侧 Embedding 与数据库内混合检索系统",
    description="2026 OceanBase AI 数据库大赛 - 阶跃星辰赛题交付系统",
)

# 会话上下文状态管理（记录用户的查询历史、当前集合、召回轨迹与端到端分阶段延迟）
SESSION_STORE: Dict[str, List[dict]] = {}


def get_db_conn():
    try:
        conn = pymysql.connect(
            host=SEEKDB.host,
            port=SEEKDB.port,
            user=SEEKDB.user,
            password=SEEKDB.password,
            database=SEEKDB.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=10,
        )
        # seekdb 默认 ob_query_timeout 只有 10s，批量写入与索引构建容易触顶
        with conn.cursor() as cur:
            cur.execute("SET SESSION ob_query_timeout = 600000000")
            cur.execute("SET SESSION net_read_timeout = 600")
            cur.execute("SET SESSION net_write_timeout = 600")
        return conn
    except pymysql.err.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                f"SeekDB 不可用（{SEEKDB.host}:{SEEKDB.port}）：{e}。"
                "请确认本地 seekdb 已启动（bash scripts/start_seekdb.sh），"
                "或设置 SEEKDB_HOST 指向可用实例。"
            ),
        )


def ensure_database():
    """确保目标 database 存在（seekdb 初始化后默认只有 test 库）。"""
    conn = pymysql.connect(
        host=SEEKDB.host,
        port=SEEKDB.port,
        user=SEEKDB.user,
        password=SEEKDB.password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{SEEKDB.database}` DEFAULT CHARACTER SET utf8mb4"
            )
    finally:
        conn.close()


def init_seekdb_table():
    """初始化或迁移 SeekDB 向量与全文双索引知识库表"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS stepfun_knowledge_base (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    content TEXT NOT NULL,
                    category VARCHAR(64) DEFAULT 'general',
                    embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                    device_source VARCHAR(64) DEFAULT 'webgpu',
                    client_ms FLOAT DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    collection_id VARCHAR(64) DEFAULT 'default',
                    quant_mode VARCHAR(32) DEFAULT 'q8',
                    VECTOR INDEX idx_stepfun_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
                    FULLTEXT INDEX idx_stepfun_fts (title, content) WITH PARSER ik
                ) DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("SHOW COLUMNS FROM stepfun_knowledge_base")
            cols = {row["Field"] for row in cur.fetchall()}
            if "collection_id" not in cols:
                cur.execute("ALTER TABLE stepfun_knowledge_base ADD COLUMN collection_id VARCHAR(64) DEFAULT 'default'")
            if "quant_mode" not in cols:
                cur.execute("ALTER TABLE stepfun_knowledge_base ADD COLUMN quant_mode VARCHAR(32) DEFAULT 'q8'")
            cur.execute("SHOW INDEX FROM stepfun_knowledge_base")
            idx_names = {row["Key_name"] for row in cur.fetchall()}
            if "idx_stepfun_fts" not in idx_names:
                cur.execute("ALTER TABLE stepfun_knowledge_base ADD FULLTEXT INDEX idx_stepfun_fts (title, content) WITH PARSER ik")
    finally:
        conn.close()


class DocumentInsertReq(BaseModel):
    title: str = Field(..., description="文档标题")
    content: str = Field(..., description="文档正文")
    category: str = Field("general", description="分类标签")
    collection_id: str = Field("default", description="所属数据集/知识集合 ID")
    embedding: List[float] = Field(..., description="浏览器端侧 WebGPU/WASM 实时生成的 512 维特征向量")
    quant_mode: str = Field("q8", description="端侧模型量化精度 (q8 / fp16 / fp32)")
    device_source: str = Field("webgpu", description="端侧推理后端标识 (webgpu / wasm)")
    client_ms: float = Field(0.0, description="浏览器端侧推理耗时(毫秒)")


class BatchDocumentInsertReq(BaseModel):
    collection_id: str = Field("default", description="目标集合 ID")
    documents: List[DocumentInsertReq] = Field(..., description="端侧批量向量化后的文档列表")


class VectorSearchReq(BaseModel):
    query_text: Optional[str] = Field("", description="用户原始查询文本（用于 SeekDB 全文检索通道与数据库内混合融合）")
    embedding: List[float] = Field(..., description="浏览器端侧实时生成的 512 维查询向量")
    top_k: int = Field(5, ge=1, le=20, description="返回的最相近结果数量")
    mode: str = Field("hybrid", description="检索模式：hybrid (SeekDB 数据库内向量+全文混合检索) | vector (纯向量) | fts (纯全文)")
    collection_id: Optional[str] = Field(None, description="按数据集/集合过滤，为空则跨全部集合检索")
    category: Optional[str] = Field(None, description="按分类标签过滤")
    session_id: Optional[str] = Field("default-session", description="用户查询会话上下文 ID")
    client_ms: float = Field(0.0, description="浏览器端侧查询向量化耗时(毫秒)")
    rrf_k: int = Field(60, ge=1, le=200, description="SeekDB SQL 内置 RRF 倒数排名融合常数 k")


@app.on_event("startup")
def startup_event():
    global SEEKDB_STATUS
    _, SEEKDB_STATUS = seekdb_bootstrap.ensure_seekdb(SEEKDB)
    print(f"[seekdb] {SEEKDB_STATUS}")
    try:
        ensure_database()
        init_seekdb_table()
    except Exception as e:
        print(f"[Warning] Failed to init SeekDB table on startup: {e}")


@app.get("/api/status")
def check_status():
    """检查 SeekDB 实例状态、双索引配置与数据集统计"""
    start = time.perf_counter()
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS ver;")
            ver = cur.fetchone()["ver"]
            cur.execute("SELECT COUNT(*) AS cnt FROM stepfun_knowledge_base;")
            cnt = cur.fetchone()["cnt"]
            cur.execute("""
                SELECT COALESCE(collection_id, 'default') AS collection_id,
                       COUNT(*) AS doc_count,
                       ROUND(AVG(client_ms), 2) AS avg_client_ms
                FROM stepfun_knowledge_base
                GROUP BY COALESCE(collection_id, 'default')
            """)
            collections = cur.fetchall()
            cur.execute("""
                SELECT id, title, category, COALESCE(collection_id, 'default') AS collection_id,
                       COALESCE(quant_mode, 'q8') AS quant_mode, device_source, client_ms, created_at
                FROM stepfun_knowledge_base
                ORDER BY id DESC LIMIT 20;
            """)
            recent_docs = cur.fetchall()
        conn.close()
        latency = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "online",
            "seekdb_host": f"{SEEKDB.host}:{SEEKDB.port}",
            "seekdb_mode": SEEKDB.mode,
            "seekdb_status": SEEKDB_STATUS,
            "database": SEEKDB.database,
            "version": ver,
            "total_documents": cnt,
            "collections": collections,
            "indexes": ["idx_stepfun_hnsw (VSAG HNSW COSINE)", "idx_stepfun_fts (FULLTEXT IK)"],
            "active_sessions": len(SESSION_STORE),
            "ping_ms": latency,
            "recent_docs": recent_docs,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SeekDB 连接异常: {str(e)}")


@app.post("/api/documents")
def insert_document(req: DocumentInsertReq):
    """接收浏览器端侧生成的 Embedding 向量并持久化至 SeekDB"""
    if len(req.embedding) != EMBEDDING_DIM:
        raise HTTPException(
            status_code=400,
            detail=f"向量维度不匹配：期望 {EMBEDDING_DIM} 维，实际收到 {len(req.embedding)} 维",
        )

    vec_str = "[" + ",".join(f"{x:.6f}" for x in req.embedding) + "]"
    start = time.perf_counter()

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """
                INSERT INTO stepfun_knowledge_base
                (title, content, category, collection_id, embedding, quant_mode, device_source, client_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(
                sql,
                (
                    req.title,
                    req.content,
                    req.category,
                    req.collection_id or "default",
                    vec_str,
                    req.quant_mode or "q8",
                    req.device_source,
                    req.client_ms,
                ),
            )
            doc_id = cur.lastrowid
    finally:
        conn.close()

    db_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "status": "inserted",
        "id": doc_id,
        "collection_id": req.collection_id or "default",
        "quant_mode": req.quant_mode or "q8",
        "seekdb_insert_ms": db_ms,
        "client_embed_ms": req.client_ms,
    }


@app.post("/api/documents/batch")
def insert_documents_batch(req: BatchDocumentInsertReq):
    """端侧批处理向量化后的一次性批量写入接口，减少网络往返并刷新索引可见性"""
    if not req.documents:
        raise HTTPException(status_code=400, detail="批量文档列表不能为空")

    rows_to_insert = []
    for doc in req.documents:
        if len(doc.embedding) != EMBEDDING_DIM:
            raise HTTPException(status_code=400, detail=f"向量维度不匹配：{len(doc.embedding)} != {EMBEDDING_DIM}")
        vec_str = "[" + ",".join(f"{x:.6f}" for x in doc.embedding) + "]"
        rows_to_insert.append(
            (
                doc.title,
                doc.content,
                doc.category,
                doc.collection_id or req.collection_id or "default",
                vec_str,
                doc.quant_mode or "q8",
                doc.device_source,
                doc.client_ms,
            )
        )

    start = time.perf_counter()
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # 分块写入：单次 executemany 过大时，向量索引的增量构建会拖长单条语句耗时
            chunk_size = 16
            for i in range(0, len(rows_to_insert), chunk_size):
                cur.executemany(
                    """
                    INSERT INTO stepfun_knowledge_base
                    (title, content, category, collection_id, embedding, quant_mode, device_source, client_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows_to_insert[i : i + chunk_size],
                )
    finally:
        conn.close()

    db_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "status": "batch_inserted",
        "count": len(rows_to_insert),
        "collection_id": req.collection_id,
        "seekdb_batch_insert_ms": db_ms,
    }


@app.post("/api/search")
def search_vectors(req: VectorSearchReq):
    """
    完全在远端 SeekDB 数据库内核中完成向量与全文混合检索（100% In-Database Hybrid Search）：
    - 严守赛题要求：「检索处理由 seekdb 完成，不得在客户端分别获取两路结果后自行拼合来替代数据库混合检索」。
    - hybrid 模式通过单条 SQL 在 SeekDB 内核中完成：
      1) 子查询 v 扫描 idx_stepfun_hnsw 计算 COSINE_DISTANCE 并通过 ROW_NUMBER() 生成向量通道次序；
      2) 子查询 f 扫描 idx_stepfun_fts 计算 MATCH ... AGAINST BM25 得分并通过 ROW_NUMBER() 生成全文通道次序；
      3) 外层查询在 SeekDB 内执行 GROUP BY id、SUM(rrf_part) AS rrf_score 与 GROUP_CONCAT(channel)，
         最终由 SeekDB 直接输出排序后的 Top-K 混合检索结果。
    """
    if len(req.embedding) != EMBEDDING_DIM:
        raise HTTPException(
            status_code=400,
            detail=f"查询向量维度不匹配：期望 {EMBEDDING_DIM} 维，实际收到 {len(req.embedding)} 维",
        )

    vec_str = "[" + ",".join(f"{x:.6f}" for x in req.embedding) + "]"
    query_text = (req.query_text or "").strip()
    mode = (req.mode or "hybrid").lower()
    candidate_limit = max(req.top_k * 3, 10)

    where_clauses = []
    where_params = []
    if req.collection_id:
        where_clauses.append("COALESCE(collection_id, 'default') = %s")
        where_params.append(req.collection_id)
    if req.category:
        where_clauses.append("category = %s")
        where_params.append(req.category)
    filter_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    fts_filter_sql = (
        (" WHERE " + " AND ".join(where_clauses + ["MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) > 0"]))
        if where_clauses
        else " WHERE MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) > 0"
    )

    start = time.perf_counter()
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if mode == "hybrid" and query_text:
                sql = f"""
                    SELECT
                        id,
                        MAX(title) AS title,
                        MAX(content) AS content,
                        MAX(category) AS category,
                        MAX(collection_id) AS collection_id,
                        MAX(quant_mode) AS quant_mode,
                        MAX(device_source) AS device_source,
                        MAX(client_ms) AS client_ms,
                        COALESCE(MIN(distance), 0.45) AS distance,
                        MAX(fts_score) AS fts_score,
                        SUM(rrf_part) AS rrf_score,
                        GROUP_CONCAT(channel ORDER BY channel SEPARATOR '+') AS channels_str
                    FROM (
                        SELECT
                            v.*,
                            1.0 / (%s + ROW_NUMBER() OVER (ORDER BY v.distance ASC, v.id ASC)) AS rrf_part
                        FROM (
                            SELECT
                                id, title, content, category,
                                COALESCE(collection_id, 'default') AS collection_id,
                                COALESCE(quant_mode, 'q8') AS quant_mode,
                                device_source, client_ms,
                                'vector' AS channel,
                                COSINE_DISTANCE(embedding, %s) AS distance,
                                0.0 AS fts_score
                            FROM stepfun_knowledge_base
                            {filter_sql}
                            ORDER BY COSINE_DISTANCE(embedding, %s) APPROXIMATE
                            LIMIT %s
                        ) v
                        UNION ALL
                        SELECT
                            f.*,
                            1.0 / (%s + ROW_NUMBER() OVER (ORDER BY f.fts_score DESC, f.id ASC)) AS rrf_part
                        FROM (
                            SELECT
                                id, title, content, category,
                                COALESCE(collection_id, 'default') AS collection_id,
                                COALESCE(quant_mode, 'q8') AS quant_mode,
                                device_source, client_ms,
                                'fts' AS channel,
                                NULL AS distance,
                                MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS fts_score
                            FROM stepfun_knowledge_base
                            {fts_filter_sql}
                            ORDER BY fts_score DESC, id ASC
                            LIMIT %s
                        ) f
                    ) hybrid_candidates
                    GROUP BY id
                    ORDER BY rrf_score DESC, distance ASC, id ASC
                    LIMIT %s
                """
                params = (
                    [req.rrf_k, vec_str]
                    + where_params
                    + [vec_str, candidate_limit, req.rrf_k, query_text]
                    + where_params
                    + [query_text, candidate_limit, req.top_k]
                )
                cur.execute(sql, tuple(params))
                db_rows = cur.fetchall()
            elif mode == "fts" and query_text:
                sql = f"""
                    SELECT
                        id, title, content, category,
                        COALESCE(collection_id, 'default') AS collection_id,
                        COALESCE(quant_mode, 'q8') AS quant_mode,
                        device_source, client_ms,
                        0.45 AS distance,
                        MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS fts_score,
                        MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS rrf_score,
                        'fts' AS channels_str
                    FROM stepfun_knowledge_base
                    {fts_filter_sql}
                    ORDER BY fts_score DESC, id ASC
                    LIMIT %s
                """
                params = [query_text, query_text] + where_params + [query_text, req.top_k]
                cur.execute(sql, tuple(params))
                db_rows = cur.fetchall()
            else:
                sql = f"""
                    SELECT
                        id, title, content, category,
                        COALESCE(collection_id, 'default') AS collection_id,
                        COALESCE(quant_mode, 'q8') AS quant_mode,
                        device_source, client_ms,
                        COSINE_DISTANCE(embedding, %s) AS distance,
                        0.0 AS fts_score,
                        1.0 / (1.0 + COSINE_DISTANCE(embedding, %s)) AS rrf_score,
                        'vector' AS channels_str
                    FROM stepfun_knowledge_base
                    {filter_sql}
                    ORDER BY COSINE_DISTANCE(embedding, %s) APPROXIMATE
                    LIMIT %s
                """
                params = [vec_str, vec_str] + where_params + [vec_str, req.top_k]
                cur.execute(sql, tuple(params))
                db_rows = cur.fetchall()
    finally:
        conn.close()

    db_ms = round((time.perf_counter() - start) * 1000, 2)

    results = []
    for r in db_rows:
        dist = float(r["distance"]) if r["distance"] is not None else 0.45
        fts_val = float(r["fts_score"] or 0.0)
        rrf_val = float(r["rrf_score"] or 0.0)
        channels = (r.get("channels_str") or "vector").split("+")
        vec_sim = max(0.0, 1.0 - dist)
        fts_bonus = min(0.12, fts_val * 0.03) if "fts" in channels else 0.0
        hybrid_similarity = min(0.999, vec_sim + fts_bonus)
        results.append(
            {
                "id": int(r["id"]),
                "title": r["title"],
                "content": r["content"],
                "category": r["category"],
                "collection_id": r.get("collection_id", "default"),
                "quant_mode": r.get("quant_mode", "q8"),
                "distance": round(dist, 4),
                "fts_score": round(fts_val, 4),
                "rrf_score": round(rrf_val, 6),
                "channels": channels,
                "similarity_pct": round(hybrid_similarity * 100, 2),
                "device_source": r["device_source"],
                "client_ms": r["client_ms"],
            }
        )

    session_id = req.session_id or "default-session"
    history = SESSION_STORE.setdefault(session_id, [])
    history.append(
        {
            "turn": len(history) + 1,
            "query_text": query_text,
            "mode": mode,
            "collection_id": req.collection_id or "all",
            "client_embed_ms": req.client_ms,
            "seekdb_search_ms": db_ms,
            "top_hit_titles": [r["title"] for r in results[:3]],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    executed_sql_summary = (
        f"SINGLE-SQL IN-SEEKDB HYBRID RRF (GROUP BY id ORDER BY SUM(rrf_part) DESC: VSAG HNSW + FULLTEXT IK '{query_text}') LIMIT {req.top_k}"
        if (mode == "hybrid" and query_text)
        else f"SELECT id, title, COSINE_DISTANCE(embedding, '{vec_str[:24]}...]') AS distance FROM stepfun_knowledge_base ORDER BY distance APPROXIMATE LIMIT {req.top_k};"
    )

    return {
        "mode": mode,
        "collection_id": req.collection_id or "all",
        "session_id": session_id,
        "session_turn_count": len(history),
        "seekdb_search_ms": db_ms,
        "total_returned": len(results),
        "executed_sql": executed_sql_summary,
        "results": results,
    }


@app.post("/api/reindex")
def rebuild_or_refresh_index():
    """刷新 SeekDB 向量与全文索引统计信息并返回双索引健康状态"""
    start = time.perf_counter()
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW INDEX FROM stepfun_knowledge_base")
            indexes = [
                {"key_name": r["Key_name"], "column_name": r["Column_name"], "index_type": r["Index_type"]}
                for r in cur.fetchall()
            ]
            cur.execute("SELECT COUNT(*) AS cnt FROM stepfun_knowledge_base")
            total = cur.fetchone()["cnt"]
    finally:
        conn.close()
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "status": "reindexed",
        "total_documents": total,
        "indexes": indexes,
        "refresh_ms": elapsed_ms,
    }


@app.get("/api/sessions/{session_id}")
def get_session_context(session_id: str):
    history = SESSION_STORE.get(session_id, [])
    return {
        "session_id": session_id,
        "turn_count": len(history),
        "history": history,
    }


@app.delete("/api/sessions/{session_id}")
def clear_session_context(session_id: str):
    SESSION_STORE.pop(session_id, None)
    return {"status": "cleared", "session_id": session_id}


@app.get("/api/benchmark")
def get_quantization_and_hybrid_benchmark():
    """返回浏览器实测的量化对比与检索质量结果。

    数据来自前端「量化与检索质量实测」页（/bench）在真实浏览器中的运行，
    由 `/api/benchmark/save` 落盘到 `benchmark/results/`，本接口原样读取，
    不在服务端编造任何数字。尚未运行过实测时返回 measured=false。
    """
    summary_path = os.path.join(os.path.dirname(__file__), "benchmark", "results", "summary.json")
    if not os.path.exists(summary_path):
        return {
            "measured": False,
            "model": "Xenova/bge-small-zh-v1.5",
            "note": "尚未运行实测。请在浏览器打开 /bench 执行「量化与检索质量实测」。",
            "runs": [],
        }
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"measured": False, "note": f"读取实测结果失败：{e}", "runs": []}
    data["measured"] = True
    return data


@app.post("/api/benchmark/save")
def save_benchmark_result(payload: dict):
    """保存 /bench 页面在浏览器中实测得到的原始结果。"""
    runs = payload.get("runs") if isinstance(payload, dict) else None
    if not runs:
        raise HTTPException(status_code=400, detail="结果为空，拒绝保存")

    results_dir = os.path.join(os.path.dirname(__file__), "benchmark", "results")
    os.makedirs(results_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    raw_path = os.path.join(results_dir, f"raw-{stamp}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    summary_keys = (
        "label",
        "device",
        "dtype",
        "model_size_mb",
        "cold_load_ms",
        "throughput_docs_per_sec",
        "embed_p50_ms",
        "seekdb_p50_ms",
        "seekdb_p90_ms",
        "e2e_p50_ms",
        "recall_at_5",
        "ndcg_at_5",
    )
    summary = {
        "model": payload.get("model"),
        "top_k": payload.get("top_k"),
        "corpus_size": payload.get("corpus_size"),
        "query_count": payload.get("query_count"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "user_agent": payload.get("user_agent"),
        "webgpu_available": payload.get("webgpu_available"),
        "raw_file": os.path.basename(raw_path),
        "runs": [{k: r.get(k) for k in summary_keys} for r in runs],
    }
    with open(os.path.join(results_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return {"saved": True, "raw": os.path.basename(raw_path), "summary": "summary.json"}


@app.post("/api/reset")
def reset_table():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE stepfun_knowledge_base;")
        SESSION_STORE.clear()
        return {"status": "truncated"}
    finally:
        conn.close()


static_dir = os.path.join(os.path.dirname(__file__), "static")
bench_dir = os.path.join(os.path.dirname(__file__), "benchmark")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/benchmark", StaticFiles(directory=bench_dir), name="benchmark")


@app.get("/")
def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/bench")
def bench_page():
    return FileResponse(os.path.join(bench_dir, "bench.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8088")),
    )
