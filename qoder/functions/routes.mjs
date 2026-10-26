// ScholarPulse 业务路由：与本地 FastAPI（server.py）保持同一套 API 契约，
// 因此 site/index.html 在本地与 Qoder Sites 上无需区分部署形态。
import {
  applyReview,
  buildBrief,
  buildPaperPresets,
  bm25Scores,
  corpusTopics,
  embeddingForDocument,
  fuseChannels,
  groupByPaper,
  mergeMemory,
  scholarEmbedding,
  SCOPE_ID,
} from './engine.mjs';

const LEARNER_ID_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;
const MAX_QUESTION_LENGTH = 500;
const MAX_CONTENT_LENGTH = 8000;
const MAX_BATCH = 200;

async function readCorpusFile() {
  const url = new URL('./corpus.json', import.meta.url);
  if (typeof Deno !== 'undefined') {
    return JSON.parse(await Deno.readTextFile(url));
  }
  const { readFile } = await import('node:fs/promises');
  return JSON.parse(await readFile(url, 'utf8'));
}

const CORPUS_PAYLOAD = await readCorpusFile();
const SEED_CORPUS = CORPUS_PAYLOAD.documents ?? [];

export function application({ db, BackendError }) {
  const fail = (code, status) => {
    throw new BackendError(code, status);
  };
  const rows = (result) => {
    if (result.error) fail(result.error.code, result.error.status);
    return result.data ?? [];
  };

  const embeddingCache = new Map();
  function embeddingFor(doc) {
    if (!embeddingCache.has(doc.doc_id)) {
      embeddingCache.set(doc.doc_id, scholarEmbedding(embeddingForDocument(doc)));
    }
    return embeddingCache.get(doc.doc_id);
  }

  async function readBody(request) {
    const contentType = request.headers.get('content-type') ?? '';
    if (!contentType.startsWith('application/json')) fail('invalid_input', 400);
    let size = 0;
    const chunks = [];
    const reader = request.body?.getReader();
    if (!reader) fail('invalid_input', 400);
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 262144) {
          await reader.cancel();
          fail('payload_too_large', 413);
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    try {
      return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    } catch {
      fail('invalid_input', 400);
    }
  }

  function parseJsonColumn(value, fallback) {
    if (value == null) return fallback;
    if (typeof value === 'string') {
      try {
        return JSON.parse(value);
      } catch {
        return fallback;
      }
    }
    return value;
  }

  function validateLearnerId(value) {
    if (typeof value !== 'string' || !LEARNER_ID_PATTERN.test(value)) fail('invalid_input', 400);
    return value;
  }

  function validateMemoryProfile(value) {
    if (value == null) return undefined;
    if (typeof value !== 'object' || Array.isArray(value)) fail('invalid_input', 400);
    const list = (raw) => {
      if (raw == null) return [];
      if (!Array.isArray(raw) || raw.some(item => typeof item !== 'string')) fail('invalid_input', 400);
      return raw.slice(0, 64);
    };
    const cards = value.cards && typeof value.cards === 'object' && !Array.isArray(value.cards) ? value.cards : {};
    return {
      weak_topics: list(value.weak_topics),
      mastered_doc_ids: list(value.mastered_doc_ids),
      preferred_depth: typeof value.preferred_depth === 'string' ? value.preferred_depth.slice(0, 32) : 'deep-derivation',
      review_note: typeof value.review_note === 'string' ? value.review_note.slice(0, 500) : '',
      cards,
    };
  }

  function normalizeDocument(input) {
    if (!input || typeof input !== 'object') fail('invalid_input', 400);
    const docId = typeof input.doc_id === 'string' ? input.doc_id.trim().slice(0, 64) : '';
    if (!docId) fail('invalid_input', 400);
    if (typeof input.content !== 'string' || !input.content.trim()) fail('invalid_input', 400);
    if (input.content.length > MAX_CONTENT_LENGTH) fail('invalid_input', 400);
    const card = input.flashcard && typeof input.flashcard === 'object' ? input.flashcard : {};
    return {
      doc_id: docId,
      paper_title: String(input.paper_title ?? docId).slice(0, 255),
      section: String(input.section ?? '').slice(0, 255),
      topic: String(input.topic ?? 'context_engineering').slice(0, 64),
      content: input.content.slice(0, MAX_CONTENT_LENGTH),
      key_takeaway: String(input.key_takeaway ?? '').slice(0, 1000),
      flashcard: {
        question: String(card.question ?? '').slice(0, 1000),
        answer: String(card.answer ?? '').slice(0, 2000),
        interval_days: Number(card.interval_days ?? 1) || 1,
        ease: Number(card.ease ?? 2.5) || 2.5,
      },
    };
  }

  async function loadDocuments() {
    const found = rows(
      await db
        .from('scholar_documents')
        .select('doc_id,paper_title,section,topic,content,key_takeaway,flashcard_json,powercontext_source_ref,embedding')
        .limit(2000),
    );
    return found.map(row => ({
      doc_id: row.doc_id,
      paper_title: row.paper_title,
      section: row.section,
      topic: row.topic,
      content: row.content,
      key_takeaway: row.key_takeaway,
      powercontext_source_ref: row.powercontext_source_ref,
      flashcard: parseJsonColumn(row.flashcard_json, {}),
      embedding: parseJsonColumn(row.embedding, []),
    }));
  }

  // 语料种子由 Function 侧写入（迁移中不含 DML）；已存在的条目跳过。
  async function ensureCorpus() {
    const existing = rows(
      await db.from('scholar_documents').select('doc_id').eq('scope_id', SCOPE_ID).limit(2000),
    );
    const present = new Set(existing.map(row => row.doc_id));
    for (const item of SEED_CORPUS) {
      if (present.has(item.doc_id)) continue;
      const embedding = embeddingFor(item);
      rows(
        await db.from('scholar_documents').insertIfAbsent(
          {
            doc_id: item.doc_id,
            scope_id: SCOPE_ID,
            paper_title: item.paper_title,
            section: item.section,
            topic: item.topic,
            content: item.content,
            key_takeaway: item.key_takeaway,
            flashcard_json: item.flashcard,
            powercontext_source_ref: `source_type='paper_section' source_id='${item.doc_id}'`,
            embedding,
          },
          { onConflict: 'doc_id' },
        ),
      );
    }
  }

  async function readMemory(learnerId) {
    const found = rows(
      await db
        .from('learner_memories')
        .select('learner_id,weak_topics,mastered_doc_ids,preferred_depth,review_note,cards,powercontext_source_ref')
        .eq('learner_id', learnerId)
        .limit(1),
    );
    if (!found.length) return null;
    const row = found[0];
    return {
      learner_id: row.learner_id,
      weak_topics: parseJsonColumn(row.weak_topics, []),
      mastered_doc_ids: parseJsonColumn(row.mastered_doc_ids, []),
      preferred_depth: row.preferred_depth,
      review_note: row.review_note,
      cards: parseJsonColumn(row.cards, {}),
      powercontext_source_ref: row.powercontext_source_ref,
    };
  }

  async function writeMemory(learnerId, profile) {
    const record = {
      learner_id: learnerId,
      scope_id: SCOPE_ID,
      weak_topics: profile.weak_topics,
      mastered_doc_ids: profile.mastered_doc_ids,
      preferred_depth: profile.preferred_depth,
      review_note: profile.review_note,
      cards: profile.cards ?? {},
      powercontext_source_ref: `scope_id='${SCOPE_ID}' artifact=ArtifactRef(family='learner_memory', artifact_id='${learnerId.toLowerCase()}', revision=1)`,
    };
    const inserted = rows(
      await db
        .from('learner_memories')
        .insertIfAbsent(record, { onConflict: 'learner_id' })
        .select('learner_id'),
    );
    if (!inserted.length) {
      rows(
        await db
          .from('learner_memories')
          .update({
            weak_topics: profile.weak_topics,
            mastered_doc_ids: profile.mastered_doc_ids,
            preferred_depth: profile.preferred_depth,
            review_note: profile.review_note,
            cards: profile.cards ?? {},
          })
          .eq('learner_id', learnerId)
          .select('learner_id'),
      );
    }
    return record;
  }

  return async ctx => {
    const request = ctx.request;
    const pathname = new URL(request.url).pathname;
    // Gateway 会把逻辑函数名 app 替换为部署后的物理函数名。
    const match = pathname.match(/^\/(?:functions\/v1\/)?(?:app|app-d[0-9a-f]{32})(\/.*)?$/);
    if (!match) fail('not_found', 404);
    const path = match[1] || '/';

    if (path === '/api/scholar/status' && request.method === 'GET') {
      await ensureCorpus();
      const documents = await loadDocuments();
      return Response.json({
        status: 'online',
        engine: 'qoder-sites-function',
        retrieval: 'vector+fulltext RRF(k=60)',
        scope_id: SCOPE_ID,
        total_sections: documents.length,
        topics: corpusTopics(documents),
        papers: groupByPaper(documents),
      });
    }

    if (path === '/api/scholar/reset' && request.method === 'POST') {
      await db.from('scholar_documents').delete().eq('scope_id', SCOPE_ID);
      await db.from('learner_memories').delete().eq('scope_id', SCOPE_ID);
      await ensureCorpus();
      const documents = await loadDocuments();
      return Response.json({
        status: 'reset',
        scope_id: SCOPE_ID,
        total_sections: documents.length,
      });
    }

    if (path === '/api/scholar/corpus' && request.method === 'GET') {
      await ensureCorpus();
      const documents = await loadDocuments();
      return Response.json({
        documents: documents.map(doc => ({
          doc_id: doc.doc_id,
          paper_title: doc.paper_title,
          section: doc.section,
          topic: doc.topic,
          key_takeaway: doc.key_takeaway,
          powercontext_source_ref: doc.powercontext_source_ref,
        })),
        topics: corpusTopics(documents),
        papers: groupByPaper(documents),
      });
    }

    if (path === '/api/scholar/presets' && request.method === 'GET') {
      await ensureCorpus();
      const documents = await loadDocuments();
      return Response.json({
        presets: buildPaperPresets(documents),
      });
    }

    if (path === '/api/scholar/memory' && request.method === 'POST') {
      const body = await readBody(request);
      const learnerId = validateLearnerId(body?.learner_id);
      const profile = validateMemoryProfile(body) ?? {};
      await ensureCorpus();
      const stored = await readMemory(learnerId);
      const merged = mergeMemory(stored, profile);
      const saved = await writeMemory(learnerId, merged);
      return Response.json({ status: 'saved', learner_id: learnerId, memory: saved });
    }

    const memoryRoute = path.match(/^\/api\/scholar\/memory\/([^/]+)$/);
    if (memoryRoute && request.method === 'GET') {
      const learnerId = decodeURIComponent(memoryRoute[1]);
      if (!LEARNER_ID_PATTERN.test(learnerId)) fail('invalid_input', 400);
      const memory = await readMemory(learnerId);
      if (!memory) fail('not_found', 404);
      return Response.json(memory);
    }

    if (path === '/api/scholar/review' && request.method === 'POST') {
      const body = await readBody(request);
      const learnerId = validateLearnerId(body?.learner_id);
      const docId = typeof body?.doc_id === 'string' ? body.doc_id.trim() : '';
      if (!docId) fail('invalid_input', 400);
      const grade = Number(body?.grade);
      if (![0, 3, 4, 5].includes(grade)) fail('invalid_input', 400);
      const stored = (await readMemory(learnerId)) ?? { cards: {}, weak_topics: [], mastered_doc_ids: [] };
      const cards = applyReview(stored.cards ?? {}, docId, grade, Math.floor(Date.now() / 1000));
      const merged = mergeMemory({ ...stored, cards }, null);
      const saved = await writeMemory(learnerId, merged);
      return Response.json({ status: 'reviewed', learner_id: learnerId, card: saved.cards[docId], memory: saved });
    }

    if (path === '/api/scholar/ingest' || path === '/api/scholar/ingest/batch') {
      if (request.method !== 'POST') fail('method_not_allowed', 405);
      const body = await readBody(request);
      const batch = Array.isArray(body?.documents) ? body.documents : [body];
      if (!batch.length || batch.length > MAX_BATCH) fail('invalid_input', 400);
      await ensureCorpus();
      const accepted = [];
      for (const raw of batch) {
        const doc = normalizeDocument(raw);
        const embedding = embeddingFor(doc);
        rows(
          await db.from('scholar_documents').insertIfAbsent(
            {
              doc_id: doc.doc_id,
              scope_id: SCOPE_ID,
              paper_title: doc.paper_title,
              section: doc.section,
              topic: doc.topic,
              content: doc.content,
              key_takeaway: doc.key_takeaway,
              flashcard_json: doc.flashcard,
              powercontext_source_ref: `source_type='paper_section' source_id='${doc.doc_id}'`,
              embedding,
            },
            { onConflict: 'doc_id' },
          ),
        );
        accepted.push(doc.doc_id);
      }
      return Response.json({ status: 'ingested', doc_ids: accepted, count: accepted.length });
    }

    if (path === '/api/scholar/brief' && request.method === 'POST') {
      const body = await readBody(request);
      const researchQuestion = typeof body?.research_question === 'string' ? body.research_question.trim() : '';
      if (!researchQuestion) fail('invalid_input', 400);
      if (researchQuestion.length > MAX_QUESTION_LENGTH) fail('invalid_input', 400);
      const maxBytes = Number.isInteger(body?.max_bytes) ? body.max_bytes : 1600;
      if (maxBytes < 400 || maxBytes > 8000) fail('invalid_input', 400);
      const learnerId = validateLearnerId(body?.learner_id ?? 'default-scholar');
      const supplied = validateMemoryProfile(body?.memory);
      const scopeDocIds = Array.isArray(body?.scope_doc_ids)
        ? body.scope_doc_ids.filter(id => typeof id === 'string').slice(0, 64)
        : null;

      await ensureCorpus();
      const documents = await loadDocuments();
      if (!documents.length) fail('corpus_empty', 503);

      const queryEmbedding = scholarEmbedding(researchQuestion);
      const lexicalScores = bm25Scores(researchQuestion, documents);
      const hits = fuseChannels(documents, queryEmbedding, lexicalScores, 4, scopeDocIds);

      const stored = await readMemory(learnerId);
      const profile = mergeMemory(stored, supplied);
      const brief = buildBrief({
        researchQuestion,
        maxBytes,
        learnerId,
        profile,
        hits,
        cardState: profile.cards ?? {},
        nowSeconds: Math.floor(Date.now() / 1000),
      });
      return Response.json({
        ...brief,
        reused_learner_memory: profile,
        memory_source: stored ? 'store' : (supplied ? 'request' : 'none'),
      });
    }

    fail('not_found', 404);
  };
}
