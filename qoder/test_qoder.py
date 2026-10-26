from pathlib import Path
from fastapi.testclient import TestClient
from server import app


client = TestClient(app)


def test_qoder_scholar_status_and_ingest():
    status_resp = client.get("/api/scholar/status")
    assert status_resp.status_code == 200
    sdata = status_resp.json()
    assert sdata["status"] == "online"
    assert sdata["total_sections"] >= 4

    ingest_resp = client.post(
        "/api/scholar/ingest",
        json={
            "doc_id": "PAPER-QODER-05",
            "paper_title": "Qoder Agentic Coding & Spec-Driven Research Workflow",
            "section": "Section 2.1: Multi-Agent Context Handoff",
            "topic": "agent_workflow",
            "content": "Qoder orchestrates specialized sub-agents using structured rules and PowerContext handoff artifacts to preserve citations across sessions.",
            "key_takeaway": "Qoder 通过结构化规则与 PowerContext Handoff 在多轮科研会话间无损传递引用证据。",
            "flashcard_q": "Qoder 如何保证跨会话研究时不丢失前序论文的原文引用区间？",
            "flashcard_a": "通过导出带 MemoryCitation 列表的 powercontext.handoff.v1 制品并在新会话初始化时挂载。",
        },
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["status"] == "ingested"


def test_qoder_scholar_hybrid_brief_and_flashcards():
    max_bytes = 1100
    resp = client.post(
        "/api/scholar/brief",
        json={
            "research_question": "SeekDB Delta HNSW 与 PowerContext 字节预算证据筛选如何协同？",
            "max_bytes": max_bytes,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["prepared_context"].startswith("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert data["prepared_context"].endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert data["prepared_context_bytes"] <= max_bytes
    assert len(data["citations"]) >= 1
    assert len(data["flashcards"]) >= 2
    assert data["handoff"]["schema"] == "powercontext.handoff.v1"

    site_html = Path(__file__).parent / "site" / "index.html"
    assert site_html.exists()
    assert "OB-P:xI1C-CXPJXT4ztVpZDY6_A" in site_html.read_text(encoding="utf-8")
