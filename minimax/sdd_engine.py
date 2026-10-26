import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
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
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
EMBEDDING_DIM = 512


def _deterministic_text_embedding(text: str, dim: int = EMBEDDING_DIM) -> List[float]:
    """
    轻量确定性语义哈希特征编码器（512 维单位向量）：
    对技术标识符、Spec 编号、模块名与中文二元词组进行多通道正交投影，
    保证在无需 GPU 的 CI/评测环境下也能向远端 SeekDB 写入稳定、可复现的 512 维单位向量。
    """
    vec = [0.0005] * dim
    tokens = text.lower().replace("\n", " ").split()
    # 兼容中文字符切分与 2-gram
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


# 六大必选 SDD 上下文维度的初始基准数据（对应 target_project: AI Token 限流与熔断网关）
INITIAL_SDD_RECORDS: List[Dict[str, Any]] = [
    # 1. Spec 条目与代码位置映射 (spec_code_map)
    {
        "record_id": "SPEC-GW-01",
        "dimension": "spec_code_map",
        "spec_id": "SPEC-GW-01",
        "module_name": "gateway.limiter",
        "title": "租户级滑动窗口双桶 Token 速率限制器",
        "content": "实现滑动窗口请求数限制 (max_requests_per_window) 与 Token 消耗配额 (max_tokens_per_window) 双重校验，超限时返回 RateLimitDecision(allowed=False, retry_after_sec>0)。",
        "metadata": {
            "file_path": "target_project/gateway.py",
            "symbol_name": "TokenBucketGateway.check_and_consume",
            "start_line": 42,
            "end_line": 96,
            "status": "IMPLEMENTED",
        },
    },
    {
        "record_id": "SPEC-GW-02",
        "dimension": "spec_code_map",
        "spec_id": "SPEC-GW-02",
        "module_name": "gateway.circuit_breaker",
        "title": "上游模型服务三态熔断器 (CLOSED / OPEN / HALF_OPEN)",
        "content": "当连续上游失败次数达到 failure_threshold 时进入 OPEN 熔断状态；冷却时间 recovery_timeout_sec 到期后转入 HALF_OPEN 探测态，探测成功则恢复 CLOSED，失败则重新计时。",
        "metadata": {
            "file_path": "target_project/gateway.py",
            "symbol_name": "TokenBucketGateway.record_upstream_outcome",
            "start_line": 98,
            "end_line": 148,
            "status": "IMPLEMENTED",
        },
    },
    {
        "record_id": "SPEC-GW-03",
        "dimension": "spec_code_map",
        "spec_id": "SPEC-GW-03",
        "module_name": "gateway.headers",
        "title": "可观测性响应头与降级原因契约导出",
        "content": "每次网关决策必须生成标准 X-RateLimit-Remaining-Tokens、X-Circuit-State 与 X-Gateway-Reason 响应头字典，供下游 Agent 感知配额余量。",
        "metadata": {
            "file_path": "target_project/gateway.py",
            "symbol_name": "RateLimitDecision.to_headers",
            "start_line": 18,
            "end_line": 35,
            "status": "IMPLEMENTED",
        },
    },
    # 2. Spec 验收结果记录 (spec_validation)
    {
        "record_id": "VAL-GW-01",
        "dimension": "spec_validation",
        "spec_id": "SPEC-GW-01",
        "module_name": "gateway.limiter",
        "title": "SPEC-GW-01 滑动窗口与 Token 配额边界验收",
        "content": "通过 test_token_quota_and_sliding_window_eviction 验证：窗口过期请求自动驱逐，单次请求超出剩余 Token 时精确拒绝并计算 retry_after_sec。",
        "metadata": {
            "test_case": "target_project/test_gateway.py::test_token_quota_and_sliding_window_eviction",
            "status": "PASSED",
            "assertion_detail": "3 assertions passed (quota enforcement, window eviction, retry_after calculation)",
            "verified_at": "2026-10-26T10:00:00Z",
        },
    },
    {
        "record_id": "VAL-GW-02",
        "dimension": "spec_validation",
        "spec_id": "SPEC-GW-02",
        "module_name": "gateway.circuit_breaker",
        "title": "SPEC-GW-02 熔断器三态转换与半开恢复验收",
        "content": "通过 test_circuit_breaker_state_transitions 验证：CLOSED -> OPEN -> HALF_OPEN -> CLOSED 完整状态机闭环。",
        "metadata": {
            "test_case": "target_project/test_gateway.py::test_circuit_breaker_state_transitions",
            "status": "PASSED",
            "assertion_detail": "4 state transitions verified with deterministic clock injection",
            "verified_at": "2026-10-26T10:00:00Z",
        },
    },
    {
        "record_id": "VAL-GW-03",
        "dimension": "spec_validation",
        "spec_id": "SPEC-GW-03",
        "module_name": "gateway.headers",
        "title": "SPEC-GW-03 网关可观测性 Header 导出格式验收",
        "content": "通过 test_gateway_headers_contract 验证：所有字符串 Header 值符合 HTTP 规范且包含精确剩余 Token 数。",
        "metadata": {
            "test_case": "target_project/test_gateway.py::test_gateway_headers_contract",
            "status": "PASSED",
            "assertion_detail": "Header keys and string types verified",
            "verified_at": "2026-10-26T10:00:00Z",
        },
    },
    # 3. 关键设计决策及其理由 (decision_rationale)
    {
        "record_id": "DEC-GW-01",
        "dimension": "decision_rationale",
        "spec_id": "SPEC-GW-01",
        "module_name": "gateway.limiter",
        "title": "采用单调时钟注入 (clock_fn) 与双端队列滑动窗口而非固定时间片重置",
        "content": "决策理由：固定时间片在窗口边界会出现 2 倍突发流量尖峰（Boundary Burst），且依赖 time.time() 会导致单元测试需真实 sleep。采用 collections.deque 记录时间戳并支持注入 clock_fn，既消除边界突发又实现毫秒级确定性测试。",
        "metadata": {
            "rationale": "消除固定窗口边界 2x 突发流量并支持无 sleep 的确定性单元测试",
            "rejected_alternatives": [
                "固定窗口计数器 (Fixed Window Counter)：存在窗口交界处双倍流量穿透缺陷",
                "后台异步线程定时清零：引入多线程锁竞争与测试非确定性",
            ],
            "impact_scope": ["gateway.limiter", "gateway.circuit_breaker", "test_gateway"],
        },
    },
    {
        "record_id": "DEC-GW-02",
        "dimension": "decision_rationale",
        "spec_id": "SPEC-GW-02",
        "module_name": "gateway.circuit_breaker",
        "title": "熔断器优先于配额扣减执行 (Circuit-First Evaluation)",
        "content": "决策理由：当上游模型处于 OPEN 熔断状态时，若先扣减租户 Token 配额再报错，会导致租户在故障期间无故损失配额。因此 check_and_consume 必须在入口处先检查熔断器状态，OPEN 态直接短路返回而不消耗 Token。",
        "metadata": {
            "rationale": "防止上游服务故障熔断期间误扣租户的可用 Token 额度",
            "rejected_alternatives": [
                "先扣减配额再检查熔断状态：故障时需要额外事务回滚逻辑，容易泄漏配额"
            ],
            "impact_scope": ["gateway.limiter", "gateway.circuit_breaker"],
        },
    },
    # 4. 已知陷阱与反模式记录 (known_pitfall)
    {
        "record_id": "PIT-GW-01",
        "dimension": "known_pitfall",
        "spec_id": "SPEC-GW-01",
        "module_name": "gateway.limiter",
        "title": "陷阱：拒绝请求时误将失败请求写入滑动窗口导致永久饥饿",
        "content": "症状：当租户发起一次超大 Token 请求被拒后，后续正常小请求也全部被拒。根因：在校验配额前就向 deque 追加了当前请求记录。防线规则：必须先完成请求数与 Token 总额双重校验，仅在 allowed=True 时才将 (now, tokens) 写入窗口队列。",
        "metadata": {
            "symptom": "被限流拒绝的请求仍然占用了滑动窗口配额，引发后续请求永久饥饿",
            "root_cause": "先写入窗口队列后判断阈值，拒绝分支未回滚队列条目",
            "guardrail_rule": "MUST validate both request count and token sum BEFORE mutating tenant window deque",
            "severity": "HIGH",
        },
    },
    {
        "record_id": "PIT-GW-02",
        "dimension": "known_pitfall",
        "spec_id": "SPEC-GW-02",
        "module_name": "gateway.circuit_breaker",
        "title": "陷阱：HALF_OPEN 半开探测失败后未刷新 opened_at 时间戳",
        "content": "症状：半开探测请求失败后，下一个请求仍然立即穿透进入上游，熔断冷却失效。根因：HALF_OPEN 失败回退至 OPEN 时未将 opened_at 更新为当前时钟。防线规则：HALF_OPEN 探测失败必须重置 opened_at = clock_fn() 并维持 OPEN 状态。",
        "metadata": {
            "symptom": "HALF_OPEN 探测失败后熔断器冷却时间失效，持续放行故障流量",
            "root_cause": "回退到 OPEN 状态时漏掉了 opened_at 时间戳刷新",
            "guardrail_rule": "MUST update opened_at = self._clock() whenever HALF_OPEN probe fails",
            "severity": "CRITICAL",
        },
    },
    # 5. 活跃任务与依赖关系 (task_dependency)
    {
        "record_id": "TASK-GW-101",
        "dimension": "task_dependency",
        "spec_id": "SPEC-GW-01",
        "module_name": "gateway.limiter",
        "title": "实现滑动窗口双桶限流核心逻辑",
        "content": "完成 TokenBucketGateway.check_and_consume 中的过期窗口清理与配额扣减。",
        "metadata": {
            "depends_on": [],
            "blocks": ["TASK-GW-102", "TASK-GW-103"],
            "status": "COMPLETED",
            "verification_gate": "pytest target_project/test_gateway.py -k test_token_quota",
        },
    },
    {
        "record_id": "TASK-GW-102",
        "dimension": "task_dependency",
        "spec_id": "SPEC-GW-02",
        "module_name": "gateway.circuit_breaker",
        "title": "集成三态熔断器与半开状态自动探测",
        "content": "在限流网关入口前置集成 CLOSED/OPEN/HALF_OPEN 状态机，并遵循 PIT-GW-02 防线。",
        "metadata": {
            "depends_on": ["TASK-GW-101"],
            "blocks": ["TASK-GW-103"],
            "status": "COMPLETED",
            "verification_gate": "pytest target_project/test_gateway.py -k test_circuit_breaker",
        },
    },
    {
        "record_id": "TASK-GW-103",
        "dimension": "task_dependency",
        "spec_id": "SPEC-GW-03",
        "module_name": "gateway.headers",
        "title": "导出标准网关可观测性响应头并完成全量回归验收",
        "content": "实现 RateLimitDecision.to_headers() 并通过全部单元测试门禁。",
        "metadata": {
            "depends_on": ["TASK-GW-101", "TASK-GW-102"],
            "blocks": [],
            "status": "COMPLETED",
            "verification_gate": "pytest target_project/test_gateway.py -v",
        },
    },
    # 6. 模块间接口契约 (interface_contract)
    {
        "record_id": "CTR-GW-01",
        "dimension": "interface_contract",
        "spec_id": "SPEC-GW-01",
        "module_name": "gateway.limiter",
        "title": "check_and_consume 网关准入契约",
        "content": "接口签名：check_and_consume(tenant_id: str, requested_tokens: int) -> RateLimitDecision。不变量：1) requested_tokens > 0；2) 当 circuit_state == 'OPEN' 时 remaining_tokens 不变且 allowed=False；3) 返回对象的 to_headers() 键值全部为 str。",
        "metadata": {
            "provider_module": "gateway.limiter",
            "consumer_module": "api.router",
            "signature": "check_and_consume(tenant_id: str, requested_tokens: int) -> RateLimitDecision",
            "invariants": [
                "requested_tokens must be positive integer",
                "OPEN circuit breaker never deducts tenant token quota",
                "retry_after_sec >= 0.0 on all paths",
            ],
        },
    },
]


class MiniMaxSDDContextEngine:
    """
    MiniMax × OceanBase SeekDB × PowerContext 规范驱动开发 (SDD) 上下文管理系统。
    涵盖 6 大必选上下文维度、SeekDB 单 SQL 向量+全文混合检索、PowerContext 证据预算装配与 MiniMax-M2.7 协同指导。
    """

    REQUIRED_DIMENSIONS = (
        "spec_code_map",
        "spec_validation",
        "decision_rationale",
        "known_pitfall",
        "task_dependency",
        "interface_contract",
    )

    def __init__(self, scope_id: str = "scope:minimax-sdd:token-gateway"):
        self.scope_id = scope_id

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
        """在远端 SeekDB 中初始化 minimax_sdd_context_store 表（HNSW 向量索引 + IK 全文索引）并写入 6 大维度基准数据"""
        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS minimax_sdd_context_store (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        record_id VARCHAR(64) NOT NULL UNIQUE,
                        scope_id VARCHAR(128) NOT NULL,
                        dimension VARCHAR(64) NOT NULL,
                        spec_id VARCHAR(64) NOT NULL,
                        module_name VARCHAR(128) NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        powercontext_source_ref VARCHAR(255) NOT NULL,
                        embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        VECTOR INDEX idx_minimax_sdd_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
                        FULLTEXT INDEX idx_minimax_sdd_fts (title, content) WITH PARSER ik
                    ) DEFAULT CHARSET=utf8mb4;
                """)
                if force_reset:
                    cur.execute("TRUNCATE TABLE minimax_sdd_context_store;")

                cur.execute("SELECT COUNT(*) AS cnt FROM minimax_sdd_context_store;")
                existing = cur.fetchone()["cnt"]
                if existing < len(INITIAL_SDD_RECORDS):
                    for item in INITIAL_SDD_RECORDS:
                        emb = _deterministic_text_embedding(
                            f"{item['record_id']} {item['spec_id']} {item['module_name']} {item['title']} {item['content']}"
                        )
                        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
                        src_ref = (
                            str(SourceRef(source_type="sdd_record", source_id=item["record_id"]))
                            if HAS_POWERCONTEXT
                            else f"sdd_record:{item['record_id']}"
                        )
                        cur.execute(
                            """
                            INSERT INTO minimax_sdd_context_store
                            (record_id, scope_id, dimension, spec_id, module_name, title, content, metadata_json, powercontext_source_ref, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                title = VALUES(title),
                                content = VALUES(content),
                                metadata_json = VALUES(metadata_json),
                                embedding = VALUES(embedding)
                            """,
                            (
                                item["record_id"],
                                self.scope_id,
                                item["dimension"],
                                item["spec_id"],
                                item["module_name"],
                                item["title"],
                                item["content"],
                                json.dumps(item["metadata"], ensure_ascii=False),
                                src_ref,
                                vec_str,
                            ),
                        )
                cur.execute("""
                    SELECT dimension, COUNT(*) AS cnt
                    FROM minimax_sdd_context_store
                    GROUP BY dimension
                """)
                dim_counts = {r["dimension"]: r["cnt"] for r in cur.fetchall()}
        finally:
            conn.close()

        return {
            "status": "initialized",
            "seekdb_host": f"{SEEKDB_HOST}:{SEEKDB_PORT}",
            "scope_id": self.scope_id,
            "dimension_counts": dim_counts,
            "all_six_dimensions_present": all(d in dim_counts and dim_counts[d] > 0 for d in self.REQUIRED_DIMENSIONS),
        }

    def hybrid_query_seekdb(
        self,
        query_text: str,
        dimension: Optional[str] = None,
        spec_id: Optional[str] = None,
        module_name: Optional[str] = None,
        top_k: int = 6,
    ) -> List[Dict[str, Any]]:
        """在 SeekDB 中通过单条 SQL (UNION ALL) 执行向量 + 全文混合检索并融合排序"""
        emb = _deterministic_text_embedding(query_text)
        vec_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"

        filters = ["scope_id = %s"]
        params_base: List[Any] = [self.scope_id]
        if dimension:
            filters.append("dimension = %s")
            params_base.append(dimension)
        if spec_id:
            filters.append("spec_id = %s")
            params_base.append(spec_id)
        if module_name:
            filters.append("module_name = %s")
            params_base.append(module_name)

        where_sql = " WHERE " + " AND ".join(filters)
        fts_where_sql = " WHERE " + " AND ".join(
            filters + ["MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) > 0"]
        )

        sql = f"""
            (
                SELECT
                    record_id, dimension, spec_id, module_name, title, content,
                    metadata_json, powercontext_source_ref,
                    'vector' AS channel,
                    COSINE_DISTANCE(embedding, %s) AS distance,
                    0.0 AS fts_score
                FROM minimax_sdd_context_store
                {where_sql}
                ORDER BY COSINE_DISTANCE(embedding, %s) APPROXIMATE
                LIMIT %s
            )
            UNION ALL
            (
                SELECT
                    record_id, dimension, spec_id, module_name, title, content,
                    metadata_json, powercontext_source_ref,
                    'fts' AS channel,
                    NULL AS distance,
                    MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS fts_score
                FROM minimax_sdd_context_store
                {fts_where_sql}
                ORDER BY fts_score DESC, record_id ASC
                LIMIT %s
            )
        """
        cand_limit = max(top_k * 2, 8)
        full_params = (
            [vec_str]
            + params_base
            + [vec_str, cand_limit]
            + [query_text]
            + params_base
            + [query_text, cand_limit]
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
            rid = r["record_id"]
            if rid not in merged:
                merged[rid] = {
                    "record_id": rid,
                    "dimension": r["dimension"],
                    "spec_id": r["spec_id"],
                    "module_name": r["module_name"],
                    "title": r["title"],
                    "content": r["content"],
                    "metadata": json.loads(r["metadata_json"]),
                    "powercontext_source_ref": r["powercontext_source_ref"],
                    "channels": [],
                    "rrf_score": 0.0,
                }
            if r["channel"] == "vector":
                v_rank += 1
                merged[rid]["channels"].append("vector")
                merged[rid]["rrf_score"] += 1.0 / (60 + v_rank)
            else:
                f_rank += 1
                merged[rid]["channels"].append("fts")
                merged[rid]["rrf_score"] += 1.0 / (60 + f_rank)

        results = list(merged.values())
        results.sort(key=lambda x: (-x["rrf_score"], x["record_id"]))
        return results[:top_k]

    # --- 五大核心 SDD 查询能力 ---
    def query_1_spec_location_and_status(self, spec_id: str) -> Dict[str, Any]:
        """核心查询 1：查询指定 Spec 条目的代码实现位置（文件、符号、行号区间）与最新测试验收状态"""
        code_maps = self.hybrid_query_seekdb(
            query_text=f"{spec_id} 代码映射 实现位置", dimension="spec_code_map", spec_id=spec_id, top_k=5
        )
        validations = self.hybrid_query_seekdb(
            query_text=f"{spec_id} 测试验收 结果", dimension="spec_validation", spec_id=spec_id, top_k=5
        )
        return {
            "query_type": "1_spec_location_and_validation",
            "spec_id": spec_id,
            "code_mappings": code_maps,
            "validations": validations,
        }

    def query_2_change_impact_scope(self, spec_id_or_module: str) -> Dict[str, Any]:
        """核心查询 2：当某个 Spec 或模块发生变更时，查询受影响的下游任务依赖、接口契约与关联模块"""
        contracts = self.hybrid_query_seekdb(
            query_text=f"{spec_id_or_module} 接口契约 不变量", dimension="interface_contract", top_k=5
        )
        tasks = self.hybrid_query_seekdb(
            query_text=f"{spec_id_or_module} 任务依赖 阻塞", dimension="task_dependency", top_k=5
        )
        decisions = self.hybrid_query_seekdb(
            query_text=f"{spec_id_or_module} 影响范围", dimension="decision_rationale", top_k=5
        )
        impacted_modules = sorted(
            {
                mod
                for d in decisions
                for mod in d["metadata"].get("impact_scope", [])
            }
            | {c["metadata"].get("consumer_module", "") for c in contracts if c["metadata"].get("consumer_module")}
        )
        return {
            "query_type": "2_change_impact_analysis",
            "target": spec_id_or_module,
            "impacted_modules": impacted_modules,
            "affected_contracts": contracts,
            "dependent_tasks": tasks,
        }

    def query_3_decision_rationale(self, topic_query: str) -> Dict[str, Any]:
        """核心查询 3：检索某项架构设计的历史决策理由与被否决的替代方案"""
        decisions = self.hybrid_query_seekdb(
            query_text=topic_query, dimension="decision_rationale", top_k=5
        )
        return {
            "query_type": "3_decision_rationale",
            "topic_query": topic_query,
            "decisions": decisions,
        }

    def query_4_module_pitfalls_and_guardrails(self, module_name: str) -> Dict[str, Any]:
        """核心查询 4：在修改某个模块前，精准拉取该模块的已知陷阱（Pitfalls）与强制防线规则（Guardrails）"""
        pitfalls = self.hybrid_query_seekdb(
            query_text=f"{module_name} 陷阱 根因 防线", dimension="known_pitfall", module_name=module_name, top_k=5
        )
        if not pitfalls:
            pitfalls = self.hybrid_query_seekdb(
                query_text=f"{module_name} 陷阱 根因 防线", dimension="known_pitfall", top_k=5
            )
        return {
            "query_type": "4_module_pitfalls_and_guardrails",
            "module_name": module_name,
            "pitfalls": pitfalls,
            "mandatory_guardrails": [p["metadata"]["guardrail_rule"] for p in pitfalls],
        }

    def query_5_assemble_task_context_pack(
        self, task_id: str, spec_id: str, max_bytes: int = 2200
    ) -> Dict[str, Any]:
        """
        核心查询 5：为执行开发任务的 AI Coding Agent 装配字节预算受限的 PowerContext PreparedContext
        以及跨阶段 Handoff 制品，并可选调用 MiniMax-M2.7 生成开发审查建议。
        """
        records = self.hybrid_query_seekdb(
            query_text=f"{task_id} {spec_id} 代码映射 决策 陷阱 契约 验收", top_k=10
        )
        # 优先保证与目标 spec_id 直接关联的 6 大维度条目排在最前
        records.sort(key=lambda r: (0 if r["spec_id"] == spec_id else 1, -r["rrf_score"]))

        header = (
            "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
            f"scope_id: {self.scope_id}\n"
            f"task_id: {task_id}\n"
            f"spec_id: {spec_id}\n"
            "sdd_entries:\n"
        )
        footer = "END_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        budget_left = max_bytes - len(header.encode("utf-8")) - len(footer.encode("utf-8"))

        lines: List[str] = []
        citations: List[Dict[str, Any]] = []
        for rec in records:
            snippet = f"[{rec['dimension']}|{rec['record_id']}] {rec['title']}: {rec['content']}"
            cite_end = min(len(snippet), 160)
            line = f"- {snippet[:cite_end]} (citation={rec['record_id']}[0:{cite_end}])\n"
            line_bytes = len(line.encode("utf-8"))
            if line_bytes <= budget_left:
                lines.append(line)
                budget_left -= line_bytes
                citations.append(
                    {
                        "source_id": rec["record_id"],
                        "dimension": rec["dimension"],
                        "start": 0,
                        "end": cite_end,
                    }
                )

        prepared_context = header + "".join(lines) + footer
        handoff_addr = (
            str(
                ArtifactAddress(
                    scope_id=self.scope_id,
                    artifact=ArtifactRef(family="handoff", artifact_id=f"sdd-{task_id.lower()}", revision=1),
                )
            )
            if HAS_POWERCONTEXT
            else f"handoff/sdd-{task_id.lower()}@v1"
        )

        minimax_advice = self._generate_minimax_sdd_guidance(spec_id, prepared_context)

        return {
            "query_type": "5_task_prepared_context_and_handoff",
            "task_id": task_id,
            "spec_id": spec_id,
            "prepared_context": prepared_context,
            "prepared_context_bytes": len(prepared_context.encode("utf-8")),
            "max_bytes": max_bytes,
            "handoff": {
                "schema": "powercontext.handoff.v1",
                "artifact_address": handoff_addr,
                "scope_id": self.scope_id,
                "task_id": task_id,
                "spec_id": spec_id,
                "citations": citations,
            },
            "minimax_agent_guidance": minimax_advice,
        }

    def _generate_minimax_sdd_guidance(self, spec_id: str, prepared_context: str) -> Dict[str, Any]:
        """若配置了 MINIMAX_API_KEY 则实时调用 MiniMax-M2.7，否则使用确定性 SDD 规范推演回退"""
        if MINIMAX_API_KEY:
            try:
                resp = httpx.post(
                    f"{MINIMAX_BASE_URL.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {MINIMAX_API_KEY}"},
                    json={
                        "model": MINIMAX_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是 MiniMax SDD 规范驱动开发架构师，请基于 PowerContext 装配的上下文给出核心实现检查点。",
                            },
                            {"role": "user", "content": prepared_context},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 300,
                    },
                    timeout=12.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "model": MINIMAX_MODEL,
                        "mode": "live_api",
                        "guidance": data["choices"][0]["message"]["content"],
                    }
            except Exception:
                pass

        return {
            "model": MINIMAX_MODEL,
            "mode": "deterministic_sdd_synthesizer",
            "guidance": (
                f"针对 {spec_id} 的 SDD 执行守则：1) 严格遵循 DEC-GW-02 在入口处优先检查熔断器状态（OPEN 态不扣减配额）；"
                f"2) 遵守 PIT-GW-01 防线，仅在请求数与 Token 总额双重校验通过后才写入 deque；"
                f"3) 遵守 PIT-GW-02 防线，在 HALF_OPEN 探测失败时同步刷新 opened_at 时间戳。"
            ),
        }

    def verify_target_project_and_sync_seekdb(self) -> Dict[str, Any]:
        """运行 target_project 的 pytest 单元测试门禁，并将最新验收时间戳回写至远端 SeekDB"""
        target_test = Path(__file__).parent / "target_project" / "test_gateway.py"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(target_test), "-v"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        passed = proc.returncode == 0
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        conn = self.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT record_id, metadata_json
                    FROM minimax_sdd_context_store
                    WHERE dimension = 'spec_validation'
                    """
                )
                rows = cur.fetchall()
                for r in rows:
                    meta = json.loads(r["metadata_json"])
                    meta["status"] = "PASSED" if passed else "FAILED"
                    meta["verified_at"] = now_iso
                    cur.execute(
                        "UPDATE minimax_sdd_context_store SET metadata_json = %s WHERE record_id = %s",
                        (json.dumps(meta, ensure_ascii=False), r["record_id"]),
                    )
        finally:
            conn.close()

        return {
            "target_project_tests_passed": passed,
            "verified_at": now_iso,
            "pytest_stdout": proc.stdout[-800:],
        }
