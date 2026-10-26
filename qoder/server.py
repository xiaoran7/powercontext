import os
from typing import Optional
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from scholar_engine import ScholarPulseEngine

app = FastAPI(
    title="ScholarPulse：Qoder × OceanBase SeekDB × PowerContext 论文精读与间隔复习助手",
    description="2026 OceanBase AI 数据库大赛 - Qoder 赛题交付系统 (OB-P:xI1C-CXPJXT4ztVpZDY6_A)",
    version="1.0.0",
)

engine = ScholarPulseEngine()


class IngestNoteRequest(BaseModel):
    doc_id: str = Field(..., description="文献/笔记唯一标识")
    paper_title: str = Field(..., description="论文或技术报告标题")
    section: str = Field(..., description="章节名称")
    topic: str = Field("context_engineering", description="研究主题分类")
    content: str = Field(..., description="原文核心段落")
    key_takeaway: str = Field(..., description="一句话核心结论")
    flashcard_q: str = Field(..., description="自适应复习闪卡问题")
    flashcard_a: str = Field(..., description="自适应复习闪卡标准答案")


class ResearchBriefRequest(BaseModel):
    research_question: str = Field(..., description="研究问题或复习主题")
    max_bytes: int = Field(1600, ge=400, le=8000, description="PowerContext PreparedContext UTF-8 字节预算")


@app.get("/api/scholar/status")
def get_status():
    return engine.init_and_seed_seekdb()


@app.post("/api/scholar/ingest")
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


@app.post("/api/scholar/brief")
def generate_research_brief(req: ResearchBriefRequest):
    engine.init_and_seed_seekdb()
    return engine.build_research_brief_and_flashcards(
        research_question=req.research_question,
        max_bytes=req.max_bytes,
    )


site_dir = os.path.join(os.path.dirname(__file__), "site")
os.makedirs(site_dir, exist_ok=True)
app.mount("/site", StaticFiles(directory=site_dir), name="site")


@app.get("/")
def index():
    return FileResponse(os.path.join(site_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8093)
