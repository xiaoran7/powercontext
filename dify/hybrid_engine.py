import os
import sys
import time
import math
import jieba
from typing import List, Dict, Any, Optional
import pymysql
from rank_bm25 import BM25Okapi
from fastembed import TextEmbedding

from mock_corpus import SAMPLE_CORPUS

sys.stdout.reconfigure(encoding="utf-8")

SEEKDB_HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
SEEKDB_DATABASE = os.environ.get("SEEKDB_DATABASE", "powercontext")
SEEKDB_USER = os.environ.get("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.environ.get("SEEKDB_PASSWORD", "")
VECTOR_DIM = 512


def ensure_seekdb_database() -> None:
    """确保目标 database 存在（seekdb 初始化后默认只有 test 库），便于本地一键启动。"""
    conn = pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{SEEKDB_DATABASE}` DEFAULT CHARACTER SET utf8mb4")
    finally:
        conn.close()

class HybridSearchEngine:
    def __init__(self):
        print("Initializing Embedding model (BAAI/bge-small-zh-v1.5) ...")
        self.embed_model = TextEmbedding("BAAI/bge-small-zh-v1.5")
        self.corpus: List[Dict[str, Any]] = SAMPLE_CORPUS
        self.doc_map: Dict[int, Dict[str, Any]] = {d["id"]: d for d in self.corpus}

        # Initialize BM25
        print("Tokenizing corpus with Jieba for BM25 ...")
        self.tokenized_corpus = [list(jieba.cut(f"{d['title']} {d['content']}")) for d in self.corpus]
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        print("BM25 Okapi index ready.")

    def get_connection(self):
        return pymysql.connect(
            host=SEEKDB_HOST,
            port=SEEKDB_PORT,
            user=SEEKDB_USER,
            password=SEEKDB_PASSWORD,
            database=SEEKDB_DATABASE,
            autocommit=True
        )

    def init_database_tables(self):
        """Create table and populate with sample corpus vectors if empty."""
        ensure_seekdb_database()
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS dify_knowledge_base (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            doc_id INT NOT NULL,
            title VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            category VARCHAR(64) DEFAULT 'general',
            source VARCHAR(64) DEFAULT 'official_docs',
            embedding VECTOR(512) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        try:
            cursor.execute("""
            CREATE VECTOR INDEX idx_dify_hnsw ON dify_knowledge_base (embedding)
            WITH (distance=cosine, type=hnsw, lib=vsag)
            """)
        except Exception:
            pass

        cursor.execute("SELECT COUNT(*) FROM dify_knowledge_base")
        count = cursor.fetchone()[0]

        if count == 0:
            print(f"Populating SeekDB dify_knowledge_base with {len(self.corpus)} sample documents...")
            texts = [f"{d['title']}: {d['content']}" for d in self.corpus]
            embeddings = list(self.embed_model.embed(texts))

            for doc, emb in zip(self.corpus, embeddings):
                vec_str = "[" + ", ".join(f"{x:.6f}" for x in emb) + "]"
                cursor.execute("""
                INSERT INTO dify_knowledge_base (doc_id, title, content, category, source, embedding)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (doc["id"], doc["title"], doc["content"], doc["category"], doc["source"], vec_str))
            print(f"Successfully populated {len(self.corpus)} documents into SeekDB.")
        else:
            print(f"SeekDB dify_knowledge_base already contains {count} documents.")

        cursor.close()
        conn.close()

    def get_query_vector(self, query: str) -> List[float]:
        vecs = list(self.embed_model.embed([query]))
        return vecs[0].tolist()

    def vector_search(self, query: str, top_k: int = 20, filter_source: Optional[str] = None) -> List[Dict[str, Any]]:
        """1. SeekDB 向量检索 (Dense Retrieval)"""
        q_vec = self.get_query_vector(query)
        vec_str = "[" + ", ".join(f"{x:.6f}" for x in q_vec) + "]"

        conn = self.get_connection()
        cursor = conn.cursor()

        where_sql = ""
        params = [vec_str]
        if filter_source and filter_source != "all":
            where_sql = "WHERE source = %s"
            params.append(filter_source)
        params.append(top_k)

        sql = f"""
            SELECT doc_id, title, content, category, source,
                   COSINE_DISTANCE(embedding, %s) as distance
            FROM dify_knowledge_base
            {where_sql}
            ORDER BY distance ASC
            LIMIT %s
        """
        start_t = time.perf_counter()
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        cursor.close()
        conn.close()

        results = []
        for rank, r in enumerate(rows, start=1):
            dist = float(r[5])
            sim = max(0.0, round((1.0 - dist) * 100.0, 2))
            results.append({
                "doc_id": r[0],
                "title": r[1],
                "content": r[2],
                "category": r[3],
                "source": r[4],
                "distance": round(dist, 4),
                "similarity_pct": sim,
                "vector_rank": rank,
                "elapsed_ms": round(elapsed_ms, 2)
            })
        return results

    def bm25_search(self, query: str, top_k: int = 20, filter_source: Optional[str] = None) -> List[Dict[str, Any]]:
        """2. BM25 关键词精确检索 (Sparse Retrieval)"""
        start_t = time.perf_counter()
        tokenized_q = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokenized_q)

        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        results = []
        rank = 1
        for idx in ranked_indices:
            score = scores[idx]
            if score <= 0.001:
                continue
            doc = self.corpus[idx]
            if filter_source and filter_source != "all" and doc["source"] != filter_source:
                continue

            results.append({
                "doc_id": doc["id"],
                "title": doc["title"],
                "content": doc["content"],
                "category": doc["category"],
                "source": doc["source"],
                "bm25_score": round(float(score), 4),
                "bm25_rank": rank
            })
            rank += 1
            if len(results) >= top_k:
                break

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        for r in results:
            r["elapsed_ms"] = round(elapsed_ms, 2)
        return results

    def hybrid_search_rrf(
        self,
        query: str,
        top_k: int = 20,
        vector_weight: float = 0.6,
        bm25_weight: float = 0.4,
        rrf_k: int = 60,
        filter_source: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """3. 倒数排名融合 (Reciprocal Rank Fusion - RRF) 混合粗排 (Top 20)"""
        vec_results = self.vector_search(query, top_k=top_k * 2, filter_source=filter_source)
        bm25_results = self.bm25_search(query, top_k=top_k * 2, filter_source=filter_source)

        vec_rank_map = {r["doc_id"]: r["vector_rank"] for r in vec_results}
        bm25_rank_map = {r["doc_id"]: r["bm25_rank"] for r in bm25_results}

        all_doc_ids = set(vec_rank_map.keys()) | set(bm25_rank_map.keys())

        rrf_scores = {}
        for doc_id in all_doc_ids:
            v_rank = vec_rank_map.get(doc_id, 999)
            b_rank = bm25_rank_map.get(doc_id, 999)

            score_v = vector_weight / (rrf_k + v_rank) if v_rank != 999 else 0.0
            score_b = bm25_weight / (rrf_k + b_rank) if b_rank != 999 else 0.0
            rrf_scores[doc_id] = score_v + score_b

        sorted_ids = sorted(all_doc_ids, key=lambda did: rrf_scores[did], reverse=True)[:top_k]

        candidates = []
        for rank, did in enumerate(sorted_ids, start=1):
            doc = self.doc_map[did]
            v_rank = vec_rank_map.get(did, None)
            b_rank = bm25_rank_map.get(did, None)

            candidates.append({
                "doc_id": did,
                "title": doc["title"],
                "content": doc["content"],
                "category": doc["category"],
                "source": doc["source"],
                "rrf_score": round(rrf_scores[did], 6),
                "rrf_rank": rank,
                "vector_rank": v_rank,
                "bm25_rank": b_rank,
                "vector_score": round(1.0 - (next((r['distance'] for r in vec_results if r['doc_id'] == did), 1.0)), 4) if v_rank else 0.0,
                "bm25_score": next((r['bm25_score'] for r in bm25_results if r['doc_id'] == did), 0.0) if b_rank else 0.0
            })

        return candidates

    def rerank_cross_scoring(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        4. 第二阶段精排 (Fine Reranker)
        采用深层特征交叉打分：
        - 关键词精确重合覆盖率 (Term Overlap Rate)
        - 连续关键短语/符号命中 (Exact Token Sequence Match, 如 batch_knn, 1064)
        - 向量语义亲和度
        - 产出最终 Rerank Score (0.00 ~ 1.00)
        """
        query_terms = set(jieba.cut(query))
        query_terms = {t.strip() for t in query_terms if len(t.strip()) > 1}

        reranked = []
        for c in candidates:
            text = f"{c['title']} {c['content']}"

            # 1. 词覆盖度 (0.0 ~ 1.0)
            matched_terms = [t for t in query_terms if t in text]
            overlap_ratio = len(matched_terms) / len(query_terms) if query_terms else 0.0

            # 2. 连续符号精准命中加权 (如代码符号、函数名、版本号)
            exact_bonus = 0.0
            for term in query_terms:
                if any(punct in term for punct in [".", "_", "-", "1", "2", "0"]):
                    if term in text:
                        exact_bonus += 0.25

            # 3. 原始语义向量分 (0.0 ~ 1.0)
            v_score = c.get("vector_score", 0.5)

            # 综合精排公式
            final_rerank_score = (
                0.40 * v_score +
                0.35 * overlap_ratio +
                0.25 * min(1.0, exact_bonus)
            )
            # 归一化至 0.00 ~ 1.00
            final_rerank_score = min(0.99, max(0.10, round(final_rerank_score, 4)))

            item = dict(c)
            item["rerank_score"] = final_rerank_score
            item["overlap_terms"] = matched_terms
            reranked.append(item)

        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)

        final_top = []
        for rank, item in enumerate(reranked[:top_n], start=1):
            item["final_rank"] = rank
            # 计算排名升降 (相比于向量检索的排名)
            prev_v = item.get("vector_rank")
            if prev_v is not None:
                item["rank_delta"] = prev_v - rank  # 正数表示排名上升
            else:
                item["rank_delta"] = 999  # 原来未入选
            final_top.append(item)

        return final_top

    def compare_all(
        self,
        query: str,
        top_k: int = 5,
        rrf_k: int = 60,
        filter_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """统一执行向量检索、BM25 全文检索与 RRF 混合检索并返回对比结构"""
        t0 = time.perf_counter()
        vec_res = self.vector_search(query=query, top_k=top_k, filter_source=filter_source)
        vec_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        t1 = time.perf_counter()
        bm25_res = self.bm25_search(query=query, top_k=top_k, filter_source=filter_source)
        bm25_ms = round((time.perf_counter() - t1) * 1000.0, 2)

        t2 = time.perf_counter()
        hybrid_raw = self.hybrid_search_rrf(
            query=query,
            top_k=max(top_k * 2, 10),
            rrf_k=rrf_k,
            filter_source=filter_source,
        )
        hybrid_reranked = self.rerank_cross_scoring(query=query, candidates=hybrid_raw, top_n=top_k)
        for item in hybrid_reranked:
            item["id"] = item["doc_id"]
            item["vec_rank"] = item.get("vector_rank")
        hybrid_ms = round((time.perf_counter() - t2) * 1000.0, 2)

        return {
            "query": query,
            "vector_only": {"latency_ms": vec_ms, "results": vec_res},
            "bm25_only": {"latency_ms": bm25_ms, "results": bm25_res},
            "hybrid": {"latency_ms": hybrid_ms, "results": hybrid_reranked},
        }

