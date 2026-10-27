import argparse
import json
import sys
from sdd_engine import MiniMaxSDDContextEngine

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="MiniMax × SeekDB × PowerContext SDD 上下文管理系统一键 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="初始化或重置 SeekDB 六大 SDD 上下文维度表与基准数据")
    init_parser.add_argument("--reset", action="store_true", help="清空并恢复初始状态")

    sub.add_parser("demo", help="一键执行完整 SDD 闭环演示（初始化 + 五大核心查询 + 状态复用 + 目标项目验收回写）")

    q_parser = sub.add_parser("query", help="执行五大核心 SDD 查询之一")
    q_parser.add_argument(
        "--type",
        choices=["1", "2", "3", "4", "5"],
        required=True,
        help="1: Spec代码映射与状态 | 2: 变更影响范围 | 3: 设计决策理由 | 4: 模块已知陷阱 | 5: 装配任务PreparedContext",
    )
    q_parser.add_argument("--target", default="SPEC-GW-02", help="查询目标 Spec ID、模块名或问题")
    q_parser.add_argument("--task-id", default="TASK-GW-102", help="用于查询 5 的任务 ID")
    q_parser.add_argument("--max-bytes", type=int, default=2400, help="PreparedContext UTF-8 字节预算")

    apply_parser = sub.add_parser("apply", help="将装配好的 SDD 上下文提供给 MiniMax 开发助手完成代码修改与测试验收")
    apply_parser.add_argument("--task-id", default="TASK-GW-102", help="任务 ID")
    apply_parser.add_argument("--target", default="SPEC-GW-02", help="目标 Spec ID")
    apply_parser.add_argument("--no-test", action="store_true", help="跳过自动单元测试门禁")

    args = parser.parse_args()
    engine = MiniMaxSDDContextEngine()

    if args.command == "init":
        res = engine.init_and_seed_seekdb(force_reset=args.reset)
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
    elif args.command == "apply":
        engine.init_and_seed_seekdb()
        out = engine.apply_minimax_code_patch(
            task_id=args.task_id,
            spec_id=args.target,
            auto_test=not args.no_test,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.command == "demo":
        init_res = engine.init_and_seed_seekdb(force_reset=True)
        q1 = engine.query_1_spec_location_and_status("SPEC-GW-02")
        q2 = engine.query_2_change_impact_scope("SPEC-GW-01")
        q3 = engine.query_3_decision_rationale("为什么要先检查熔断器状态再扣减配额？")
        q4 = engine.query_4_module_pitfalls_and_guardrails("gateway.circuit_breaker")
        engine.save_task_state(
            task_id="TASK-GW-102",
            spec_id="SPEC-GW-02",
            completed_items=["SPEC-GW-01 滑动窗口双桶限流"],
            todo_items=["SPEC-GW-02 三态熔断器落地", "SPEC-GW-03 响应头验证"],
            verification_summary="before snapshot: 1 passed, 3 failed",
        )
        restarted_engine = MiniMaxSDDContextEngine()
        reused_state = restarted_engine.load_task_state("TASK-GW-102")
        q5 = restarted_engine.query_5_assemble_task_context_pack("TASK-GW-102", "SPEC-GW-02", max_bytes=2400)
        apply_res = restarted_engine.apply_minimax_code_patch("TASK-GW-102", "SPEC-GW-02", auto_test=True)
        summary = {
            "init": init_res,
            "query_1_spec_location": q1,
            "query_2_impact_scope": q2,
            "query_3_decision_rationale": q3,
            "query_4_pitfalls": q4,
            "reused_task_state_after_restart": reused_state,
            "query_5_prepared_context": q5,
            "minimax_apply_code_modification": apply_res,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
