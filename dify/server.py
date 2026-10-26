import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hybrid_engine import HybridSearchEngine
from detective_context_engine import CyberDetectiveContextEngine, CASE_DOSSIER

app = FastAPI(
    title="Dify × OceanBase SeekDB × PowerContext：星海迷局 AI 赛博侦探与混合检索服务",
    description="2026 OceanBase AI 数据库大赛 - Dify 赛题交付系统 (OB-P:xI1C-CXPJXT4ztVpZDY6_A)",
    version="1.1.0",
)

engine: Optional[HybridSearchEngine] = None
detective_engine = CyberDetectiveContextEngine()


def get_engine() -> HybridSearchEngine:
    global engine
    if engine is None:
        engine = HybridSearchEngine()
        try:
            engine.init_database_tables()
        except Exception as e:
            print(f"Warning initializing tables: {e}")
    return engine


# --- 1. Dify External Knowledge API Models ---
class RetrievalSetting(BaseModel):
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)


class DifyRetrievalRequest(BaseModel):
    knowledge_id: str
    query: str
    retrieval_setting: RetrievalSetting


# --- 2. Three-Way Comparison Models ---
class CompareSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=15)
    rrf_k: int = Field(default=60, ge=1, le=200)
    filter_source: Optional[str] = None


# --- 3. Just-for-Fun Cyber Detective Game Models ---
class DetectiveTurnRequest(BaseModel):
    session_id: str = Field(default="detective-001", description="玩家侦探会话 ID")
    suspect_target: str = Field(default="lin_wei", description="审讯/调查对象 (lin_wei / su_qing / chen_mo)")
    player_action: str = Field(..., description="玩家输入的搜证或质询指令")
    max_bytes: int = Field(default=1600, ge=400, le=8000, description="PowerContext PreparedContext 字节预算")


class DetectiveHandoffRequest(BaseModel):
    session_id: str = Field(default="detective-001", description="玩家侦探会话 ID")
    accused_suspect: str = Field(default="lin_wei", description="最终指控的核心嫌犯")


@app.post("/retrieval")
async def dify_external_knowledge_retrieval(
    req: DifyRetrievalRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Dify 标准外部知识库接口 (External Knowledge API)
    将 Dify 知识检索节点无缝对接到 OceanBase SeekDB 的向量+全文 RRF 混合检索引擎。
    """
    eng = get_engine()
    if not req.query.strip():
        return {"records": []}

    top_k = req.retrieval_setting.top_k
    threshold = req.retrieval_setting.score_threshold
    res = eng.compare_all(query=req.query, top_k=top_k)
    hybrid_items = res["hybrid"]["results"]

    records = []
    max_rrf = 2.0 / 61.0
    for item in hybrid_items:
        normalized_score = min(round(item["rrf_score"] / max_rrf, 4), 1.0)
        if normalized_score < threshold:
            continue
        records.append(
            {
                "content": item["content"],
                "score": normalized_score,
                "title": item["title"],
                "metadata": {
                    "doc_id": item["id"],
                    "category": item["category"],
                    "source": item["source"],
                    "rrf_raw_score": item["rrf_score"],
                    "vec_rank": item["vec_rank"],
                    "bm25_rank": item["bm25_rank"],
                },
            }
        )
    return {"records": records}


@app.post("/api/compare")
async def compare_search_modes(req: CompareSearchRequest):
    """三路对比接口：SeekDB HNSW 向量检索 vs BM25 全文检索 vs RRF 混合检索"""
    eng = get_engine()
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    return eng.compare_all(
        query=req.query,
        top_k=req.top_k,
        rrf_k=req.rrf_k,
        filter_source=req.filter_source,
    )


@app.get("/api/game/dossier")
async def get_case_dossier():
    """获取《星海迷局》案件背景与嫌疑人初始档案"""
    return CASE_DOSSIER


@app.post("/api/game/turn")
async def play_detective_turn(req: DetectiveTurnRequest):
    """
    执行一轮《星海迷局：AI 赛博侦探剧场》交互：
    1. 调用 OceanBase SeekDB 执行向量 + 全文 RRF 混合检索召回案件技术卷宗；
    2. 调用 PowerContext 引擎将本轮取证固化为 CAPTURED Source；
    3. 提炼带字符偏移 (MemoryCitation) 的线索记忆并检测嫌疑人不在场证明矛盾；
    4. 在 max_bytes 字节预算内装配 BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1 上下文。
    """
    eng = get_engine()
    retrieval_bundle = eng.compare_all(query=req.player_action, top_k=3)
    hybrid_hits = retrieval_bundle["hybrid"]["results"]

    turn_result = detective_engine.process_turn(
        session_id=req.session_id,
        player_action=req.player_action,
        suspect_target=req.suspect_target,
        seekdb_hybrid_hits=hybrid_hits,
        max_bytes=req.max_bytes,
    )
    turn_result["seekdb_latency_ms"] = retrieval_bundle["hybrid"]["latency_ms"]
    return turn_result


@app.post("/api/game/handoff")
async def create_detective_handoff(req: DetectiveHandoffRequest):
    """生成带完整证据链溯源的 PowerContext Handoff 结案陈词制品"""
    return detective_engine.generate_case_handoff(
        session_id=req.session_id,
        accused_suspect=req.accused_suspect,
    )


@app.get("/api/game/state/{session_id}")
async def get_detective_session_state(session_id: str):
    """查询指定侦探会话的完整 PowerContext 状态与线索列表"""
    return detective_engine.get_or_create_session(session_id)


@app.post("/api/game/reset/{session_id}")
async def reset_detective_session_state(session_id: str):
    """恢复指定侦探会话至初始状态，便于评审重复执行多轮验证样例"""
    detective_engine.sessions.pop(session_id, None)
    return {
        "status": "reset",
        "session_id": session_id,
        "initial_state": detective_engine.get_or_create_session(session_id),
    }


@app.get("/api/corpus")
async def get_corpus():
    eng = get_engine()
    return {"total": len(eng.corpus), "documents": eng.corpus}


@app.get("/api/health")
async def health_check():
    eng = get_engine()
    try:
        conn = eng.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dify_knowledge_base")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {
            "status": "healthy",
            "seekdb_connected": True,
            "powercontext_enabled": True,
            "seekdb_host": f"{os.environ.get('SEEKDB_HOST', '100.72.34.104')}:{os.environ.get('SEEKDB_PORT', '2881')}",
            "documents_in_seekdb": count,
        }
    except Exception as e:
        return {"status": "degraded", "seekdb_connected": False, "error": str(e)}


static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8089)
