// 校验浏览器/Function 侧引擎与 Python scholar_engine.py 的向量实现完全一致。
// 参考向量由 `python dev/embedding-reference.py` 生成。
import { readFileSync } from 'node:fs';
import { applyReview, parsePaper, scholarEmbedding, sha256Words } from '../functions/engine.mjs';

const reference = JSON.parse(readFileSync(new URL('./embedding-reference.json', import.meta.url), 'utf8'));

let failed = 0;
for (const sample of reference.samples) {
  const js = scholarEmbedding(sample.text);
  const py = sample.embedding;
  let maxDiff = 0;
  for (let i = 0; i < py.length; i++) maxDiff = Math.max(maxDiff, Math.abs(js[i] - py[i]));
  const ok = maxDiff < 1e-9;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  embedding parity  maxDiff=${maxDiff}  "${sample.text.slice(0, 40)}"`);
}

// SHA-256 已知向量自检
const empty = sha256Words(new Uint8Array());
const emptyHex = [...empty].map(w => w.toString(16).padStart(8, '0')).join('');
const abc = sha256Words(new TextEncoder().encode('abc'));
const abcHex = [...abc].map(w => w.toString(16).padStart(8, '0')).join('');
console.log(`${emptyHex === 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' ? 'PASS' : 'FAIL'}  sha256("")  ${emptyHex}`);
console.log(`${abcHex === 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' ? 'PASS' : 'FAIL'}  sha256("abc")  ${abcHex}`);
if (emptyHex !== 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855') failed++;
if (abcHex !== 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') failed++;

// SM-2 间隔调度：JS 与 Python 必须推进到同一状态
const NOW = 1_700_000_000;
let jsCards = {};
for (const step of reference.sm2_steps) {
  jsCards = applyReview(jsCards, 'PAPER-X', step.grade, NOW);
  const j = jsCards['PAPER-X'];
  const p = step.cards['PAPER-X'];
  const same =
    j.reps === p.reps &&
    j.lapses === p.lapses &&
    j.interval_days === p.interval_days &&
    Math.abs(j.ease - p.ease) < 1e-9 &&
    j.due_at === p.due_at;
  if (!same) failed++;
  console.log(`${same ? 'PASS' : 'FAIL'}  sm2 parity  grade=${step.grade}  reps=${j.reps} interval=${j.interval_days} ease=${j.ease} due_at=${j.due_at}`);
}

// 论文切分：章节数、标题与派生字段必须一致
const compareParse = (jsList, pyList, label) => {
  const same = jsList.length === pyList.length
    && jsList.every((s, i) => s.section === pyList[i].section
      && s.content === pyList[i].content
      && s.key_takeaway === pyList[i].key_takeaway
      && s.flashcard.question === pyList[i].flashcard.question
      && s.flashcard.answer === pyList[i].flashcard.answer
      && s.paper_title === pyList[i].paper_title);
  if (!same) failed++;
  console.log(`${same ? 'PASS' : 'FAIL'}  parse parity (${label})  sections=${jsList.length}`);
};
compareParse(parsePaper('# A Paper Title\n\n## First Section\nBody of the first section.\n\n## Second Section\nBody of the second section.', { topic: 'x' }), reference.parse_paper, 'markdown');
compareParse(parsePaper('First paragraph body.\n\nSecond paragraph body.\n\nThird paragraph body.', { topic: 'x' }), reference.parse_paper_plain, 'plain');

console.log(`\n${failed === 0 ? 'ALL PARITY CHECKS PASSED' : failed + ' CHECKS FAILED'}`);
process.exit(failed === 0 ? 0 : 1);
