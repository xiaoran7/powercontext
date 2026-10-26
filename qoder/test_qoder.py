from pathlib import Path
import time

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

    # 页面脚本是 type="module"，函数不挂在 window 上；内联 onclick 无法解析模块作用域函数。
    assert 'type="module"' in html_text
    assert "onclick=" not in html_text
    assert "onchange=" not in html_text
    assert "addEventListener('click', runBrief)" in html_text

    # 浏览器侧与 Function 侧共用同一份引擎实现，发布目录必须一并带上。
    assert (base / "site" / "engine.js").exists()
    assert (base / "dist" / "index.html").exists()
    assert (base / "dist" / "engine.js").exists()

    for doc in ["README.md", "VALIDATION_SAMPLE.md", "QODER_DEV_LOG.md", "USAGE_EFFECT_REPORT.md", "requirements.txt", ".env.example"]:
        assert (base / doc).exists(), f"Missing deliverable file: {doc}"


def test_learner_memory_is_merged_not_clobbered():
    """
    回归：客户端再次提交画像时，服务端必须与已存记忆取并集，
    既不能清空已掌握文献，也不能丢失已记录的薄弱知识点。
    """
    client.post(
        "/api/scholar/memory",
        json={
            "learner_id": "merge-test-01",
            "weak_topics": ["hybrid_rag"],
            "mastered_doc_ids": ["PAPER-VSAG-01"],
            "preferred_depth": "deep-derivation",
            "review_note": "首次记录",
        },
    )

    brief_resp = client.post(
        "/api/scholar/brief",
        json={
            "research_question": "RRF 与 HNSW 如何协同",
            "max_bytes": 1400,
            "learner_id": "merge-test-01",
            "memory": {
                "weak_topics": ["vector_index"],
                "mastered_doc_ids": [],
                "preferred_depth": "standard",
                "review_note": "",
            },
        },
    )
    assert brief_resp.status_code == 200
    data = brief_resp.json()
    reused = data["reused_learner_memory"]

    # 已存记忆与请求记忆取并集
    assert set(reused["weak_topics"]) == {"hybrid_rag", "vector_index"}
    assert reused["mastered_doc_ids"] == ["PAPER-VSAG-01"]
    # 请求侧给了深度时优先采用请求侧
    assert reused["preferred_depth"] == "standard"
    assert reused["review_note"] == "首次记录"

    # 薄弱知识点命中判定：新旧两个主题都能命中
    weak_docs = {c["doc_id"] for c in data["flashcards"] if c["priority"] == "high_weak_topic"}
    assert "PAPER-RRF-03" in weak_docs
    # 已掌握是更具体的信号，优先于主题级薄弱点
    mastered_card = next(c for c in data["flashcards"] if c["doc_id"] == "PAPER-VSAG-01")
    assert mastered_card["priority"] == "low_mastered"
    assert mastered_card["mastered"] is True

    # 简报信封内回写了学习者记忆行
    assert "learner_memory: id=merge-test-01 weak_topics=hybrid_rag,vector_index" in data["prepared_context"]


def test_adaptive_flashcard_scheduling_and_concept_edges():
    """验证闪卡携带自适应复习间隔，且概念图谱只连接共享同一主题的文献"""
    client.post(
        "/api/scholar/memory",
        json={
            "learner_id": "adaptive-test-01",
            "weak_topics": ["hybrid_rag"],
            "mastered_doc_ids": ["PAPER-VSAG-01"],
        },
    )
    resp = client.post(
        "/api/scholar/brief",
        json={
            "research_question": "混合检索 RRF 与字节预算",
            "max_bytes": 1400,
            "learner_id": "adaptive-test-01",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    weak_card = next(c for c in data["flashcards"] if c["priority"] == "high_weak_topic")
    normal_card = next(c for c in data["flashcards"] if c["priority"] == "normal")
    mastered_card = next(c for c in data["flashcards"] if c["priority"] == "low_mastered")

    # 薄弱点优先、已掌握后置，且复习间隔随优先级自适应
    assert weak_card["next_review_days"] < normal_card["next_review_days"] <= mastered_card["next_review_days"]
    assert weak_card["weak_topic_match"] is True
    assert mastered_card["mastered"] is True
    assert data["flashcards"][0]["priority"] == "high_weak_topic"

    # 概念边只连接共享主题的文献
    for edge in data["concept_graph_edges"]:
        assert edge["relation"].startswith("shares_topic(")


def test_function_compatible_api_prefix():
    """前端在 Qoder Sites 与本地 FastAPI 上使用同一套路径前缀"""
    resp = client.post(
        "/functions/v1/app/api/scholar/brief",
        json={
            "research_question": "SeekDB 与 PowerContext 如何协同",
            "max_bytes": 1200,
            "learner_id": "prefix-test-01",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["prepared_context"].startswith("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")


def test_status_endpoint_is_read_only():
    """GET /api/scholar/status 不得写入语料行"""
    engine = ScholarPulseEngine()
    before = engine.get_status()["total_sections"]
    resp = client.get("/api/scholar/status")
    assert resp.status_code == 200
    assert resp.json()["total_sections"] == before
    assert engine.get_status()["total_sections"] == before


def test_corpus_library_listing():
    """文献库接口应返回按论文聚合的章节、主题分布，且不含 learner_memory 行"""
    resp = client.get("/api/scholar/corpus")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["documents"]) >= 4
    assert len(data["papers"]) >= 4
    assert all(d["topic"] != "learner_memory" for d in data["documents"])
    assert {t["topic"] for t in data["topics"]} >= {"vector_index", "hybrid_rag", "context_engineering"}
    assert all(p["section_count"] >= 1 for p in data["papers"])


def test_batch_import_and_scoped_retrieval():
    """批量导入论文后，应能按章节 ID 聚焦检索到刚导入的内容"""
    import_resp = client.post(
        "/api/scholar/ingest/batch",
        json={
            "documents": [
                {
                    "doc_id": "PAPER-IMPORT-TEST-01",
                    "paper_title": "Imported Paper For Scoped Retrieval",
                    "section": "Section 1.1: Scoped Retrieval",
                    "topic": "agent_workflow",
                    "content": "A freshly imported section used to verify that scoped retrieval only returns the selected paper.",
                    "key_takeaway": "导入章节可被聚焦检索命中。",
                    "flashcard": {
                        "question": "导入的章节能被检索到吗？",
                        "answer": "可以，按章节 ID 聚焦即可命中。",
                        "interval_days": 1,
                        "ease": 2.5,
                    },
                }
            ]
        },
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["count"] == 1

    brief_resp = client.post(
        "/api/scholar/brief",
        json={
            "research_question": "聚焦检索 导入章节",
            "max_bytes": 1400,
            "learner_id": "scope-test-01",
            "scope_doc_ids": ["PAPER-IMPORT-TEST-01"],
        },
    )
    assert brief_resp.status_code == 200
    data = brief_resp.json()
    assert data["flashcards"], "聚焦检索应至少命中一篇文献"
    assert {c["doc_id"] for c in data["flashcards"]} == {"PAPER-IMPORT-TEST-01"}


def test_review_grading_advances_interval():
    """复习评分按 SM-2 推进间隔、难度系数与到期时间，并写回学习者画像"""
    learner = "review-test-01"
    first = client.post(
        "/api/scholar/review",
        json={"learner_id": learner, "doc_id": "PAPER-RRF-03", "grade": 5},
    )
    assert first.status_code == 200
    card = first.json()["card"]
    assert card["reps"] == 1
    assert card["interval_days"] == 1
    assert card["ease"] > 2.5

    second = client.post(
        "/api/scholar/review",
        json={"learner_id": learner, "doc_id": "PAPER-RRF-03", "grade": 4},
    )
    assert second.status_code == 200
    card = second.json()["card"]
    assert card["reps"] == 2
    assert card["interval_days"] == 3

    # 忘记应清零连击并把卡片立即拉回到期队列
    lapsed = client.post(
        "/api/scholar/review",
        json={"learner_id": learner, "doc_id": "PAPER-RRF-03", "grade": 0},
    )
    assert lapsed.status_code == 200
    card = lapsed.json()["card"]
    assert card["reps"] == 0
    assert card["lapses"] == 1
    assert card["due_at"] <= int(time.time())

    # 画像中持久化了调度状态，简报会采用 SM-2 间隔而非主题默认值
    brief = client.post(
        "/api/scholar/brief",
        json={"research_question": "RRF 融合", "max_bytes": 1400, "learner_id": learner},
    )
    assert brief.status_code == 200
    card_state = next(c for c in brief.json()["flashcards"] if c["doc_id"] == "PAPER-RRF-03")
    assert card_state["reviewed"] is True
    assert card_state["lapses"] == 1


def test_invalid_review_grade_rejected():
    resp = client.post(
        "/api/scholar/review",
        json={"learner_id": "review-test-02", "doc_id": "PAPER-RRF-03", "grade": 2},
    )
    assert resp.status_code == 400
