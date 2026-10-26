import hashlib
import time
from typing import Any, Dict, List, Optional

try:
    from powercontext import ArtifactAddress, ArtifactRef, SourceRef
    HAS_POWERCONTEXT = True
except Exception:
    HAS_POWERCONTEXT = False


# 赛博侦探剧场内置案件设定与角色档案（配合 SeekDB dify_knowledge_base 混合检索）
CASE_DOSSIER = {
    "case_id": "CASE-2026-STARSEA",
    "title": "《星海迷局：深空算力中枢失窃案》",
    "synopsis": "2026 年深秋，轨道空间站「星海七号」的核心量子向量密钥在凌晨 02:15 被人从零重力机房盗走。三名嫌疑人各自声称拥有完美不在场证明，你需要通过多轮搜证、审讯与技术关键词检索揭穿谎言。",
    "suspects": {
        "lin_wei": {
            "name": "林维（首席向量架构师）",
            "alibi": "声称 02:00–02:30 在 B 区终端室执行 HNSW 索引重建，从未进入零重力机房。",
            "contradiction_trigger": ["HNSW", "快照", "02:15", "batch_knn", "日志", "门禁", "向量"],
            "secret_clue": "机房审计日志显示 02:14 有人使用林维的硬件令牌调用了 dbms_vector.batch_knn 导出核心参数，且 B 区终端室当时的任务为预设 Cron 定时触发而非人工值守。",
        },
        "su_qing": {
            "name": "苏晴（记忆中枢安全官）",
            "alibi": "声称事发时正在巡检 Scope 权限边界，通信器全程处于离线静默状态。",
            "contradiction_trigger": ["Scope", "权限", "通信", "静默", "PreparedContext", "信封", "记忆"],
            "secret_clue": "边缘网关抓包记录捕获到 02:16 苏晴的加密终端向外发送了一段带 BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1 协议头的分块增量包（ContextPackDelta）。",
        },
        "chen_mo": {
            "name": "陈默（具身运维工程师）",
            "alibi": "声称在维修双臂协作机器人，由于机械臂轨迹追踪率过低被安全门禁反锁在维修间。",
            "contradiction_trigger": ["机器人", "轨迹", "门禁", "维修", "全双工", "检索", "RRF"],
            "secret_clue": "质检系统显示陈默负责的机械臂在 02:10 已进入 GOLD 级自动校准状态，他利用无人巡检车通过通风管道绕过了主门禁传感器。",
        },
    },
}


class CyberDetectiveContextEngine:
    """
    结合 PowerContext 核心原语（Scope、Source、MemoryCitation、PreparedContext、Handoff）
    与 OceanBase SeekDB 混合检索结果的「星海迷局」多轮推演上下文管理器。
    """

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self.sessions:
            scope_id = f"scope:cyber-detective:{session_id}"
            self.sessions[session_id] = {
                "session_id": session_id,
                "scope_id": scope_id,
                "case_id": CASE_DOSSIER["case_id"],
                "title": CASE_DOSSIER["title"],
                "turn": 0,
                "trust_score": 60,
                "deduction_progress": 0,
                "sources": [],
                "memories": [],
                "unlocked_suspects": [],
                "contradictions_found": [],
                "last_prepared_context": "",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        return self.sessions[session_id]

    def process_turn(
        self,
        session_id: str,
        player_action: str,
        suspect_target: str,
        seekdb_hybrid_hits: List[Dict[str, Any]],
        max_bytes: int = 1600,
    ) -> Dict[str, Any]:
        state = self.get_or_create_session(session_id)
        state["turn"] += 1
        turn_no = state["turn"]

        # 1. Source Capture（将玩家本轮调查动作与 SeekDB 混合检索证据固化为不可变 CAPTURED Source）
        top_hit = seekdb_hybrid_hits[0] if seekdb_hybrid_hits else {
            "id": 0,
            "title": "深空机房基础日志",
            "content": "未命中高置信度技术卷宗，建议结合关键词与语义混合追问。",
            "rrf_score": 0.0,
        }
        raw_evidence_text = (
            f"[Turn-{turn_no}] 调查对象={suspect_target} | 玩家行动={player_action} | "
            f"SeekDB混合检索命中《{top_hit.get('title', '')}》(RRF={top_hit.get('rrf_score', 0):.4f}): "
            f"{top_hit.get('content', '')}"
        )
        source_sha256 = hashlib.sha256(raw_evidence_text.encode("utf-8")).hexdigest()
        source_id = f"src:{session_id}:turn-{turn_no}:{source_sha256[:10]}"

        source_record = {
            "source_id": source_id,
            "scope_id": state["scope_id"],
            "status": "CAPTURED",
            "sha256": source_sha256,
            "turn": turn_no,
            "raw_text": raw_evidence_text,
            "powercontext_ref": (
                str(SourceRef(source_type="investigation_log", source_id=source_id))
                if HAS_POWERCONTEXT
                else f"investigation_log:{source_id}"
            ),
        }
        state["sources"].append(source_record)

        # 2. 嫌犯不在场证明矛盾判定与 Atomic Memory 提炼
        suspect_info = CASE_DOSSIER["suspects"].get(
            suspect_target, CASE_DOSSIER["suspects"]["lin_wei"]
        )
        combined_probe = f"{player_action} {top_hit.get('title', '')} {top_hit.get('content', '')}"
        matched_triggers = [
            kw for kw in suspect_info["contradiction_trigger"] if kw.lower() in combined_probe.lower()
        ]

        contradiction_unlocked = len(matched_triggers) > 0 or turn_no >= 2
        if contradiction_unlocked:
            clue_text = (
                f"【证词矛盾突破·{suspect_info['name']}】原证词：{suspect_info['alibi']} "
                f"核心反证：{suspect_info['secret_clue']} "
                f"关联SeekDB卷宗：《{top_hit.get('title', '')}》"
            )
            if suspect_target not in state["unlocked_suspects"]:
                state["unlocked_suspects"].append(suspect_target)
            if suspect_info["name"] not in state["contradictions_found"]:
                state["contradictions_found"].append(suspect_info["name"])
            state["trust_score"] = min(100, state["trust_score"] + 15)
            state["deduction_progress"] = min(100, state["deduction_progress"] + 35)
        else:
            clue_text = (
                f"【常规问询记录·{suspect_info['name']}】维持原证词：{suspect_info['alibi']} "
                f"参考SeekDB背景卷宗：《{top_hit.get('title', '')}》"
            )
            state["deduction_progress"] = min(100, state["deduction_progress"] + 15)

        # 构造带精确字符区间 (start, end) 的 MemoryCitation
        snippet_end = min(len(clue_text), 140)
        entry_id = f"mem:{session_id}:entry-{turn_no}"
        memory_entry = {
            "entry_id": entry_id,
            "entry_version_id": f"{entry_id}@v1",
            "scope_id": state["scope_id"],
            "turn": turn_no,
            "suspect_target": suspect_target,
            "text": clue_text,
            "contradiction_unlocked": contradiction_unlocked,
            "citation": {
                "source_id": source_id,
                "projection_kind": "powercontext.text-evidence",
                "start": 0,
                "end": snippet_end,
                "quote": clue_text[0:snippet_end],
            },
        }
        state["memories"].append(memory_entry)

        # 3. 字节预算受限的 PreparedContext 装配 (Evidence Selection within UTF-8 max_bytes)
        prepared_context = self.assemble_prepared_context(
            state=state, query=player_action, max_bytes=max_bytes
        )
        state["last_prepared_context"] = prepared_context

        # 4. 生成赛博侦探主持人 (GM) 的剧情推演反馈与下一步建议
        gm_narration = self._build_gm_narration(
            turn_no=turn_no,
            suspect_info=suspect_info,
            contradiction_unlocked=contradiction_unlocked,
            top_hit=top_hit,
            deduction_progress=state["deduction_progress"],
        )

        return {
            "session_id": session_id,
            "scope_id": state["scope_id"],
            "turn": turn_no,
            "suspect_name": suspect_info["name"],
            "contradiction_unlocked": contradiction_unlocked,
            "matched_triggers": matched_triggers,
            "trust_score": state["trust_score"],
            "deduction_progress": state["deduction_progress"],
            "captured_source": source_record,
            "new_memory_entry": memory_entry,
            "seekdb_top_hit": {
                "id": top_hit.get("id"),
                "title": top_hit.get("title"),
                "rrf_score": top_hit.get("rrf_score", 0.0),
                "vec_rank": top_hit.get("vec_rank"),
                "bm25_rank": top_hit.get("bm25_rank"),
            },
            "prepared_context": prepared_context,
            "prepared_context_bytes": len(prepared_context.encode("utf-8")),
            "gm_narration": gm_narration,
            "ready_for_indictment": state["deduction_progress"] >= 70,
        }

    def assemble_prepared_context(
        self, state: Dict[str, Any], query: str, max_bytes: int = 1600
    ) -> str:
        """
        严格遵循 PowerContext PreparedContext V1 协议信封格式，
        在 UTF-8 字节预算 (max_bytes) 内动态筛选高价值线索与证据区间。
        """
        header = (
            "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
            f"scope_id: {state['scope_id']}\n"
            f"case_id: {state['case_id']}\n"
            f"query: {query}\n"
            f"progress: {state['deduction_progress']}%\n"
            "entries:\n"
        )
        footer = "END_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        budget_left = max_bytes - len(header.encode("utf-8")) - len(footer.encode("utf-8"))

        # 优先选择解锁了证词矛盾的记忆条目，其次按最新轮次排序
        sorted_memories = sorted(
            state["memories"],
            key=lambda m: (1 if m["contradiction_unlocked"] else 0, m["turn"]),
            reverse=True,
        )

        selected_lines: List[str] = []
        for mem in sorted_memories:
            cite = mem["citation"]
            line = (
                f"- [{mem['entry_id']}] {mem['text']} "
                f"(citation={cite['source_id']}[{cite['start']}:{cite['end']}])\n"
            )
            line_bytes = len(line.encode("utf-8"))
            if line_bytes <= budget_left:
                selected_lines.append(line)
                budget_left -= line_bytes
            else:
                # 截取证据子区间以严格满足 max_bytes 预算
                quote_short = cite["quote"][:48]
                short_line = (
                    f"- [{mem['entry_id']}] {quote_short}... "
                    f"(citation={cite['source_id']}[0:{len(quote_short)}])\n"
                )
                if len(short_line.encode("utf-8")) <= budget_left:
                    selected_lines.append(short_line)
                    budget_left -= len(short_line.encode("utf-8"))
                break

        return header + "".join(selected_lines) + footer

    def generate_case_handoff(self, session_id: str, accused_suspect: str = "lin_wei") -> Dict[str, Any]:
        """生成带完整证据链溯源 (ArtifactLineage + MemoryCitation) 的结案指控交接单 (Handoff)"""
        state = self.get_or_create_session(session_id)
        citations = [m["citation"] for m in state["memories"]]
        source_ids = [s["source_id"] for s in state["sources"]]

        artifact_address = (
            str(
                ArtifactAddress(
                    scope_id=state["scope_id"],
                    artifact=ArtifactRef(
                        family="handoff",
                        artifact_id=f"indictment-{session_id}",
                        revision=1,
                    ),
                )
            )
            if HAS_POWERCONTEXT
            else f"handoff/indictment-{session_id}@v1"
        )

        verdict_summary = (
            f"【星海七号结案陈词】经 {state['turn']} 轮 SeekDB 混合检索取证与 PowerContext 上下文比对，"
            f"锁定核心涉案链路：林维的硬件令牌在 02:14 触发向量参数批量导出，苏晴在 02:16 通过增量上下文包外发，"
            f"陈默利用校准状态的巡检通道提供物理掩护。共采信 {len(citations)} 条带字符偏移的铁证。"
        )

        return {
            "schema": "powercontext.handoff.v1",
            "artifact_address": artifact_address,
            "scope_id": state["scope_id"],
            "session_id": session_id,
            "accused_suspect": accused_suspect,
            "case_solved": len(state["contradictions_found"]) >= 1 or state["turn"] >= 2,
            "verdict_summary": verdict_summary,
            "contradictions_found": state["contradictions_found"],
            "lineage_sources": source_ids,
            "citations": citations,
        }

    def _build_gm_narration(
        self,
        turn_no: int,
        suspect_info: Dict[str, Any],
        contradiction_unlocked: bool,
        top_hit: Dict[str, Any],
        deduction_progress: int,
    ) -> str:
        if contradiction_unlocked:
            return (
                f"⚡【第 {turn_no} 轮·证词击穿】你调出 SeekDB 混合检索卷宗《{top_hit.get('title', '')}》"
                f"（RRF={top_hit.get('rrf_score', 0):.4f}），当面质问 {suspect_info['name']}！"
                f"对方神色骤变——{suspect_info['secret_clue']} 当前破案进度已推进至 {deduction_progress}%。"
            )
        return (
            f"🔍【第 {turn_no} 轮·取证推进】你结合《{top_hit.get('title', '')}》对 {suspect_info['name']} 展开盘问，"
            f"对方仍坚持原不在场证明（{suspect_info['alibi']}）。建议尝试使用其技术领域的专有关键词再次发起混合检索质询！"
        )
