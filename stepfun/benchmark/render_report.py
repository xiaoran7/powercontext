"""把 benchmark/results/summary.json 渲染进 REPORT.md 的实测表格区块。

用法：
    python benchmark/render_report.py

只改写 REPORT.md 中 `<!-- BENCHMARK_RESULTS_START -->` 与
`<!-- BENCHMARK_RESULTS_END -->` 之间的内容，其余文字保持不变，
保证报告里的数字始终与最近一次真实浏览器实测一致。
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SUMMARY = os.path.join(HERE, "results", "summary.json")
REPORT = os.path.join(os.path.dirname(HERE), "REPORT.md")
START = "<!-- BENCHMARK_RESULTS_START -->"
END = "<!-- BENCHMARK_RESULTS_END -->"


def fmt(x, digits=2):
    if x is None:
        return "-"
    return f"{x:.{digits}f}"


def main():
    if not os.path.exists(SUMMARY):
        raise SystemExit(f"未找到 {SUMMARY}，请先在浏览器打开 /bench 运行实测。")
    with open(SUMMARY, "r", encoding="utf-8") as f:
        data = json.load(f)
    runs = data.get("runs") or []
    if not runs:
        raise SystemExit("summary.json 中没有 runs。")

    baseline = next((r for r in runs if r.get("dtype") == "fp32"), None)
    base_size = baseline["model_size_mb"] if baseline else None

    lines = []
    lines.append(f"- **测试环境**：`{data.get('user_agent', '未知')}`；WebGPU 可用：{data.get('webgpu_available')}"
                 f"；SeekDB 本地实例（127.0.0.1:2881）；语料 {data.get('corpus_size')} 篇 / 查询 {data.get('query_count')} 条，Top-K = {data.get('top_k')}。")
    lines.append(f"- **运行时间**：{data.get('started_at')} → {data.get('finished_at')}（原始数据 `benchmark/results/{data.get('raw_file')}`）。")
    lines.append("")
    lines.append("| 配置 | 模型体积 (MiB) | 压缩比 | 冷加载 (ms) | 向量化吞吐 (docs/s) | seekdb 检索 P50 (ms) | 端到端 P50 (ms) | Recall@5 | nDCG@5 |")
    lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in runs:
        size = r.get("model_size_mb")
        ratio = f"{base_size / size:.2f}x" if base_size and size else "-"
        lines.append(
            "| {label} | {size} | {ratio} | {cold} | {tp} | {sdb} | {e2e} | {recall} | {ndcg} |".format(
                label=r.get("label"),
                size=fmt(size),
                ratio=ratio,
                cold=fmt(r.get("cold_load_ms"), 0),
                tp=fmt(r.get("throughput_docs_per_sec"), 1),
                sdb=fmt(r.get("seekdb_p50_ms")),
                e2e=fmt(r.get("e2e_p50_ms")),
                recall=fmt(r.get("recall_at_5"), 4),
                ndcg=fmt(r.get("ndcg_at_5"), 4),
            )
        )
    lines.append("")
    lines.append("- **口径**：模型体积按 ONNX 权重实测字节数（MiB = 1024²）；压缩比以 FP32 为基线；"
                 "冷加载包含权重下载/读取与执行图初始化（可用浏览器缓存，首次运行会明显更慢）；"
                 "吞吐为 48 篇文档连续推理的均值；seekdb 检索为服务端单条混合 SQL 往返 P50；"
                 "端到端 = 查询向量化 + 检索。每组配置在计时前先预热 3 次推理，"
                 "避免 WebGPU 首次 shader 编译污染延迟数据。")
    block = "\n".join(lines)

    with open(REPORT, "r", encoding="utf-8") as f:
        text = f.read()
    if START not in text or END not in text:
        raise SystemExit("REPORT.md 中缺少 BENCHMARK_RESULTS 标记区块。")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new_text = f"{head}{START}\n{block}\n{END}{tail}"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"已更新 REPORT.md 实测区块：{len(runs)} 组配置")


if __name__ == "__main__":
    main()
