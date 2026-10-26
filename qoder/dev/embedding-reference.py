"""生成 JS 引擎对齐所需的 Python 参考数据（供 dev/embedding-parity.mjs 比对）。"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scholar_engine import _apply_review, _scholar_embedding, parse_paper

SAMPLES = [
    "RRF 混合检索",
    "SeekDB integrates the VSAG library to maintain a two-tier HNSW architecture",
    "PAPER-PC-02 PowerContext: Deterministic Evidence Selection under Strict Byte Budgets",
    "双层 HNSW（Delta + Snapshot）兼顾毫秒级实时写入可见性",
    "a",
]

SM2_NOW = 1_700_000_000
SM2_GRADES = [5, 4, 3, 0, 5]

cards = {}
sm2_steps = []
for grade in SM2_GRADES:
    cards = _apply_review(cards, "PAPER-X", grade, SM2_NOW)
    sm2_steps.append({"grade": grade, "cards": cards})

MARKDOWN = "# A Paper Title\n\n## First Section\nBody of the first section.\n\n## Second Section\nBody of the second section."
PLAIN = "First paragraph body.\n\nSecond paragraph body.\n\nThird paragraph body."

target = pathlib.Path(__file__).with_name("embedding-reference.json")
target.write_text(
    json.dumps(
        {
            "samples": [{"text": text, "embedding": _scholar_embedding(text)} for text in SAMPLES],
            "sm2_steps": sm2_steps,
            "parse_paper": parse_paper(MARKDOWN, "x"),
            "parse_paper_plain": parse_paper(PLAIN, "x"),
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"wrote {target}")
