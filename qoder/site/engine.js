// ScholarPulse 检索与装配引擎（浏览器与 Qoder Sites Edge Function 共用）。
// 与 scholar_engine.py 保持同一套算法：512 维哈希向量余弦距离 + 词法 BM25 双通道，
// 经 RRF(k=60) 融合后，在严格 UTF-8 字节预算内装配 PreparedContext V1 信封。
// 语料来自 corpus.json（唯一数据源），本模块不内嵌任何示例数据。

export const SCOPE_ID = 'scope:qoder:scholar-pulse';
export const EMBEDDING_DIM = 512;
export const RRF_K = 60;

const DEPTH_SPAN_CHARS = { 'deep-derivation': 150, standard: 110, brief: 80 };

const TOPIC_ALIASES = {
  // 现有与经典别名兼容
  rrf: 'rag_context',
  'reciprocal rank fusion': 'rag_context',
  hybrid_rag: 'rag_context',
  vector_index: 'vector_retrieval',
  hnsw: 'vector_retrieval',
  vsag: 'vector_retrieval',
  'delta hnsw': 'vector_retrieval',
  'snapshot hnsw': 'vector_retrieval',
  'byte budget': 'rag_context',
  'evidence selection': 'rag_context',
  'prepared context': 'rag_context',
  context_engineering: 'context_engineering',
  // 五大学术领域完整别名映射
  distributed_database: 'distributed_database',
  paxos: 'distributed_database',
  raft: 'distributed_database',
  spanner: 'distributed_database',
  oceanbase: 'distributed_database',
  lsm: 'distributed_database',
  consensus: 'distributed_database',
  vector_retrieval: 'vector_retrieval',
  faiss: 'vector_retrieval',
  ivf: 'vector_retrieval',
  pq: 'vector_retrieval',
  simd: 'vector_retrieval',
  ann: 'vector_retrieval',
  llm_reasoning: 'llm_reasoning',
  attention: 'llm_reasoning',
  transformer: 'llm_reasoning',
  flashattention: 'llm_reasoning',
  tot: 'llm_reasoning',
  'tree of thoughts': 'llm_reasoning',
  rag_context: 'rag_context',
  dpr: 'rag_context',
  hybrid: 'rag_context',
  memory_cognition: 'memory_cognition',
  sm2: 'memory_cognition',
  anki: 'memory_cognition',
  'spaced repetition': 'memory_cognition',
  'forgetting curve': 'memory_cognition',
};

export const REVIEW_GRADES = [
  { grade: 0, key: 'forgot', label: '忘记' },
  { grade: 3, key: 'hard', label: '困难' },
  { grade: 4, key: 'good', label: '良好' },
  { grade: 5, key: 'easy', label: '掌握' },
];

export function normalizeTopic(value) {
  return String(value ?? '').trim().toLowerCase().replace(/[\s\-/]+/g, '_').replace(/^_+|_+$/g, '');
}

export function normalizeTopics(entries) {
  const out = [];
  for (const entry of entries ?? []) {
    if (typeof entry !== 'string') continue;
    const norm = normalizeTopic(entry);
    if (norm && !out.includes(norm)) out.push(norm);
  }
  return out;
}

export function normalizeDocIds(entries) {
  const out = [];
  for (const entry of entries ?? []) {
    if (typeof entry !== 'string') continue;
    const norm = entry.trim();
    if (norm && !out.includes(norm)) out.push(norm);
  }
  return out;
}

export function mergeMemory(stored, supplied) {
  const left = stored ?? {};
  const right = supplied ?? {};
  return {
    weak_topics: normalizeTopics([...(left.weak_topics ?? []), ...(right.weak_topics ?? [])]),
    mastered_doc_ids: normalizeDocIds([...(left.mastered_doc_ids ?? []), ...(right.mastered_doc_ids ?? [])]),
    preferred_depth: right.preferred_depth || left.preferred_depth || 'deep-derivation',
    review_note: right.review_note || left.review_note || '',
    cards: { ...(left.cards ?? {}), ...(right.cards ?? {}) },
  };
}

// 由语料推导主题清单，避免在界面层写死选项。
export function corpusTopics(corpus) {
  const counts = new Map();
  for (const item of corpus ?? []) {
    const topic = normalizeTopic(item.topic);
    if (!topic) continue;
    counts.set(topic, (counts.get(topic) || 0) + 1);
  }
  return [...counts.entries()].map(([topic, count]) => ({ topic, count }));
}

// 语料按论文聚合，供文献库面板展示。
export function groupByPaper(corpus) {
  const papers = new Map();
  for (const item of corpus ?? []) {
    const key = item.paper_title || item.doc_id;
    if (!papers.has(key)) {
      papers.set(key, {
        paper_title: key,
        topics: new Set(),
        sections: [],
        doc_ids: [],
      });
    }
    const paper = papers.get(key);
    paper.topics.add(normalizeTopic(item.topic));
    paper.sections.push({ doc_id: item.doc_id, section: item.section });
    paper.doc_ids.push(item.doc_id);
  }
  return [...papers.values()]
    .map(paper => ({
      paper_title: paper.paper_title,
      topics: [...paper.topics],
      sections: paper.sections,
      doc_ids: paper.doc_ids,
      section_count: paper.sections.length,
    }))
    .sort((a, b) => b.section_count - a.section_count || a.paper_title.localeCompare(b.paper_title));
}

/* ---------------- 向量与词法通道 ---------------- */

// 内置纯 JS SHA-256，保证浏览器与 Deno 运行时产出完全一致的向量。
const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotr(value, bits) {
  return (value >>> bits) | (value << (32 - bits));
}

export function sha256Words(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  const state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const bitLength = bytes.length * 8;
  const paddedLength = (((bytes.length + 8) >> 6) + 1) << 6;
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 4, bitLength >>> 0, false);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 4294967296), false);

  const w = new Uint32Array(64);
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const x = w[i - 15];
      const y = w[i - 2];
      const s0 = rotr(x, 7) ^ rotr(x, 18) ^ (x >>> 3);
      const s1 = rotr(y, 17) ^ rotr(y, 19) ^ (y >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = state[0], b = state[1], c = state[2], d = state[3];
    let e = state[4], f = state[5], g = state[6], h = state[7];
    for (let i = 0; i < 64; i++) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + ch + SHA256_K[i] + w[i]) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    state[0] = (state[0] + a) >>> 0;
    state[1] = (state[1] + b) >>> 0;
    state[2] = (state[2] + c) >>> 0;
    state[3] = (state[3] + d) >>> 0;
    state[4] = (state[4] + e) >>> 0;
    state[5] = (state[5] + f) >>> 0;
    state[6] = (state[6] + g) >>> 0;
    state[7] = (state[7] + h) >>> 0;
  }
  return state;
}

// 与 Python 端 _scholar_embedding 同构：SHA-256 双哈希投影 + 符号位 + L2 归一化。
export function scholarEmbedding(text, dim = EMBEDDING_DIM) {
  const vec = new Float64Array(dim).fill(0.0005);
  const lower = String(text ?? '').toLowerCase();
  const tokens = lower.replace(/\n/g, ' ').split(/\s+/).filter(Boolean);
  const clean = Array.from(lower).filter(ch => !/\s/.test(ch)).join('');
  const ngrams = [];
  for (let i = 0; i + 1 < clean.length; i++) ngrams.push(clean.slice(i, i + 2));
  const encoder = new TextEncoder();
  for (const token of tokens.concat(ngrams)) {
    const state = sha256Words(encoder.encode(token));
    const idx1 = (state[0] >>> 16) % dim;
    const idx2 = (state[0] & 0xffff) % dim;
    const sign = (state[1] >>> 24) % 2 === 0 ? 1 : -1;
    vec[idx1] += 1;
    vec[idx2] += 0.5 * sign;
  }
  let norm = 0;
  for (const value of vec) norm += value * value;
  norm = Math.sqrt(norm) || 1;
  const out = new Array(dim);
  for (let i = 0; i < dim; i++) out[i] = Math.round((vec[i] / norm) * 1e6) / 1e6;
  return out;
}

export function embeddingForDocument(doc) {
  return `${doc.doc_id} ${doc.paper_title} ${doc.section} ${doc.content} ${doc.key_takeaway}`;
}

export function cosineDistance(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return 1;
  const length = Math.min(a.length, b.length);
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  if (!normA || !normB) return 1;
  return 1 - dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

// 双语分词：ASCII 词元 + CJK 字符二元组，与向量侧使用同一套切分，保证两通道可比。
export function tokenize(text) {
  const lower = String(text ?? '').toLowerCase();
  const words = lower.replace(/\n/g, ' ').split(/\s+/).filter(Boolean);
  const cjk = lower.replace(/[^一-鿿]/g, '');
  const bigrams = [];
  for (let i = 0; i + 1 < cjk.length; i++) bigrams.push(cjk.slice(i, i + 2));
  return words.concat(bigrams);
}

export function bm25Scores(query, documents) {
  const queryTerms = tokenize(query);
  const tokenized = documents.map(doc => tokenize(`${doc.paper_title} ${doc.section} ${doc.content} ${doc.key_takeaway}`));
  const avgLength = tokenized.reduce((sum, tokens) => sum + tokens.length, 0) / (tokenized.length || 1) || 1;
  const k1 = 1.2;
  const b = 0.75;
  return documents.map((doc, index) => {
    const tokens = tokenized[index];
    const counts = new Map();
    for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
    let score = 0;
    for (const term of new Set(queryTerms)) {
      const freq = counts.get(term) ?? 0;
      if (!freq) continue;
      const containing = tokenized.reduce((sum, other) => sum + (other.includes(term) ? 1 : 0), 0);
      const idf = Math.log(1 + (documents.length - containing + 0.5) / (containing + 0.5));
      score += idf * ((freq * (k1 + 1)) / (freq + k1 * (1 - b + (b * tokens.length) / avgLength)));
    }
    return score;
  });
}

// 与 SeekDB 侧一致的单条 UNION ALL + RRF 融合语义。
export function fuseChannels(documents, queryEmbedding, lexicalScores, topK, scopeDocIds = null) {
  const scoped = scopeDocIds && scopeDocIds.length
    ? documents.filter(doc => scopeDocIds.includes(doc.doc_id))
    : documents;
  const pool = scoped.length ? scoped : documents;
  const poolIndices = pool.map(doc => documents.indexOf(doc));
  const candidateLimit = Math.max(topK * 2, 8);
  const vectorRanked = pool
    .map((doc, index) => ({ doc, distance: cosineDistance(queryEmbedding, doc.embedding) }))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, candidateLimit);
  const lexicalRanked = pool
    .map((doc, index) => ({ doc, index: poolIndices[index], score: lexicalScores[poolIndices[index]] ?? 0 }))
    .sort((a, b) => b.score - a.score || (a.doc.doc_id < b.doc.doc_id ? -1 : 1))
    .slice(0, candidateLimit);

  const merged = new Map();
  vectorRanked.forEach((entry, position) => {
    const id = entry.doc.doc_id;
    if (!merged.has(id)) merged.set(id, { ...entry.doc, channels: [], rrf_score: 0 });
    const record = merged.get(id);
    if (!record.channels.includes('vector')) record.channels.push('vector');
    record.rrf_score += 1 / (RRF_K + position + 1);
  });
  lexicalRanked.forEach((entry, position) => {
    const id = entry.doc.doc_id;
    if (!merged.has(id)) merged.set(id, { ...entry.doc, channels: [], rrf_score: 0 });
    const record = merged.get(id);
    if (!record.channels.includes('fts')) record.channels.push('fts');
    record.rrf_score += 1 / (RRF_K + position + 1);
  });

  const results = [...merged.values()];
  for (const item of results) item.rrf_score = Math.round(item.rrf_score * 1e6) / 1e6;
  results.sort((a, b) => b.rrf_score - a.rrf_score || (a.doc_id < b.doc_id ? -1 : 1));
  return results.slice(0, topK);
}

/* ---------------- 间隔重复调度（SM-2 变体） ---------------- */

export function applyReview(cardState, docId, grade, nowSeconds) {
  const current = cardState?.[docId] ?? { ease: 2.5, interval_days: 0, due_at: 0, reps: 0, lapses: 0 };
  const next = { ...current };
  if (grade < 3) {
    next.reps = 0;
    next.lapses = (current.lapses || 0) + 1;
    next.interval_days = 1;
    next.ease = Math.max(1.3, Number(((current.ease || 2.5) - 0.2).toFixed(2)));
  } else {
    const reps = current.reps || 0;
    if (reps === 0) next.interval_days = 1;
    else if (reps === 1) next.interval_days = 3;
    else next.interval_days = Math.max(1, Math.round((current.interval_days || 1) * (current.ease || 2.5)));
    next.reps = reps + 1;
    const delta = 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02);
    next.ease = Math.min(2.8, Math.max(1.3, Number(((current.ease || 2.5) + delta).toFixed(2))));
  }
  next.due_at = grade < 3 ? nowSeconds : nowSeconds + next.interval_days * 86400;
  next.last_grade = grade;
  next.last_reviewed_at = nowSeconds;
  return { ...(cardState ?? {}), [docId]: next };
}

function priorityInterval(baseInterval, ease, priority) {
  if (priority === 'high_weak_topic') return Math.max(1, Math.round(baseInterval * 0.5));
  if (priority === 'low_mastered') return Math.max(1, Math.round(baseInterval * ease * 2));
  return Math.max(1, Math.round(baseInterval * ease));
}

export function weakHitIds(hits, weakTopics) {
  const entries = normalizeTopics(weakTopics);
  if (!entries.length) return new Set();
  const weakIds = new Set();
  for (const hit of hits) {
    const docTopic = normalizeTopic(hit.topic);
    let matched = false;
    for (const entry of entries) {
      const candidates = new Set([entry]);
      const alias = TOPIC_ALIASES[entry] ?? TOPIC_ALIASES[entry.replace(/_/g, ' ')];
      if (alias) candidates.add(alias);
      for (const candidate of candidates) {
        if (candidate && (candidate === docTopic || candidate.includes(docTopic) || docTopic.includes(candidate))) {
          matched = true;
          break;
        }
      }
      if (matched) break;
    }
    if (!matched) {
      const haystack = `${hit.topic} ${hit.key_takeaway} ${hit.content}`.toLowerCase();
      for (const entry of entries) {
        if (entry.length >= 2 && haystack.includes(entry.replace(/_/g, ' '))) {
          matched = true;
          break;
        }
      }
    }
    if (matched) weakIds.add(hit.doc_id);
  }
  return weakIds;
}

export function generateResearchSynthesis(researchQuestion, hits, citations = []) {
  if (!hits || !hits.length) {
    return {
      summary: '未检索到与该问题高置信度相关的文献，建议调整关键词或引入新论文。',
      analysis_paragraphs: [],
      trade_offs: [],
      open_questions: ['如何扩展文献库以覆盖该研究领域？'],
    };
  }

  const topHit = hits[0];
  const summary = `围绕「${researchQuestion}」，结合检索召回的 ${hits.length} 篇权威文献，核心结论表明：${topHit.key_takeaway || ''}`;

  const paragraphs = [];
  for (let idx = 0; idx < Math.min(hits.length, 4); idx++) {
    const h = hits[idx];
    const citeBadge = `[${h.doc_id}:§${h.section}]`;
    const para = `**${idx + 1}. 《${h.paper_title}》 (${h.section})**：${h.content} 其核心论据为：${h.key_takeaway} ${citeBadge}`;
    paragraphs.append ? paragraphs.append(para) : paragraphs.push(para);
  }

  const tradeOffs = [];
  const topics = new Set(hits.map(h => h.topic || ''));
  if ([...topics].some(t => t.includes('database'))) {
    tradeOffs.push('一致性 vs 吞吐量：通过分区 Paxos / TrueTime 约束外部一致性，但跨区事务需承担两阶段提交或 Commit Wait 延迟。');
  }
  if ([...topics].some(t => t.includes('vector'))) {
    tradeOffs.push('检索精度 vs 内存开销：双层 HNSW 或 IVF-PQ 在高维空间利用量化和图分层压缩 90% 显存，但在极高维稀疏特征下召回率存在长尾衰减。');
  }
  if ([...topics].some(t => t.includes('llm'))) {
    tradeOffs.push('计算复杂度 vs 显存带宽：FlashAttention 利用片上 SRAM 分块避免 HBM 频繁访存，显著提升 IO-Bound 算子算力利用率。');
  }
  if ([...topics].some(t => t.includes('rag') || t.includes('context'))) {
    tradeOffs.push('全量上下文 vs 严格预算：PowerContext 通过字符级证据抽取在受限窗口保留高信噪比论据，避免长文本带来的模型注意力分散。');
  }
  if (!tradeOffs.length) {
    tradeOffs.push('语义召回 vs 精确匹配：RRF (k=60) 融合向量余弦距离与词法 BM25，有效平衡冷启动术语缺失与复杂长查询语义泛化。');
  }

  const openQuestions = [
    `如何在严格 UTF-8 字节预算内进一步压缩《${topHit.paper_title}》的证明与公式推导？`,
    '多轮 Agent 在跨会话长程研究中，如何协同复用学习者薄弱点记忆与增量 ContextPackDelta？',
  ];

  return {
    summary,
    analysis_paragraphs: paragraphs,
    trade_offs: tradeOffs,
    open_questions: openQuestions,
  };
}

export function buildBrief({ researchQuestion, maxBytes, learnerId, profile, hits, cardState = {}, nowSeconds = 0 }) {
  const depth = profile.preferred_depth;
  const spanChars = DEPTH_SPAN_CHARS[depth] ?? DEPTH_SPAN_CHARS['deep-derivation'];
  const mastered = new Set(profile.mastered_doc_ids);
  const weakIds = weakHitIds(hits, profile.weak_topics);

  const memLine = profile.weak_topics.length || profile.review_note
    ? `learner_memory: id=${learnerId} weak_topics=${profile.weak_topics.join(',') || 'none'} depth=${depth}\n`
    : '';
  const header =
    'BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n' +
    `scope_id: ${SCOPE_ID}\n` +
    `research_question: ${researchQuestion}\n` +
    memLine +
    'evidence_spans:\n';
  const footer = 'END_POWERCONTEXT_PREPARED_CONTEXT_V1\n';
  const encoder = new TextEncoder();
  let budgetLeft = maxBytes - encoder.encode(header).length - encoder.encode(footer).length;

  const lines = [];
  const citations = [];
  const flashcards = [];
  for (const hit of hits) {
    const body = `${hit.key_takeaway} | ${hit.content}`;
    const endPos = Math.min(body.length, spanChars);
    const line = `- [${hit.doc_id}] ${body.slice(0, endPos)} (citation=${hit.doc_id}[0:${endPos}])\n`;
    const lineBytes = encoder.encode(line).length;
    if (lineBytes <= budgetLeft) {
      lines.push(line);
      budgetLeft -= lineBytes;
      citations.push({
        source_id: hit.doc_id,
        paper_title: hit.paper_title,
        section: hit.section,
        start: 0,
        end: endPos,
      });
    }
    const isWeak = weakIds.has(hit.doc_id);
    const isMastered = mastered.has(hit.doc_id);
    // 已掌握是针对单篇文献的显式信号，优先于主题级薄弱点，避免把已掌握的文献推到队首。
    const priority = isMastered ? 'low_mastered' : (isWeak ? 'high_weak_topic' : 'normal');
    const card = hit.flashcard ?? {};
    const baseInterval = Number(card.interval_days ?? 1) || 1;
    const ease = Number(card.ease ?? 2.5) || 2.5;
    const scheduled = cardState[hit.doc_id] ?? null;
    const nextReviewDays = scheduled?.interval_days
      ? scheduled.interval_days
      : priorityInterval(baseInterval, ease, priority);
    flashcards.push({
      doc_id: hit.doc_id,
      topic: hit.topic,
      priority,
      mastered: isMastered,
      weak_topic_match: isWeak,
      question: card.question ?? '',
      answer: card.answer ?? '',
      interval_days: baseInterval,
      ease: scheduled?.ease ?? ease,
      next_review_days: nextReviewDays,
      reviewed: Boolean(scheduled && scheduled.last_reviewed_at),
      due: Boolean(scheduled && scheduled.due_at && scheduled.due_at <= nowSeconds),
      reps: scheduled?.reps ?? 0,
      lapses: scheduled?.lapses ?? 0,
    });
  }

  const conceptEdges = [];
  for (let i = 0; i < hits.length; i++) {
    for (let j = i + 1; j < hits.length; j++) {
      if (hits[i].topic === hits[j].topic) {
        conceptEdges.push({
          from: hits[i].doc_id,
          to: hits[j].doc_id,
          relation: `shares_topic(${hits[i].topic})`,
        });
      }
    }
  }

  const priorityRank = { high_weak_topic: 0, normal: 1, low_mastered: 2 };
  flashcards.sort((a, b) => {
    const dueDiff = Number(b.due) - Number(a.due);
    if (dueDiff) return dueDiff;
    const rankDiff = (priorityRank[a.priority] ?? 1) - (priorityRank[b.priority] ?? 1);
    if (rankDiff) return rankDiff;
    return a.next_review_days - b.next_review_days;
  });

  const preparedContext = header + lines.join('') + footer;
  return {
    research_question: researchQuestion,
    learner_id: learnerId,
    prepared_context: preparedContext,
    prepared_context_bytes: encoder.encode(preparedContext).length,
    max_bytes: maxBytes,
    citations,
    flashcards,
    concept_graph_edges: conceptEdges,
    synthesis: generateResearchSynthesis(researchQuestion, hits, citations),
    retrieval: {
      fusion: 'RRF',
      rrf_k: RRF_K,
      vector_hits: hits.filter(hit => (hit.channels ?? []).includes('vector')).length,
      fts_hits: hits.filter(hit => (hit.channels ?? []).includes('fts')).length,
      candidates: hits.length,
    },
    review_due: flashcards.filter(card => card.due).length,
    handoff: {
      schema: 'powercontext.handoff.v1',
      artifact_address: `scope_id='${SCOPE_ID}' artifact=ArtifactRef(family='handoff', artifact_id='scholar-study-brief', revision=1)`,
      scope_id: SCOPE_ID,
      citations,
    },
  };
}

/* ---------------- 论文切分与派生 ---------------- */

export function detectSectionIntent(heading, body = '') {
  const target = `${heading} ${body}`.toLowerCase();
  if (/架构|实现|机制|结构|设计|pipeline|architecture|mechanism|structure|engine|paxos|raft|lsm/.test(target)) {
    return 'mechanism';
  }
  if (/算法|公式|计算|数学|推导|algorithm|formula|softmax|cosine|distance|rrf|hnsw|sm-2|sm2|quantiz/.test(target)) {
    return 'algorithm';
  }
  if (/对比|权衡|评估|性能|延迟|吞吐|trade-off|comparison|benchmark|latency|throughput|overhead|vs/.test(target)) {
    return 'tradeoff';
  }
  return 'concept';
}

export function deriveQuestion(heading, paperTitle, body = '') {
  const intent = detectSectionIntent(heading, body);
  if (intent === 'mechanism') {
    return `在《${paperTitle}》的「${heading}」中，系统如何实现其核心机制，有哪些关键设计？`;
  }
  if (intent === 'algorithm') {
    return `在《${paperTitle}》中，针对「${heading}」，其核心算法推导或数学原理是什么？`;
  }
  if (intent === 'tradeoff') {
    return `《${paperTitle}》在「${heading}」中指出了哪些关键技术指标权衡（Trade-offs）与工程取舍？`;
  }
  return `${paperTitle} 的「${heading}」说明了什么？`;
}

export function deriveTakeaway(heading, firstSentence, body = '') {
  const raw = body || firstSentence;
  const sentences = String(raw ?? '')
    .split(/(?<=[.!?。！？])\s*/)
    .map(s => s.trim())
    .filter(Boolean);
  for (const s of sentences) {
    if (/通过|采用|实现了|核心是|proves|achieves|combines|integrates|demonstrates|proposes/.test(s)) {
      return s.length > 140 ? `${s.slice(0, 140)}…` : s;
    }
  }
  const trimmed = String(firstSentence ?? '').trim();
  if (!trimmed) return heading;
  return trimmed.length > 140 ? `${trimmed.slice(0, 140)}…` : trimmed;
}

export function deriveAnswer(heading, body, takeaway) {
  const sentences = String(body ?? '')
    .split(/(?<=[.!?。！？])\s*/)
    .map(s => s.trim())
    .filter(Boolean);
  if (!sentences.length) return takeaway;
  if (sentences.length === 1) return sentences[0].slice(0, 240);
  const combined = sentences.slice(0, 2).join(' ');
  return combined.slice(0, 240);
}

// 将论文原文按标题层级切分为可检索章节；无标题时退化为按空行分段。
export function parsePaper(rawText, { defaultTitle = '', topic = 'context_engineering' } = {}) {
  const text = String(rawText ?? '').replace(/\r\n/g, '\n').trim();
  if (!text) return [];
  const lines = text.split('\n');
  const titleLine = lines.find(line => /^#\s+/.test(line));
  const paperTitle = (titleLine ? titleLine.replace(/^#\s+/, '') : defaultTitle).trim() || '未命名文献';

  const sections = [];
  let current = null;
  const bodyText = (section) => (section ? section.body.join('\n').trim() : '');
  for (const line of lines) {
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      if (bodyText(current)) sections.push(current);
      current = { heading: heading[2].trim(), body: [] };
      continue;
    }
    if (!current) current = { heading: '', body: [] };
    current.body.push(line);
  }
  if (bodyText(current)) sections.push(current);

  const hasHeadings = sections.some(section => section.heading);
  let usable = sections.filter(section => bodyText(section));
  if (!hasHeadings) {
    // 无标题层级时退化为按空行分段。
    usable = text
      .split(/\n\s*\n/)
      .map(block => ({ heading: '', body: block.split('\n') }))
      .filter(block => block.body.join('').trim());
  }

  return usable.map((section, index) => {
    const body = section.body.join('\n').trim();
    const firstSentence = body.split(/(?<=[.!?。！？])\s*/)[0]?.trim() || body.slice(0, 120);
    const heading = section.heading || `段落 ${index + 1}`;
    const takeaway = deriveTakeaway(heading, firstSentence, body);
    const answer = deriveAnswer(heading, body, takeaway);
    return {
      paper_title: paperTitle,
      section: heading,
      content: body,
      key_takeaway: takeaway,
      flashcard: {
        question: deriveQuestion(heading, paperTitle, body),
        answer,
        interval_days: 1,
        ease: 2.5,
      },
      topic,
    };
  });
}
