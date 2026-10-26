from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel, Field

from sdd_engine import MiniMaxSDDContextEngine

app = FastAPI(
    title="MiniMax × OceanBase SeekDB × PowerContext SDD 上下文管理系统",
    description="2026 OceanBase AI 数据库大赛 - MiniMax 赛题交付系统 (OB-P:xI1C-CXPJXT4ztVpZDY6_A)",
    version="1.0.0",
)

engine = MiniMaxSDDContextEngine()


class SDDQueryRequest(BaseModel):
    query_type: int = Field(..., ge=1, le=5, description="核心查询编号 (1~5)")
    target: str = Field("SPEC-GW-02", description="目标 Spec ID / 模块名 / 决策问题")
    task_id: Optional[str] = Field("TASK-GW-102", description="任务 ID（用于查询 5）")
    max_bytes: int = Field(1800, ge=400, le=8000, description="PreparedContext 字节预算")


@app.get("/api/sdd/status")
def get_sdd_status():
    return engine.init_and_seed_seekdb()


@app.post("/api/sdd/query")
def execute_sdd_query(req: SDDQueryRequest):
    engine.init_and_seed_seekdb()
    if req.query_type == 1:
        return engine.query_1_spec_location_and_status(req.target)
    if req.query_type == 2:
        return engine.query_2_change_impact_scope(req.target)
    if req.query_type == 3:
        return engine.query_3_decision_rationale(req.target)
    if req.query_type == 4:
        return engine.query_4_module_pitfalls_and_guardrails(req.target)
    return engine.query_5_assemble_task_context_pack(
        task_id=req.task_id or "TASK-GW-102",
        spec_id=req.target,
        max_bytes=req.max_bytes,
    )


@app.post("/api/sdd/verify-target")
def verify_target_project():
    engine.init_and_seed_seekdb()
    return engine.verify_target_project_and_sync_seekdb()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8092)
