// 本地函数自检：只使用内存数据库替身，不连接任何云端服务。
// 校验 ScholarPulse 业务路由的检索、记忆与简报装配逻辑。
// 放在 dev/ 下，不属于任何上传目录。
import { application } from '../functions/routes.mjs';

class LocalError extends Error {
  constructor(code, status) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

function createMemoryDb() {
  const tables = { scholar_documents: [], learner_memories: [] };
  const db = {
    from(table) {
      if (!(table in tables)) throw new LocalError('invalid_table', 400);
      const filters = [];
      const order = [];
      let mode = 'read';
      let values;
      let conflict;
      let columns;
      let count = 1000;
      const query = {
        select(value) {
          columns = value;
          return query;
        },
        eq(key, value) {
          filters.push([key, value]);
          return query;
        },
        order(key, options = {}) {
          order.push([key, options.ascending !== false]);
          return query;
        },
        limit(value) {
          count = value;
          return query;
        },
        insert(value) {
          mode = 'insert';
          values = value;
          return query;
        },
        insertIfAbsent(value, options) {
          mode = 'insert';
          values = value;
          conflict = options.onConflict.split(',');
          return query;
        },
        update(value) {
          mode = 'update';
          values = value;
          return query;
        },
        then(fulfilled, rejected) {
          return Promise.resolve()
            .then(() => {
              let result;
              if (mode === 'insert') {
                const duplicate =
                  conflict && tables[table].some(row => conflict.every(key => row[key] === values[key]));
                if (duplicate) {
                  result = [];
                } else {
                  const row = { id: crypto.randomUUID(), created_at: new Date().toISOString(), ...values };
                  tables[table].push(row);
                  result = [row];
                }
              } else {
                result = tables[table].filter(row => filters.every(([key, value]) => row[key] === value));
                if (mode === 'update') result.forEach(row => Object.assign(row, values));
              }
              for (const [key, asc] of [...order].reverse()) {
                result = [...result].sort((a, b) => (a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0) * (asc ? 1 : -1));
              }
              result = result.slice(0, count).map(row =>
                columns ? Object.fromEntries(columns.split(',').map(key => [key, row[key]])) : { ...row },
              );
              return { data: result, error: null };
            })
            .then(fulfilled, rejected);
        },
      };
      return query;
    },
  };
  return { db, tables };
}

const { db, tables } = createMemoryDb();
const handle = application({ db, BackendError: LocalError });

async function call(method, path, body) {
  const request = new Request('http://127.0.0.1/functions/v1/app' + path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  try {
    return await handle({ request, user: null });
  } catch (error) {
    // 与部署入口的 errorResponse 行为保持一致：业务错误映射为同状态码响应。
    const status = error?.status ?? 500;
    return Response.json({ error: error?.code ?? 'internal_error' }, { status });
  }
}

const checks = [];
function check(name, condition, detail) {
  checks.push({ name, ok: Boolean(condition), detail });
}

const status = await (await call('GET', '/api/scholar/status')).json();
check('status online', status.status === 'online', status.status);
check('corpus seeded to docs', status.total_sections >= 4, status.total_sections);

await call('POST', '/api/scholar/memory', {
  learner_id: 'local-check-01',
  weak_topics: ['hybrid_rag'],
  mastered_doc_ids: ['PAPER-VSAG-01'],
  preferred_depth: 'deep-derivation',
  review_note: '攻克 RRF',
});
const stored = await (await call('GET', '/api/scholar/memory/local-check-01')).json();
check('memory persisted', stored.learner_id === 'local-check-01');
check('weak topic stored', stored.weak_topics.includes('hybrid_rag'), stored.weak_topics);
check('mastered doc stored', stored.mastered_doc_ids.includes('PAPER-VSAG-01'), stored.mastered_doc_ids);

const brief = await (
  await call('POST', '/api/scholar/brief', {
    research_question: 'RRF 倒数排名融合与 HNSW 检索如何协同',
    max_bytes: 1400,
    learner_id: 'local-check-01',
    memory: { weak_topics: ['vector_index'], mastered_doc_ids: [], preferred_depth: 'standard', review_note: '' },
  })
).json();

check('envelope header', brief.prepared_context.startsWith('BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n'));
check('envelope footer', brief.prepared_context.endsWith('END_POWERCONTEXT_PREPARED_CONTEXT_V1\n'));
check('byte budget respected', brief.prepared_context_bytes <= 1400, `${brief.prepared_context_bytes} <= 1400`);
check('citations produced', brief.citations.length > 0, brief.citations.length);
check('memory merged (union)', brief.reused_learner_memory.weak_topics.length === 2, brief.reused_learner_memory.weak_topics);
check('mastered doc preserved', brief.reused_learner_memory.mastered_doc_ids.includes('PAPER-VSAG-01'));
check('memory source is store', brief.memory_source === 'store', brief.memory_source);
check('weak card pinned first', brief.flashcards[0].priority === 'high_weak_topic', brief.flashcards[0].doc_id);
check('mastered card demoted', brief.flashcards.some(c => c.doc_id === 'PAPER-VSAG-01' && c.priority === 'low_mastered'));
const nonWeakCard = brief.flashcards.find(c => c.priority === 'normal' || c.priority === 'low_mastered');
check('adaptive interval shorter for weak', nonWeakCard ? brief.flashcards[0].next_review_days < nonWeakCard.next_review_days : true);
check('handoff schema', brief.handoff.schema === 'powercontext.handoff.v1');
check('concept edges share topic', brief.concept_graph_edges.every(e => e.relation.startsWith('shares_topic(')));

await call('POST', '/api/scholar/ingest', {
  doc_id: 'PAPER-LOCAL-99',
  paper_title: 'Local Ingest Check',
  section: 'Section 1.0',
  topic: 'agent_workflow',
  content: 'A locally ingested paragraph used to verify that the hosted corpus accepts new documents.',
  key_takeaway: '本地入库校验。',
  flashcard_q: '入库是否生效？',
  flashcard_a: '生效。',
});
const afterIngest = await (await call('GET', '/api/scholar/status')).json();
check('ingest persisted to corpus', afterIngest.total_sections === status.total_sections + 1, afterIngest.total_sections);

const missing = await call('GET', '/api/scholar/memory/does-not-exist');
check('unknown learner returns 404', missing.status === 404, missing.status);

const badMethod = await call('POST', '/api/scholar/status', {});
check('wrong method rejected', badMethod.status === 404 || badMethod.status === 405, badMethod.status);

// --- 文献库 / 批量导入 / 复习评分 ---
const corpus = await (await call('GET', '/api/scholar/corpus')).json();
check('corpus lists documents', (corpus.documents || []).length >= 4, corpus.documents?.length);
check('corpus grouped by paper', (corpus.papers || []).length >= 4, corpus.papers?.length);
check('corpus topics derived', (corpus.topics || []).length >= 3, corpus.topics?.length);

const batch = await (await call('POST', '/api/scholar/ingest/batch', {
  documents: [
    {
      doc_id: 'PAPER-BATCH-01',
      paper_title: 'Batch Import Check',
      section: 'Section 9.9: Batch Path',
      topic: 'agent_workflow',
      content: 'A batch imported section used to verify the corpus ingestion path.',
      key_takeaway: '批量导入校验。',
      flashcard: { question: '批量导入是否生效？', answer: '生效。', interval_days: 1, ease: 2.5 },
    },
  ],
})).json();
check('batch ingest accepted', batch.status === 'ingested' && batch.count === 1, JSON.stringify(batch));

const corpusAfter = await (await call('GET', '/api/scholar/corpus')).json();
check('imported doc lands in corpus', corpusAfter.documents.some(d => d.doc_id === 'PAPER-BATCH-01'));

const reviewed = await (await call('POST', '/api/scholar/review', {
  learner_id: 'local-check-01',
  doc_id: 'PAPER-RRF-03',
  grade: 5,
})).json();
check('review recorded', reviewed.status === 'reviewed', reviewed.status);
check('sm2 ease adjusted', (reviewed.card?.ease ?? 0) > 2.5, reviewed.card?.ease);
check('sm2 interval scheduled', (reviewed.card?.interval_days ?? 0) >= 1, reviewed.card?.interval_days);

const scopedBrief = await (await call('POST', '/api/scholar/brief', {
  research_question: '批量导入校验',
  max_bytes: 1400,
  learner_id: 'local-check-01',
  scope_doc_ids: ['PAPER-BATCH-01'],
})).json();
check('scope restricts retrieval', scopedBrief.flashcards.every(c => c.doc_id === 'PAPER-BATCH-01'),
  scopedBrief.flashcards.map(c => c.doc_id).join(','));

const badGrade = await call('POST', '/api/scholar/review', { learner_id: 'local-check-01', doc_id: 'X', grade: 2 });
check('invalid grade rejected', badGrade.status === 400, badGrade.status);

let failed = 0;
for (const item of checks) {
  if (!item.ok) failed++;
  console.log(`${item.ok ? 'PASS' : 'FAIL'}  ${item.name}${item.detail !== undefined ? `  [${item.detail}]` : ''}`);
}
console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed === 0 ? 0 : 1);
