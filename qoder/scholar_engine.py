import hashlib
import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence
import pymysql

try:
    from powercontext import ArtifactAddress, ArtifactRef, SourceRef
    HAS_POWERCONTEXT = True
except Exception:
    HAS_POWERCONTEXT = False

SEEKDB_HOST = os.getenv("SEEKDB_HOST", "100.72.34.104")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASS = os.getenv("SEEKDB_PASS", "")
SEEKDB_DB = os.getenv("SEEKDB_DB", "powercontext")
EMBEDDING_DIM = 512
RRF_K = 60
MAX_STUDY_HISTORY = 200

_DEPTH_SPAN_CHARS = {"deep-derivation": 150, "standard": 110, "brief": 80}

_TOPIC_ALIASES = {
    # 现有与经典别名兼容
    "rrf": "rag_context",
    "reciprocal rank fusion": "rag_context",
    "hybrid_rag": "rag_context",
    "vector_index": "vector_retrieval",
    "hnsw": "vector_retrieval",
    "vsag": "vector_retrieval",
    "delta hnsw": "vector_retrieval",
    "snapshot hnsw": "vector_retrieval",
    "byte budget": "rag_context",
    "evidence selection": "rag_context",
    "prepared context": "rag_context",
    "context_engineering": "context_engineering",
    # 五大学术领域完整别名映射
    "distributed_database": "distributed_database",
    "paxos": "distributed_database",
    "raft": "distributed_database",
    "spanner": "distributed_database",
    "oceanbase": "distributed_database",
    "lsm": "distributed_database",
    "consensus": "distributed_database",
    "vector_retrieval": "vector_retrieval",
    "faiss": "vector_retrieval",
    "ivf": "vector_retrieval",
    "pq": "vector_retrieval",
    "simd": "vector_retrieval",
    "ann": "vector_retrieval",
    "llm_reasoning": "llm_reasoning",
    "attention": "llm_reasoning",
    "transformer": "llm_reasoning",
    "flashattention": "llm_reasoning",
    "tot": "llm_reasoning",
    "tree of thoughts": "llm_reasoning",
    "rag_context": "rag_context",
    "dpr": "rag_context",
    "hybrid": "rag_context",
    "memory_cognition": "memory_cognition",
    "sm2": "memory_cognition",
    "anki": "memory_cognition",
    "spaced repetition": "memory_cognition",
    "forgetting curve": "memory_cognition",
}


def _scholar_embedding(text: str, dim: int = EMBEDDING_DIM) -> List[float]:
    vec = [0.0005] * dim
    tokens = text.lower().replace("\n", " ").split()
    clean = "".join(ch for ch in text.lower() if not ch.isspace())
    ngrams = [clean[i : i + 2] for i in range(max(0, len(clean) - 1))]
    for tok in tokens + ngrams:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        idx1 = int.from_bytes(digest[0:2], "big") % dim
        idx2 = int.from_bytes(digest[2:4], "big") % dim
        sign = 1.0 if (digest[4] % 2 == 0) else -1.0
        vec[idx1] += 1.0
        vec[idx2] += 0.5 * sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _normalize_topic(value: str) -> str:
    return re.sub(r"[\s\-/]+", "_", value.strip().lower()).strip("_")


def _normalize_topics(entries: Sequence[str]) -> List[str]:
    """薄弱知识点归一化为小写下划线 slug，便于与语料 topic 精确对齐。"""
    seen: List[str] = []
    for entry in entries or []:
        if not isinstance(entry, str):
            continue
        norm = _normalize_topic(entry)
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def _normalize_doc_ids(entries: Sequence[str]) -> List[str]:
    """文献 ID 保留原始大小写，仅去空白与去重。"""
    seen: List[str] = []
    for entry in entries or []:
        if not isinstance(entry, str):
            continue
        norm = entry.strip()
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def _merge_memory(
    stored: Optional[Dict[str, Any]], supplied: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    学习者记忆合并：存储态与请求态按并集合并，客户端持有的画像始终是服务端画像的超集。
    """
    stored = stored or {}
    supplied = supplied or {}
    weak = _normalize_topics(list(stored.get("weak_topics") or []) + list(supplied.get("weak_topics") or []))
    mastered = _normalize_doc_ids(
        list(stored.get("mastered_doc_ids") or []) + list(supplied.get("mastered_doc_ids") or [])
    )
    depth = supplied.get("preferred_depth") or stored.get("preferred_depth") or "deep-derivation"
    note = supplied.get("review_note") or stored.get("review_note") or ""
    cards = dict(stored.get("cards") or {})
    cards.update(supplied.get("cards") or {})
    return {
        "weak_topics": weak,
        "mastered_doc_ids": mastered,
        "preferred_depth": depth,
        "review_note": note,
        "cards": cards,
    }


def _adaptive_interval(base_interval: float, ease: float, priority: str) -> int:
    if priority == "high_weak_topic":
        return max(1, int(round(base_interval * 0.5)))
    if priority == "low_mastered":
        return max(1, int(round(base_interval * ease * 2)))
    return max(1, int(round(base_interval * ease)))


def _load_seed_corpus() -> List[Dict[str, Any]]:
    """语料种子来自 corpus.json（与前端共用同一份数据源），代码中不再内嵌示例数据。"""
    corpus_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "functions", "corpus.json")
    with open(corpus_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    documents: List[Dict[str, Any]] = []
    for item in payload.get("documents", []):
        card = item.get("flashcard", {}) or {}
        documents.append(
            {
                "doc_id": item["doc_id"],
                "paper_title": item["paper_title"],
                "section": item["section"],
                "topic": item["topic"],
                "content": item["content"],
                "key_takeaway": item["key_takeaway"],
                "flashcard_q": card.get("question", ""),
                "flashcard_a": card.get("answer", ""),
                "interval_days": card.get("interval_days", 1),
                "ease": card.get("ease", 2.5),
            }
        )
    return documents


def _detect_section_intent(heading: str, body: str) -> str:
    target = f"{heading} {body}".lower()
    if any(k in target for k in ["架构", "实现", "机制", "结构", "设计", "pipeline", "architecture", "mechanism", "structure", "engine", "paxos", "raft", "lsm"]):
        return "mechanism"
    if any(k in target for k in ["算法", "公式", "计算", "数学", "推导", "algorithm", "formula", "softmax", "cosine", "distance", "rrf", "hnsw", "sm-2", "sm2", "quantiz"]):
        return "algorithm"
    if any(k in target for k in ["对比", "权衡", "评估", "性能", "延迟", "吞吐", "trade-off", "comparison", "benchmark", "latency", "throughput", "overhead", "vs"]):
        return "tradeoff"
    return "concept"


def _derive_question(heading: str, paper_title: str, body: str = "") -> str:
    intent = _detect_section_intent(heading, body)
    if intent == "mechanism":
        return f"在《{paper_title}》的「{heading}」中，系统如何实现其核心机制，有哪些关键设计？"
    if intent == "algorithm":
        return f"在《{paper_title}》中，针对「{heading}」，其核心算法推导或数学原理是什么？"
    if intent == "tradeoff":
        return f"《{paper_title}》在「{heading}」中指出了哪些关键技术指标权衡（Trade-offs）与工程取舍？"
    return f"{paper_title} 的「{heading}」说明了什么？"


def _derive_takeaway(heading: str, first_sentence: str, body: str = "") -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s*", body or first_sentence) if s.strip()]
    for s in sentences:
        if any(w in s for w in ["通过", "采用", "实现了", "核心是", "proves", "achieves", "combines", "integrates", "demonstrates", "proposes"]):
            return s if len(s) <= 140 else s[:140] + "…"
    trimmed = (first_sentence or "").strip()
    if not trimmed:
        return heading
    return trimmed if len(trimmed) <= 140 else trimmed[:140] + "…"


def _derive_answer(heading: str, body: str, takeaway: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s*", body) if s.strip()]
    if not sentences:
        return takeaway
    if len(sentences) == 1:
        return sentences[0][:240]
    combined = " ".join(sentences[:2])
    return combined[:240]


def generate_research_synthesis(
    research_question: str,
    hits: List[Dict[str, Any]],
    citations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    基于 SeekDB 混合检索命中的文献证据与 PowerContext 引用区间，
    自动生成包含执行摘要、跨文献对比分析、工程架构权衡与开放问题的结构化精读研报。
    """
    if not hits:
        return {
            "summary": "未检索到与该问题高置信度相关的文献，建议调整关键词或引入新论文。",
            "analysis_paragraphs": [],
            "trade_offs": [],
            "open_questions": ["如何扩展文献库以覆盖该研究领域？"],
        }

    top_hit = hits[0]
    summary = (
        f"围绕「{research_question}」，结合检索召回的 {len(hits)} 篇权威文献，"
        f"核心结论表明：{top_hit.get('key_takeaway', '')}"
    )

    paragraphs = []
    for idx, h in enumerate(hits[:4]):
        cite_badge = f"[{h['doc_id']}:§{h['section']}]"
        para = (
            f"**{idx + 1}. 《{h['paper_title']}》 ({h['section']})**：{h['content']} "
            f"其核心论据为：{h['key_takeaway']} {cite_badge}"
        )
        paragraphs.append(para)

    trade_offs = []
    topics = set(h.get("topic", "") for h in hits)
    if any("database" in t for t in topics):
        trade_offs.append("一致性 vs 吞吐量：通过分区 Paxos / TrueTime 约束外部一致性，但跨区事务需承担两阶段提交或 Commit Wait 延迟。")
    if any("vector" in t for t in topics):
        trade_offs.append("检索精度 vs 内存开销：双层 HNSW 或 IVF-PQ 在高维空间利用量化和图分层压缩 90% 显存，但在极高维稀疏特征下召回率存在长尾衰减。")
    if any("llm" in t for t in topics):
        trade_offs.append("计算复杂度 vs 显存带宽：FlashAttention 利用片上 SRAM 分块避免 HBM 频繁访存，显著提升 IO-Bound 算子算力利用率。")
    if any("rag" in t or "context" in t for t in topics):
        trade_offs.append("全量上下文 vs 严格预算：PowerContext 通过字符级证据抽取在受限窗口保留高信噪比论据，避免长文本带来的模型注意力分散。")
    if not trade_offs:
        trade_offs.append("语义召回 vs 精确匹配：RRF (k=60) 融合向量余弦距离与词法 BM25，有效平衡冷启动术语缺失与复杂长查询语义泛化。")

    open_questions = [
        f"如何在严格 UTF-8 字节预算内进一步压缩《{top_hit['paper_title']}》的证明与公式推导？",
        "多轮 Agent 在跨会话长程研究中，如何协同复用学习者薄弱点记忆与增量 ContextPackDelta？",
    ]

    return {
        "summary": summary,
        "analysis_paragraphs": paragraphs,
        "trade_offs": trade_offs,
        "open_questions": open_questions,
    }


def parse_paper(raw_text: str, topic: str = "context_engineering") -> List[Dict[str, Any]]:
    """按标题层级切分论文正文，无标题层级时退化为按空行分段。"""
    text = re.sub(r"\r\n", "\n", str(raw_text or "")).strip()
    if not text:
        return []
    lines = text.split("\n")
    title_line = next((line for line in lines if re.match(r"^#\s+", line)), None)
    paper_title = (re.sub(r"^#\s+", "", title_line) if title_line else "").strip() or "未命名文献"

    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def body_text(section: Optional[Dict[str, Any]]) -> str:
        return "\n".join(section["body"]).strip() if section else ""

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            if body_text(current):
                sections.append(current)
            current = {"heading": heading.group(2).strip(), "body": []}
            continue
        if current is None:
            current = {"heading": "", "body": []}
        current["body"].append(line)
    if body_text(current):
        sections.append(current)

    has_headings = any(section["heading"] for section in sections)
    usable = [section for section in sections if body_text(section)]
    if not has_headings:
        usable = [
            {"heading": "", "body": block.split("\n")}
            for block in re.split(r"\n\s*\n", text)
            if "".join(block.split("\n")).strip()
        ]

    parsed: List[Dict[str, Any]] = []
    for index, section in enumerate(usable):
        body = body_text(section)
        first_sentence = re.split(r"(?<=[.!?。！？])\s*", body)[0].strip() or body[:120]
        heading_text = section["heading"] or f"段落 {index + 1}"
        takeaway = _derive_takeaway(heading_text, first_sentence, body)
        answer = _derive_answer(heading_text, body, takeaway)
        parsed.append(
            {
                "paper_title": paper_title,
                "section": heading_text,
                "content": body,
                "key_takeaway": takeaway,
                "flashcard": {
                    "question": _derive_question(heading_text, paper_title, body),
                    "answer": answer,
                    "interval_days": 1,
                    "ease": 2.5,
                },
                "topic": topic,
            }
        )
    return parsed


DEFAULT_RESEARCH_CORPUS: List[Dict[str, Any]] = _load_seed_corpus()

SM2_GRADES = (0, 3, 4, 5)


def _apply_review(cards: Dict[str, Any], doc_id: str, grade: int, now_seconds: int) -> Dict[str, Any]:
    """SM-2 变体：根据复习评分推进每张卡片的间隔、难度系数与到期时间。"""
    current = dict((cards or {}).get(doc_id) or {"ease": 2.5, "interval_days": 0, "due_at": 0, "reps": 0, "lapses": 0})
    nxt = dict(current)
    if grade < 3:
        nxt["reps"] = 0
        nxt["lapses"] = int(current.get("lapses", 0)) + 1
        nxt["interval_days"] = 1
        nxt["ease"] = max(1.3, round(float(current.get("ease", 2.5)) - 0.2, 2))
    else:
        reps = int(current.get("reps", 0))
        if reps == 0:
            nxt["interval_days"] = 1
        elif reps == 1:
            nxt["interval_days"] = 3
        else:
            nxt["interval_days"] = max(
                1, round(float(current.get("interval_days", 1)) * float(current.get("ease", 2.5)))
            )
        nxt["reps"] = reps + 1
        delta = 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)
        nxt["ease"] = min(2.8, max(1.3, round(float(current.get("ease", 2.5)) + delta, 2)))
    nxt["due_at"] = now_seconds if grade < 3 else now_seconds + nxt["interval_days"] * 86400
    nxt["last_grade"] = grade
    nxt["last_reviewed_at"] = now_seconds
    updated = dict(cards or {})
    updated[doc_id] = nxt
    return updated


def _corpus_topics(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for doc in documents:
        topic = _normalize_topic(doc.get("topic", ""))
        if not topic:
            continue
        counts[topic] = counts.get(topic, 0) + 1
    return [{"topic": topic, "count": count} for topic, count in counts.items()]


def _group_by_paper(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    papers: Dict[str, Dict[str, Any]] = {}
    for doc in documents:
        key = doc.get("paper_title") or doc.get("doc_id")
        paper = papers.setdefault(key, {"paper_title": key, "topics": set(), "sections": [], "doc_ids": []})
        paper["topics"].add(_normalize_topic(doc.get("topic", "")))
        paper["sections"].append({"doc_id": doc["doc_id"], "section": doc.get("section", "")})
        paper["doc_ids"].append(doc["doc_id"])
    grouped = [
        {
            "paper_title": paper["paper_title"],
            "topics": sorted(t for t in paper["topics"] if t),
            "sections": paper["sections"],
            "doc_ids": paper["doc_ids"],
            "section_count": len(paper["sections"]),
        }
        for paper in papers.values()
    ]
    grouped.sort(key=lambda p: (-p["section_count"], p["paper_title"]))
    return grouped


class ScholarPulseEngine:
    """
    ScholarPulse：基于 Qoder × OceanBase SeekDB × PowerContext 的论文精读、概念图谱与间隔复习引擎。
    """

    def __init__(self, scope_id: str = "scope:qoder:scholar-pulse"):
        self.scope_id = scope_id
        self.study_history: List[Dict[str, Any]] = []

    def get_conn(self):
        return pymysql.connect(
            host=SEEKDB_HOST,
            port=SEEKDB_PORT,
            user=SEEKDB_USER,
            password=SEEKDB_PASS,
            database=SEEKDB_DB,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=10,
        )

    def ensure_schema(self) -> None:
        """只建表，不写数据。供只读接口使用，保证 GET 语义不产生行级变更。"""
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS qoder_scholar_store (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        doc_id VARCHAR(64) NOT NULL UNIQUE,
                        scope_id VARCHAR(128) NOT NULL,
                        paper_title VARCHAR(255) NOT NULL,
                        section VARCHAR(255) NOT NULL,
                        topic VARCHAR(64) NOT NULL,
                        content TEXT NOT NULL,
                        key_takeaway TEXT NOT NULL,
                        flashcard_json TEXT NOT NULL,
                        powercontext_source_ref VARCHAR(255) NOT NULL,
                        embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        VECTOR INDEX idx_qoder_scholar_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
                        FULLTEXT INDEX idx_qoder_scholar_fts (paper_title, section, content, key_takeaway) WITH PARSER ik
                    ) DEFAULT CHARSET=utf8mb4;
                """)
        finally:
            conn.close()

    def seed_corpus(self, force_reset: bool = False) -> int:
        """写入默认文献语料并返回非 learner_memory 的文献条目数。"""
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                if force_reset:
                    cur.execute("DELETE FROM qoder_scholar_store WHERE scope_id = %s;", (self.scope_id,))

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM qoder_scholar_store WHERE scope_id = %s AND topic != 'learner_memory';",
                    (self.scope_id,),
                )
                cnt = cur.fetchone()["cnt"]
                if cnt < len(DEFAULT_RESEARCH_CORPUS) or force_reset:
                    for item in DEFAULT_RESEARCH_CORPUS:
                        emb = _scholar_embedding(
                            f"{item['doc_id']} {item['paper_title']} {item['section']} {item['content']} {item['key_takeaway']}"
                        )
                        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
                        src_ref = (
                            str(SourceRef(source_type="paper_section", source_id=item["doc_id"]))
                            if HAS_POWERCONTEXT
                            else f"paper_section:{item['doc_id']}"
                        )
                        fc_json = json.dumps(
                            {
                                "question": item["flashcard_q"],
                                "answer": item["flashcard_a"],
                                "interval_days": item.get("interval_days", 1),
                                "ease": item.get("ease", 2.5),
                            },
                            ensure_ascii=False,
                        )
                        cur.execute(
                            """
                            INSERT INTO qoder_scholar_store
                            (doc_id, scope_id, paper_title, section, topic, content, key_takeaway, flashcard_json, powercontext_source_ref, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                content = VALUES(content),
                                key_takeaway = VALUES(key_takeaway),
                                flashcard_json = VALUES(flashcard_json),
                                embedding = VALUES(embedding)
                            """,
                            (
                                item["doc_id"],
                                self.scope_id,
                                item["paper_title"],
                                item["section"],
                                item["topic"],
                                item["content"],
                                item["key_takeaway"],
                                fc_json,
                                src_ref,
                                vec_str,
                            ),
                        )
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM qoder_scholar_store WHERE scope_id = %s AND topic != 'learner_memory';",
                    (self.scope_id,),
                )
                total = cur.fetchone()["cnt"]
        finally:
            conn.close()
        return total

    def init_and_seed_seekdb(self, force_reset: bool = False) -> Dict[str, Any]:
        self.ensure_schema()
        total = self.seed_corpus(force_reset=force_reset)
        return {
            "status": "online",
            "seekdb_host": f"{SEEKDB_HOST}:{SEEKDB_PORT}",
            "scope_id": self.scope_id,
            "total_sections": total,
        }

    def get_status(self) -> Dict[str, Any]:
        """只读状态探针：建表后仅做统计，不写入语料。"""
        self.ensure_schema()
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM qoder_scholar_store WHERE scope_id = %s AND topic != 'learner_memory';",
                    (self.scope_id,),
                )
                total = cur.fetchone()["cnt"]
        finally:
            conn.close()
        return {
            "status": "online",
            "seekdb_host": f"{SEEKDB_HOST}:{SEEKDB_PORT}",
            "scope_id": self.scope_id,
            "total_sections": total,
        }

    def save_learner_memory(
        self,
        learner_id: str,
        weak_topics: List[str],
        mastered_doc_ids: List[str],
        preferred_depth: str = "deep-derivation",
        review_note: str = "",
        cards: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        使用 PowerContext 保存学习者的薄弱知识点、已掌握文献与复习偏好，持久化至 SeekDB 并在后续跨会话复习中复用。
        """
        doc_id = f"MEMORY-{learner_id}"
        artifact_addr = (
            str(
                ArtifactAddress(
                    scope_id=self.scope_id,
                    artifact=ArtifactRef(family="learner_memory", artifact_id=learner_id.lower(), revision=1),
                )
            )
            if HAS_POWERCONTEXT
            else f"learner_memory/{learner_id}@v1"
        )
        payload = {
            "learner_id": learner_id,
            "weak_topics": _normalize_topics(weak_topics),
            "mastered_doc_ids": _normalize_doc_ids(mastered_doc_ids),
            "preferred_depth": preferred_depth,
            "review_note": review_note,
            "cards": cards or {},
            "artifact_address": artifact_addr,
            "updated_at": int(time.time()),
        }
        content_text = (
            f"学习者 {learner_id} 薄弱知识点：{', '.join(payload['weak_topics'])}；"
            f"已掌握文献：{', '.join(payload['mastered_doc_ids'])}；偏好：{preferred_depth}；备注：{review_note}"
        )
        emb = _scholar_embedding(content_text)
        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO qoder_scholar_store
                    (doc_id, scope_id, paper_title, section, topic, content, key_takeaway, flashcard_json, powercontext_source_ref, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        content = VALUES(content),
                        key_takeaway = VALUES(key_takeaway),
                        flashcard_json = VALUES(flashcard_json),
                        powercontext_source_ref = VALUES(powercontext_source_ref),
                        embedding = VALUES(embedding)
                    """,
                    (
                        doc_id,
                        self.scope_id,
                        f"Learner Memory ({learner_id})",
                        "PowerContext Persistent Study Profile",
                        "learner_memory",
                        content_text,
                        review_note or f"优先强化 {', '.join(payload['weak_topics'])}",
                        json.dumps(payload, ensure_ascii=False),
                        artifact_addr,
                        vec_str,
                    ),
                )
        finally:
            conn.close()
        return {"status": "saved", "learner_id": learner_id, "memory": payload}

    def get_learner_memory(self, learner_id: str) -> Optional[Dict[str, Any]]:
        """从 SeekDB 读取持久化的学习者记忆（跨实例重启后可直接复用）。"""
        doc_id = f"MEMORY-{learner_id}"
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id, content, flashcard_json, powercontext_source_ref
                    FROM qoder_scholar_store
                    WHERE scope_id = %s AND doc_id = %s AND topic = 'learner_memory'
                    """,
                    (self.scope_id, doc_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return json.loads(row["flashcard_json"])

    def ingest_research_note(
        self,
        doc_id: str,
        paper_title: str,
        section: str,
        topic: str,
        content: str,
        key_takeaway: str,
        flashcard_q: str,
        flashcard_a: str,
    ) -> Dict[str, Any]:
        emb = _scholar_embedding(f"{doc_id} {paper_title} {section} {content} {key_takeaway}")
        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
        src_ref = (
            str(SourceRef(source_type="paper_section", source_id=doc_id))
            if HAS_POWERCONTEXT
            else f"paper_section:{doc_id}"
        )
        fc_json = json.dumps(
            {"question": flashcard_q, "answer": flashcard_a, "interval_days": 1, "ease": 2.5},
            ensure_ascii=False,
        )
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO qoder_scholar_store
                    (doc_id, scope_id, paper_title, section, topic, content, key_takeaway, flashcard_json, powercontext_source_ref, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        content = VALUES(content),
                        key_takeaway = VALUES(key_takeaway),
                        flashcard_json = VALUES(flashcard_json),
                        embedding = VALUES(embedding)
                    """,
                    (
                        doc_id,
                        self.scope_id,
                        paper_title,
                        section,
                        topic,
                        content,
                        key_takeaway,
                        fc_json,
                        src_ref,
                        vec_str,
                    ),
                )
        finally:
            conn.close()
        return {"status": "ingested", "doc_id": doc_id, "powercontext_source_ref": src_ref}

    def list_documents(self) -> Dict[str, Any]:
        """列出文献库：按论文聚合的章节、主题分布与可回查引用来源。"""
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id, paper_title, section, topic, key_takeaway, powercontext_source_ref
                    FROM qoder_scholar_store
                    WHERE scope_id = %s AND topic != 'learner_memory'
                    ORDER BY paper_title ASC, doc_id ASC
                    """,
                    (self.scope_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        documents = [
            {
                "doc_id": r["doc_id"],
                "paper_title": r["paper_title"],
                "section": r["section"],
                "topic": r["topic"],
                "key_takeaway": r["key_takeaway"],
                "powercontext_source_ref": r["powercontext_source_ref"],
            }
            for r in rows
        ]
        return {
            "documents": documents,
            "topics": _corpus_topics(documents),
            "papers": _group_by_paper(documents),
        }

    def ingest_documents(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量导入论文章节（由导入入口按标题层级切分后写入）。"""
        accepted: List[str] = []
        for item in documents:
            card = item.get("flashcard") or {}
            self.ingest_research_note(
                doc_id=item["doc_id"],
                paper_title=item.get("paper_title", ""),
                section=item.get("section", ""),
                topic=item.get("topic", "context_engineering"),
                content=item.get("content", ""),
                key_takeaway=item.get("key_takeaway", ""),
                flashcard_q=card.get("question", ""),
                flashcard_a=card.get("answer", ""),
            )
            accepted.append(item["doc_id"])
        return {"status": "ingested", "doc_ids": accepted, "count": len(accepted)}

    def record_review(self, learner_id: str, doc_id: str, grade: int) -> Dict[str, Any]:
        """记录一次间隔复习评分，按 SM-2 推进该卡片的间隔与难度系数。"""
        if grade not in SM2_GRADES:
            raise ValueError(f"unsupported review grade: {grade}")
        stored = self.get_learner_memory(learner_id) or {}
        cards = _apply_review(stored.get("cards") or {}, doc_id, grade, int(time.time()))
        self.save_learner_memory(
            learner_id=learner_id,
            weak_topics=stored.get("weak_topics") or [],
            mastered_doc_ids=stored.get("mastered_doc_ids") or [],
            preferred_depth=stored.get("preferred_depth") or "deep-derivation",
            review_note=stored.get("review_note") or "",
            cards=cards,
        )
        return {"status": "reviewed", "learner_id": learner_id, "card": cards[doc_id]}

    def hybrid_search_papers(
        self,
        query: str,
        topic: Optional[str] = None,
        top_k: int = 4,
        scope_doc_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """在远端 SeekDB 中通过单条 UNION ALL SQL 同时执行 HNSW 向量检索与 IK 全文检索并 RRF 融合"""
        emb = _scholar_embedding(query)
        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"

        filters = ["scope_id = %s", "topic != 'learner_memory'"]
        params_base: List[Any] = [self.scope_id]
        if topic:
            filters.append("topic = %s")
            params_base.append(topic)
        if scope_doc_ids:
            placeholders = ", ".join(["%s"] * len(scope_doc_ids))
            filters.append(f"doc_id IN ({placeholders})")
            params_base.extend(scope_doc_ids)

        where_sql = " WHERE " + " AND ".join(filters)
        fts_where_sql = " WHERE " + " AND ".join(
            filters
            + ["MATCH(paper_title, section, content, key_takeaway) AGAINST (%s IN NATURAL LANGUAGE MODE) > 0"]
        )

        sql = f"""
            (
                SELECT
                    doc_id, paper_title, section, topic, content, key_takeaway,
                    flashcard_json, powercontext_source_ref,
                    'vector' AS channel,
                    COSINE_DISTANCE(embedding, %s) AS distance,
                    0.0 AS fts_score
                FROM qoder_scholar_store
                {where_sql}
                ORDER BY COSINE_DISTANCE(embedding, %s) APPROXIMATE
                LIMIT %s
            )
            UNION ALL
            (
                SELECT
                    doc_id, paper_title, section, topic, content, key_takeaway,
                    flashcard_json, powercontext_source_ref,
                    'fts' AS channel,
                    NULL AS distance,
                    MATCH(paper_title, section, content, key_takeaway) AGAINST (%s IN NATURAL LANGUAGE MODE) AS fts_score
                FROM qoder_scholar_store
                {fts_where_sql}
                ORDER BY fts_score DESC, doc_id ASC
                LIMIT %s
            )
        """
        cand_limit = max(top_k * 2, 8)
        full_params = (
            [vec_str]
            + params_base
            + [vec_str, cand_limit]
            + [query]
            + params_base
            + [query, cand_limit]
        )

        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(full_params))
                rows = cur.fetchall()
        finally:
            conn.close()

        merged: Dict[str, Dict[str, Any]] = {}
        v_rank = 0
        f_rank = 0
        for r in rows:
            did = r["doc_id"]
            if did not in merged:
                merged[did] = {
                    "doc_id": did,
                    "paper_title": r["paper_title"],
                    "section": r["section"],
                    "topic": r["topic"],
                    "content": r["content"],
                    "key_takeaway": r["key_takeaway"],
                    "flashcard": json.loads(r["flashcard_json"]),
                    "powercontext_source_ref": r["powercontext_source_ref"],
                    "channels": [],
                    "rrf_score": 0.0,
                }
            if r["channel"] == "vector":
                v_rank += 1
                if "vector" not in merged[did]["channels"]:
                    merged[did]["channels"].append("vector")
                merged[did]["rrf_score"] += 1.0 / (RRF_K + v_rank)
            else:
                f_rank += 1
                if "fts" not in merged[did]["channels"]:
                    merged[did]["channels"].append("fts")
                merged[did]["rrf_score"] += 1.0 / (RRF_K + f_rank)

        results = list(merged.values())
        for item in results:
            item["rrf_score"] = round(item["rrf_score"], 6)
        results.sort(key=lambda x: (-x["rrf_score"], x["doc_id"]))
        return results[:top_k]

    def _weak_hit_ids(self, hits: List[Dict[str, Any]], weak_topics: Sequence[str]) -> set:
        """
        薄弱知识点命中判定：主题精确/近义匹配优先，其次允许自由文本主题回落到论文原文关键词。
        """
        entries = _normalize_topics(weak_topics)
        if not entries:
            return set()
        weak_ids: set = set()
        for hit in hits:
            doc_topic = _normalize_topic(hit["topic"])
            matched = False
            for entry in entries:
                candidates = {entry}
                alias = _TOPIC_ALIASES.get(entry) or _TOPIC_ALIASES.get(entry.replace("_", " "))
                if alias:
                    candidates.add(alias)
                if any(c and (c == doc_topic or c in doc_topic or doc_topic in c) for c in candidates):
                    matched = True
                    break
            if not matched:
                haystack = f"{hit['topic']} {hit['key_takeaway']} {hit['content']}".lower()
                for entry in entries:
                    if len(entry) >= 2 and entry.replace("_", " ") in haystack:
                        matched = True
                        break
            if matched:
                weak_ids.add(hit["doc_id"])
        return weak_ids

    def build_research_brief_and_flashcards(
        self,
        research_question: str,
        max_bytes: int = 1600,
        learner_id: str = "default-scholar",
        memory: Optional[Dict[str, Any]] = None,
        scope_doc_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """
        结合 SeekDB 混合检索与 PowerContext 学习者记忆 + 字节预算装配，
        生成可溯源的文献精读简报（PreparedContext V1）、概念知识图谱边集与自适应间隔复习闪卡。
        """
        hits = self.hybrid_search_papers(
            query=research_question, top_k=4, scope_doc_ids=scope_doc_ids
        )
        stored_mem = self.get_learner_memory(learner_id)
        profile = _merge_memory(stored_mem, memory)
        weak_topics = profile["weak_topics"]
        mastered_docs = set(profile["mastered_doc_ids"])
        depth = profile["preferred_depth"]
        span_chars = _DEPTH_SPAN_CHARS.get(depth, _DEPTH_SPAN_CHARS["deep-derivation"])

        weak_ids = self._weak_hit_ids(hits, weak_topics)
        card_state = profile.get("cards") or {}
        now_seconds = int(time.time())

        mem_line = ""
        if weak_topics or stored_mem or memory:
            mem_line = (
                f"learner_memory: id={learner_id} weak_topics={','.join(weak_topics) or 'none'}"
                f" depth={depth}\n"
            )
        header = (
            "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
            f"scope_id: {self.scope_id}\n"
            f"research_question: {research_question}\n"
            f"{mem_line}"
            "evidence_spans:\n"
        )
        footer = "END_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        budget_left = max_bytes - len(header.encode("utf-8")) - len(footer.encode("utf-8"))

        lines: List[str] = []
        citations: List[Dict[str, Any]] = []
        flashcards: List[Dict[str, Any]] = []
        concept_edges: List[Dict[str, str]] = []

        for h in hits:
            body = f"{h['key_takeaway']} | {h['content']}"
            end_pos = min(len(body), span_chars)
            line = f"- [{h['doc_id']}] {body[:end_pos]} (citation={h['doc_id']}[0:{end_pos}])\n"
            line_bytes = len(line.encode("utf-8"))
            if line_bytes <= budget_left:
                lines.append(line)
                budget_left -= line_bytes
                citations.append(
                    {
                        "source_id": h["doc_id"],
                        "paper_title": h["paper_title"],
                        "section": h["section"],
                        "start": 0,
                        "end": end_pos,
                    }
                )
            is_weak = h["doc_id"] in weak_ids
            is_mastered = h["doc_id"] in mastered_docs
            # 已掌握是针对单篇文献的显式信号，优先于主题级薄弱点，避免把已掌握的文献推到队首。
            if is_mastered:
                priority = "low_mastered"
            elif is_weak:
                priority = "high_weak_topic"
            else:
                priority = "normal"
            fc = h["flashcard"]
            base_interval = float(fc.get("interval_days", 1) or 1)
            ease = float(fc.get("ease", 2.5) or 2.5)
            scheduled = card_state.get(h["doc_id"]) or {}
            next_review_days = int(scheduled.get("interval_days") or 0) or _adaptive_interval(
                base_interval, ease, priority
            )
            flashcards.append(
                {
                    "doc_id": h["doc_id"],
                    "topic": h["topic"],
                    "priority": priority,
                    "mastered": is_mastered,
                    "weak_topic_match": is_weak,
                    "question": fc.get("question", ""),
                    "answer": fc.get("answer", ""),
                    "interval_days": base_interval,
                    "ease": float(scheduled.get("ease") or ease),
                    "next_review_days": next_review_days,
                    "reviewed": bool(scheduled.get("last_reviewed_at")),
                    "due": bool(scheduled.get("due_at") and int(scheduled["due_at"]) <= now_seconds),
                    "reps": int(scheduled.get("reps") or 0),
                    "lapses": int(scheduled.get("lapses") or 0),
                }
            )

        for i, a in enumerate(hits):
            for b in hits[i + 1 :]:
                if a["topic"] == b["topic"]:
                    concept_edges.append(
                        {
                            "from": a["doc_id"],
                            "to": b["doc_id"],
                            "relation": f"shares_topic({a['topic']})",
                        }
                    )

        # 依据 PowerContext 学习者记忆，将薄弱知识点对应的闪卡优先置顶复习
        priority_rank = {"high_weak_topic": 0, "normal": 1, "low_mastered": 2}
        flashcards.sort(
            key=lambda c: (not c["due"], priority_rank.get(c["priority"], 1), c["next_review_days"])
        )

        prepared_context = header + "".join(lines) + footer
        handoff_addr = (
            str(
                ArtifactAddress(
                    scope_id=self.scope_id,
                    artifact=ArtifactRef(family="handoff", artifact_id="scholar-study-brief", revision=1),
                )
            )
            if HAS_POWERCONTEXT
            else "handoff/scholar-study-brief@v1"
        )

        record = {
            "research_question": research_question,
            "learner_id": learner_id,
            "reused_learner_memory": profile,
            "memory_source": "store" if stored_mem else ("request" if memory else "none"),
            "prepared_context": prepared_context,
            "prepared_context_bytes": len(prepared_context.encode("utf-8")),
            "max_bytes": max_bytes,
            "citations": citations,
            "flashcards": flashcards,
            "concept_graph_edges": concept_edges,
            "synthesis": generate_research_synthesis(research_question, hits, citations),
            "review_due": sum(1 for c in flashcards if c["due"]),
            "retrieval": {
                "fusion": "RRF",
                "rrf_k": RRF_K,
                "vector_hits": sum(1 for h in hits if "vector" in h["channels"]),
                "fts_hits": sum(1 for h in hits if "fts" in h["channels"]),
                "candidates": len(hits),
            },
            "handoff": {
                "schema": "powercontext.handoff.v1",
                "artifact_address": handoff_addr,
                "scope_id": self.scope_id,
                "citations": citations,
            },
        }
        self.study_history.append(record)
        if len(self.study_history) > MAX_STUDY_HISTORY:
            del self.study_history[: len(self.study_history) - MAX_STUDY_HISTORY]
        return record
