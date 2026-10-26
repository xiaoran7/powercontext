import hashlib
import json
import math
import os
import time
from typing import Any, Dict, List, Optional
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


DEFAULT_RESEARCH_CORPUS: List[Dict[str, Any]] = [
    {
        "doc_id": "PAPER-VSAG-01",
        "paper_title": "OceanBase SeekDB: Single-Stage Hybrid Vector and Full-Text Retrieval Engine",
        "section": "Section 3.2: Delta HNSW & Snapshot HNSW Compaction",
        "topic": "vector_index",
        "content": "SeekDB integrates the VSAG library to maintain a two-tier HNSW architecture: newly inserted vectors enter the mutable Delta HNSW buffer for immediate visibility, while background compaction merges them into the cache-aligned Snapshot HNSW graph.",
        "key_takeaway": "双层 HNSW（Delta + Snapshot）兼顾毫秒级实时写入可见性与高吞吐近似最近邻（ANN）检索。",
        "flashcard_q": "SeekDB 如何同时保障新增向量的实时可查性与大规模图索引的查询吞吐？",
        "flashcard_a": "采用 Delta HNSW 承接实时增量写入，并通过后台异步 Compaction 合并至只读优化的 Snapshot HNSW。",
    },
    {
        "doc_id": "PAPER-PC-02",
        "paper_title": "PowerContext: Deterministic Evidence Selection under Strict Byte Budgets",
        "section": "Section 4.1: UTF-8 Byte-Bounded PreparedContext Assembly",
        "topic": "context_engineering",
        "content": "Given a query and a strict max_bytes UTF-8 budget, PowerContext ranks active atomic memories via hybrid retrieval + HTTP reranking, extracts query-aligned text spans [start:end], and emits a verifiable BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1 envelope with MemoryCitation.",
        "key_takeaway": "在严格 UTF-8 字节预算内定位原文高相关子区间 [start:end] 并附带可回查的 MemoryCitation。",
        "flashcard_q": "PowerContext 在上下文窗口受限时如何避免整段长记忆截断导致的证据丢失？",
        "flashcard_a": "开启 evidence_selection=True，围绕查询词提取精确字符区间 [start:end] 并封装为带 MemoryCitation 的 PreparedContext V1 信封。",
    },
    {
        "doc_id": "PAPER-RRF-03",
        "paper_title": "Reciprocal Rank Fusion (RRF) for Lexical-Semantic Hybrid RAG",
        "section": "Section 2.4: Rank-Based Score Calibration",
        "topic": "hybrid_rag",
        "content": "Combining BM25 full-text scores with cosine distance requires rank-based normalization. RRF computes score(d) = 1/(k + rank_vec(d)) + 1/(k + rank_fts(d)) with k=60, eliminating sensitivity to uncalibrated score distributions across modalities.",
        "key_takeaway": "RRF（k=60）利用倒数排名平滑融合 BM25 词法匹配与 HNSW 向量距离，消除异构分值量纲差异。",
        "flashcard_q": "为什么在混合检索中常使用 RRF（倒数排名融合）而非直接将 BM25 分数与余弦相似度线性相加？",
        "flashcard_a": "BM25 无上界且受词频分布影响大，而余弦相似度位于 [-1,1]，RRF 基于次序融合（1/(k+rank)）无需分值归一化即可稳定校准。",
    },
    {
        "doc_id": "PAPER-DELTA-04",
        "paper_title": "Incremental Context Pack Transmission in Multi-Turn Agent Workflows",
        "section": "Section 5.3: Prefix Chunk Reuse and SHA-256 Verification",
        "topic": "context_engineering",
        "content": "Across multi-turn research sessions, ContextPackDelta.between(base_pack, target_pack) identifies shared immutable prefix chunks and transmits only the appended delta chunks plus a full-pack SHA-256 digest, cutting cross-agent bandwidth by over 78%.",
        "key_takeaway": "利用 ContextPackDelta 复用多轮会话的公共前缀分块，仅传输增量分块并通过 SHA-256 校验完整性。",
        "flashcard_q": "多轮 Agent 协作中如何降低重复发送长上下文包的网络与序列化开销？",
        "flashcard_a": "使用 ContextPackDelta.between(base, target) 复用公共前缀分块 ID，仅传输新增后缀分块并校验重建后的 SHA-256 摘要。",
    },
]


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

    def init_and_seed_seekdb(self, force_reset: bool = False) -> Dict[str, Any]:
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
                            {"question": item["flashcard_q"], "answer": item["flashcard_a"], "interval_days": 1, "ease": 2.5},
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
            "weak_topics": weak_topics,
            "mastered_doc_ids": mastered_doc_ids,
            "preferred_depth": preferred_depth,
            "review_note": review_note,
            "artifact_address": artifact_addr,
            "updated_at": int(time.time()),
        }
        content_text = (
            f"学习者 {learner_id} 薄弱知识点：{', '.join(weak_topics)}；"
            f"已掌握文献：{', '.join(mastered_doc_ids)}；偏好：{preferred_depth}；备注：{review_note}"
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
                        review_note or f"优先强化 {', '.join(weak_topics)}",
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

    def hybrid_search_papers(
        self, query: str, topic: Optional[str] = None, top_k: int = 4
    ) -> List[Dict[str, Any]]:
        """在远端 SeekDB 中通过单条 UNION ALL SQL 同时执行 HNSW 向量检索与 IK 全文检索并 RRF 融合"""
        emb = _scholar_embedding(query)
        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"

        filters = ["scope_id = %s", "topic != 'learner_memory'"]
        params_base: List[Any] = [self.scope_id]
        if topic:
            filters.append("topic = %s")
            params_base.append(topic)

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
                merged[did]["rrf_score"] += 1.0 / (60 + v_rank)
            else:
                f_rank += 1
                if "fts" not in merged[did]["channels"]:
                    merged[did]["channels"].append("fts")
                merged[did]["rrf_score"] += 1.0 / (60 + f_rank)

        results = list(merged.values())
        for item in results:
            item["rrf_score"] = round(item["rrf_score"], 6)
        results.sort(key=lambda x: (-x["rrf_score"], x["doc_id"]))
        return results[:top_k]

    def build_research_brief_and_flashcards(
        self,
        research_question: str,
        max_bytes: int = 1600,
        learner_id: str = "default-scholar",
    ) -> Dict[str, Any]:
        """
        结合 SeekDB 混合检索与 PowerContext 学习者记忆 + 字节预算装配，
        生成可溯源的文献精读简报（PreparedContext V1）、概念知识图谱边集与自适应间隔复习闪卡。
        """
        hits = self.hybrid_search_papers(query=research_question, top_k=4)
        learner_mem = self.get_learner_memory(learner_id)
        weak_topics = learner_mem.get("weak_topics", []) if learner_mem else []
        mastered_docs = set(learner_mem.get("mastered_doc_ids", [])) if learner_mem else set()

        mem_line = (
            f"learner_memory: id={learner_id} weak_topics={','.join(weak_topics) or 'none'}\n"
            if learner_mem
            else ""
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

        for idx, h in enumerate(hits):
            span_text = f"[{h['doc_id']}] {h['key_takeaway']} | {h['content']}"
            end_pos = min(len(span_text), 150)
            line = f"- {span_text[:end_pos]} (citation={h['doc_id']}[0:{end_pos}])\n"
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
            is_weak = h["topic"] in weak_topics
            is_mastered = h["doc_id"] in mastered_docs
            flashcards.append(
                {
                    "doc_id": h["doc_id"],
                    "topic": h["topic"],
                    "priority": "high_weak_topic" if is_weak else ("low_mastered" if is_mastered else "normal"),
                    "mastered": is_mastered,
                    "question": h["flashcard"]["question"],
                    "answer": h["flashcard"]["answer"],
                }
            )
            if idx > 0:
                concept_edges.append(
                    {
                        "from": hits[idx - 1]["doc_id"],
                        "to": h["doc_id"],
                        "relation": f"synergizes_in({h['topic']})",
                    }
                )

        # 依据 PowerContext 学习者记忆，将薄弱知识点对应的闪卡优先置顶复习
        priority_rank = {"high_weak_topic": 0, "normal": 1, "low_mastered": 2}
        flashcards.sort(key=lambda c: priority_rank.get(c["priority"], 1))

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
            "reused_learner_memory": learner_mem,
            "prepared_context": prepared_context,
            "prepared_context_bytes": len(prepared_context.encode("utf-8")),
            "max_bytes": max_bytes,
            "citations": citations,
            "flashcards": flashcards,
            "concept_graph_edges": concept_edges,
            "handoff": {
                "schema": "powercontext.handoff.v1",
                "artifact_address": handoff_addr,
                "scope_id": self.scope_id,
                "citations": citations,
            },
        }
        self.study_history.append(record)
        return record
