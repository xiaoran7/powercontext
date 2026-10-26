import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from scholar_engine import ScholarPulseEngine

app = FastAPI(
    title="ScholarPulse：Qoder × OceanBase SeekDB × PowerContext 论文精读与间隔复习助手",
    description="2026 OceanBase AI 数据库大赛 - Qoder 赛题交付系统 (OB-P:xI1C-CXPJXT4ztVpZDY6_A)",
    version="1.2.0",
)

engine = ScholarPulseEngine()

# 前端在本地 FastAPI 与 Qoder Sites Function 上使用同一套路径前缀，
# 保证 site/index.html 无需区分部署形态即可调用同一份 API 契约。
router = APIRouter()


class IngestNoteRequest(BaseModel):
    doc_id: str = Field(..., description="文献/笔记唯一标识")
    paper_title: str = Field(..., description="论文或技术报告标题")
    section: str = Field(..., description="章节名称")
    topic: str = Field("context_engineering", description="研究主题分类")
    content: str = Field(..., description="原文核心段落")
    key_takeaway: str = Field(..., description="一句话核心结论")
    flashcard_q: str = Field(..., description="自适应复习闪卡问题")
    flashcard_a: str = Field(..., description="自适应复习闪卡标准答案")


class LearnerMemoryRequest(BaseModel):
    learner_id: str = Field("default-scholar", description="学习者唯一标识")
    weak_topics: List[str] = Field(default_factory=list, description="薄弱知识点列表")
    mastered_doc_ids: List[str] = Field(default_factory=list, description="已掌握文献 ID 列表")
    preferred_depth: str = Field("deep-derivation", description="偏好讲解深度")
    review_note: str = Field("", description="复习进度备注")
    cards: Dict[str, Any] = Field(default_factory=dict, description="每张闪卡的间隔重复调度状态")


class LearnerProfile(BaseModel):
    weak_topics: List[str] = Field(default_factory=list, description="薄弱知识点列表")
    mastered_doc_ids: List[str] = Field(default_factory=list, description="已掌握文献 ID 列表")
    preferred_depth: str = Field("deep-derivation", description="偏好讲解深度")
    review_note: str = Field("", description="复习进度备注")
    cards: Dict[str, Any] = Field(default_factory=dict, description="每张闪卡的间隔重复调度状态")


class ResearchBriefRequest(BaseModel):
    research_question: str = Field(..., description="研究问题或复习主题")
    max_bytes: int = Field(1600, ge=400, le=8000, description="PowerContext PreparedContext UTF-8 字节预算")
    learner_id: str = Field("default-scholar", description="关联的学习者 ID（自动复用其薄弱点记忆）")
    memory: Optional[LearnerProfile] = Field(
        None, description="客户端持有的学习者画像，与服务端已存记忆合并后用于闪卡置顶"
    )
    scope_doc_ids: Optional[List[str]] = Field(
        None, description="限定检索范围到指定章节 ID（文献库中点击聚焦时传入）"
    )


class IngestBatchRequest(BaseModel):
    documents: List[Dict[str, Any]] = Field(default_factory=list, description="批量导入的论文章节")


class ReviewRequest(BaseModel):
    learner_id: str = Field(..., description="学习者 ID")
    doc_id: str = Field(..., description="被复习的文献章节 ID")
    grade: int = Field(..., description="复习评分：0=忘记 3=困难 4=良好 5=掌握")


@router.get("/scholar/status")
def get_status():
    return engine.get_status()


@router.post("/scholar/init")
def init_scholar_store():
    return engine.init_and_seed_seekdb()


@router.post("/scholar/reset")
def reset_scholar_store():
    return engine.init_and_seed_seekdb(force_reset=True)


@router.post("/scholar/memory")
def save_learner_memory(req: LearnerMemoryRequest):
    engine.init_and_seed_seekdb()
    return engine.save_learner_memory(
        learner_id=req.learner_id,
        weak_topics=req.weak_topics,
        mastered_doc_ids=req.mastered_doc_ids,
        preferred_depth=req.preferred_depth,
        review_note=req.review_note,
        cards=req.cards,
    )


@router.get("/scholar/corpus")
def get_corpus():
    engine.ensure_schema()
    return engine.list_documents()


@router.get("/scholar/presets")
def get_presets():
    return engine.get_presets()


@router.post("/scholar/review")
def record_review(req: ReviewRequest):
    engine.ensure_schema()
    try:
        return engine.record_review(learner_id=req.learner_id, doc_id=req.doc_id, grade=req.grade)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_review_grade") from exc


@router.get("/scholar/memory/{learner_id}")
def get_learner_memory(learner_id: str):
    engine.ensure_schema()
    try:
        mem = ScholarPulseEngine().get_learner_memory(learner_id)
    except Exception as exc:  # noqa: BLE001 - 外部依赖故障统一映射为可重试的 503
        raise HTTPException(status_code=503, detail="learner_memory_unavailable") from exc
    if mem is None:
        raise HTTPException(status_code=404, detail=f"Learner memory {learner_id} not found")
    return mem


@router.post("/scholar/ingest")
def ingest_note(req: IngestNoteRequest):
    engine.init_and_seed_seekdb()
    return engine.ingest_research_note(
        doc_id=req.doc_id,
        paper_title=req.paper_title,
        section=req.section,
        topic=req.topic,
        content=req.content,
        key_takeaway=req.key_takeaway,
        flashcard_q=req.flashcard_q,
        flashcard_a=req.flashcard_a,
    )


@router.post("/scholar/ingest/batch")
def ingest_batch(req: IngestBatchRequest):
    engine.init_and_seed_seekdb()
    if not req.documents or len(req.documents) > 200:
        raise HTTPException(status_code=400, detail="invalid_document_batch")
    return engine.ingest_documents(req.documents)


@router.post("/scholar/brief")
def generate_research_brief(req: ResearchBriefRequest):
    engine.init_and_seed_seekdb()
    memory: Optional[Dict[str, Any]] = req.memory.model_dump() if req.memory else None
    return engine.build_research_brief_and_flashcards(
        research_question=req.research_question,
        max_bytes=req.max_bytes,
        learner_id=req.learner_id,
        memory=memory,
        scope_doc_ids=req.scope_doc_ids,
    )


app.include_router(router, prefix="/api")
app.include_router(router, prefix="/functions/v1/app/api")

site_dir = os.path.join(os.path.dirname(__file__), "site")
os.makedirs(site_dir, exist_ok=True)
app.mount("/site", StaticFiles(directory=site_dir), name="site")


@app.get("/")
def index():
    return FileResponse(os.path.join(site_dir, "index.html"))


@app.get("/engine.js")
def engine_js():
    return FileResponse(os.path.join(site_dir, "engine.js"), media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8093")),
    )
