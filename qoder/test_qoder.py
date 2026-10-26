from pathlib import Path
from fastapi.testclient import TestClient
from server import app
from scholar_engine import ScholarPulseEngine


client = TestClient(app)


def test_qoder_scholar_status_and_ingest():
    """验证 SeekDB 文献知识库初始化、重置与新笔记入库"""
    reset_resp = client.post("/api/scholar/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["total_sections"] >= 4

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


def test_powercontext_learner_memory_persistence_and_flashcard_adaptation():
    """
    验证 PowerContext 学习者记忆保存、跨实例重启读取，以及在后续生成精读简报与复习闪卡中实际发挥作用
    """
    save_resp = client.post(
        "/api/scholar/memory",
        json={
            "learner_id": "scholar-test-01",
            "weak_topics": ["hybrid_rag"],
            "mastered_doc_ids": ["PAPER-VSAG-01"],
            "preferred_depth": "deep-derivation",
            "review_note": "重点攻克 RRF 倒数排名融合公式",
        },
    )
    assert save_resp.status_code == 200
    assert save_resp.json()["status"] == "saved"

    # 新建独立引擎实例模拟重启，核对记忆可持久化读取
    restarted = ScholarPulseEngine()
    loaded_mem = restarted.get_learner_memory("scholar-test-01")
    assert loaded_mem is not None
    assert "hybrid_rag" in loaded_mem["weak_topics"]
    assert "PAPER-VSAG-01" in loaded_mem["mastered_doc_ids"]

    # 调用精读简报与闪卡生成，验证薄弱知识点自动置顶且字节预算严格守恒
    max_bytes = 1200
    brief_resp = client.post(
        "/api/scholar/brief",
        json={
            "research_question": "SeekDB Delta HNSW 与 RRF 混合检索在字节预算下如何协同？",
            "max_bytes": max_bytes,
            "learner_id": "scholar-test-01",
        },
    )
    assert brief_resp.status_code == 200
    data = brief_resp.json()
    assert data["prepared_context"].startswith("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert "learner_memory: id=scholar-test-01 weak_topics=hybrid_rag" in data["prepared_context"]
    assert data["prepared_context"].endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert data["prepared_context_bytes"] <= max_bytes
    assert len(data["citations"]) >= 1
    assert len(data["flashcards"]) >= 2
    assert data["flashcards"][0]["priority"] == "high_weak_topic"
    assert data["handoff"]["schema"] == "powercontext.handoff.v1"


def test_qoder_site_and_deliverables_completeness():
    """验证 Qoder Sites 静态站点与全部提交文档齐全"""
    base = Path(__file__).parent
    site_html = base / "site" / "index.html"
    assert site_html.exists()
    html_text = site_html.read_text(encoding="utf-8")
    assert "OB-P:xI1C-CXPJXT4ztVpZDY6_A" in html_text
    assert "https://github.com/xiaoran7/powercontext" in html_text

    for doc in ["README.md", "VALIDATION_SAMPLE.md", "QODER_DEV_LOG.md", "USAGE_EFFECT_REPORT.md", "requirements.txt", ".env.example"]:
        assert (base / doc).exists(), f"Missing deliverable file: {doc}"
