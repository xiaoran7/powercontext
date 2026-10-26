import argparse
import json
from sdd_engine import MiniMaxSDDContextEngine


def main():
    parser = argparse.ArgumentParser(
        description="MiniMax × SeekDB × PowerContext SDD 上下文管理系统一键 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化远端 SeekDB 六大 SDD 上下文维度表与基准数据")
    sub.add_parser("demo", help="一键执行完整 SDD 闭环演示（初始化 + 五大核心查询 + 目标项目验收回写）")

    q_parser = sub.add_parser("query", help="执行五大核心 SDD 查询之一")
    q_parser.add_argument(
        "--type",
        choices=["1", "2", "3", "4", "5"],
        required=True,
        help="1: Spec代码映射与状态 | 2: 变更影响范围 | 3: 设计决策理由 | 4: 模块已知陷阱 | 5: 装配任务PreparedContext",
    )
    q_parser.add_argument("--target", default="SPEC-GW-02", help="查询目标 Spec ID、模块名或问题")
    q_parser.add_argument("--task-id", default="TASK-GW-102", help="用于查询 5 的任务 ID")
    q_parser.add_argument("--max-bytes", type=int, default=1800, help="PreparedContext UTF-8 字节预算")

    args = parser.parse_args()
    engine = MiniMaxSDDContextEngine()

    if args.command == "init":
        res = engine.init_and_seed_seekdb()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "query":
        engine.init_and_seed_seekdb()
        if args.type == "1":
            out = engine.query_1_spec_location_and_status(args.target)
        elif args.type == "2":
            out = engine.query_2_change_impact_scope(args.target)
        elif args.type == "3":
            out = engine.query_3_decision_rationale(args.target)
        elif args.type == "4":
            out = engine.query_4_module_pitfalls_and_guardrails(args.target)
        else:
            out = engine.query_5_assemble_task_context_pack(
                task_id=args.task_id, spec_id=args.target, max_bytes=args.max_bytes
            )
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.command == "demo":
        init_res = engine.init_and_seed_seekdb()
        q1 = engine.query_1_spec_location_and_status("SPEC-GW-02")
        q2 = engine.query_2_change_impact_scope("SPEC-GW-01")
        q3 = engine.query_3_decision_rationale("为什么要先检查熔断器状态再扣减配额？")
        q4 = engine.query_4_module_pitfalls_and_guardrails("gateway.circuit_breaker")
        q5 = engine.query_5_assemble_task_context_pack("TASK-GW-102", "SPEC-GW-02", max_bytes=1800)
        verify_res = engine.verify_target_project_and_sync_seekdb()
        summary = {
            "init": init_res,
            "query_1_spec_location": q1,
            "query_2_impact_scope": q2,
            "query_3_decision_rationale": q3,
            "query_4_pitfalls": q4,
            "query_5_prepared_context": q5,
            "target_project_verification": verify_res,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
