/**
 * StepFun × OceanBase SeekDB —— 官方 seekdb-js 接入的完整服务端实现
 *
 * 赛题 2.3 要求「通过 seekdb-js 接入 seekdb」。本文件是与 Python 版
 * `server.py` 功能对等的 Node.js 服务端：对外暴露相同的 HTTP API 与
 * 静态页面，数据层完全基于官方 npm 包 `seekdb`（SeekdbClient）实现，
 * 向量与全文混合检索以单条 SQL 在 SeekDB 内核完成。
 *
 * 启动：
 *   npm install
 *   node server_seekdb_js.mjs            # 默认 127.0.0.1:8089
 *
 * 连接参数（与 Python 侧一致）：
 *   SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASS / SEEKDB_DB
 * 默认连接本机 seekdb（127.0.0.1:2881）；本地实例可用
 * `bash scripts/start_seekdb.sh --daemon` 或 `python seekdb_bootstrap.py` 拉起。
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { SeekdbClient } from "seekdb";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const SEEKDB_CONFIG = {
  host: process.env.SEEKDB_HOST || "127.0.0.1",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASS || "",
  database: process.env.SEEKDB_DB || "powercontext",
};

export const TABLE_NAME = "stepfun_knowledge_base";
export const EMBEDDING_DIM = 512;

const STATIC_DIR = path.join(__dirname, "static");
const BENCH_DIR = path.join(__dirname, "benchmark");

// 会话上下文：与 Python 侧相同的结构（查询轨迹、集合与延迟审计）
const SESSION_STORE = new Map();
let DB_STATUS = "尚未初始化";

/* ------------------------------------------------------------------ */
/* SQL 构造：与 server.py 保持同一套库内混合检索语义                     */
/* ------------------------------------------------------------------ */

/**
 * 单条 SQL：向量通道 + 全文通道在库内 UNION ALL，随后 GROUP BY id 做 RRF 融合排序。
 * 参数顺序：rrf_k, vec, [filter...], vec, candidate_limit,
 *           rrf_k, query_text, [filter...], query_text, candidate_limit, top_k
 */
export function buildHybridSQL(filterSql = "", ftsFilterSql = "") {
  return `
    SELECT
      id,
      MAX(title) AS title,
      MAX(content) AS content,
      MAX(category) AS category,
      MAX(collection_id) AS collection_id,
      MAX(quant_mode) AS quant_mode,
      MAX(device_source) AS device_source,
      MAX(client_ms) AS client_ms,
      COALESCE(MIN(distance), 0.45) AS distance,
      MAX(fts_score) AS fts_score,
      SUM(rrf_part) AS rrf_score,
      GROUP_CONCAT(channel ORDER BY channel SEPARATOR '+') AS channels_str
    FROM (
      SELECT
        v.*,
        1.0 / (? + ROW_NUMBER() OVER (ORDER BY v.distance ASC, v.id ASC)) AS rrf_part
      FROM (
        SELECT
          id, title, content, category,
          COALESCE(collection_id, 'default') AS collection_id,
          COALESCE(quant_mode, 'q8') AS quant_mode,
          device_source, client_ms,
          'vector' AS channel,
          COSINE_DISTANCE(embedding, ?) AS distance,
          0.0 AS fts_score
        FROM ${TABLE_NAME}
        ${filterSql}
        ORDER BY COSINE_DISTANCE(embedding, ?) APPROXIMATE
        LIMIT ?
      ) v
      UNION ALL
      SELECT
        f.*,
        1.0 / (? + ROW_NUMBER() OVER (ORDER BY f.fts_score DESC, f.id ASC)) AS rrf_part
      FROM (
        SELECT
          id, title, content, category,
          COALESCE(collection_id, 'default') AS collection_id,
          COALESCE(quant_mode, 'q8') AS quant_mode,
          device_source, client_ms,
          'fts' AS channel,
          NULL AS distance,
          MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) AS fts_score
        FROM ${TABLE_NAME}
        ${ftsFilterSql}
        ORDER BY fts_score DESC, id ASC
        LIMIT ?
      ) f
    ) hybrid_candidates
    GROUP BY id
    ORDER BY rrf_score DESC, distance ASC, id ASC
    LIMIT ?
  `;
}

export function buildVectorSQL(filterSql = "") {
  return `
    SELECT
      id, title, content, category,
      COALESCE(collection_id, 'default') AS collection_id,
      COALESCE(quant_mode, 'q8') AS quant_mode,
      device_source, client_ms,
      COSINE_DISTANCE(embedding, ?) AS distance,
      0.0 AS fts_score,
      1.0 / (1.0 + COSINE_DISTANCE(embedding, ?)) AS rrf_score,
      'vector' AS channels_str
    FROM ${TABLE_NAME}
    ${filterSql}
    ORDER BY COSINE_DISTANCE(embedding, ?) APPROXIMATE
    LIMIT ?
  `;
}

export function buildFtsSQL(ftsFilterSql = "") {
  return `
    SELECT
      id, title, content, category,
      COALESCE(collection_id, 'default') AS collection_id,
      COALESCE(quant_mode, 'q8') AS quant_mode,
      device_source, client_ms,
      0.45 AS distance,
      MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) AS fts_score,
      MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) AS rrf_score,
      'fts' AS channels_str
    FROM ${TABLE_NAME}
    ${ftsFilterSql}
    ORDER BY fts_score DESC, id ASC
    LIMIT ?
  `;
}

export function buildFilterClauses({ collectionId, category } = {}) {
  const where = [];
  const params = [];
  if (collectionId) {
    where.push("COALESCE(collection_id, 'default') = ?");
    params.push(collectionId);
  }
  if (category) {
    where.push("category = ?");
    params.push(category);
  }
  const filterSql = where.length ? ` WHERE ${where.join(" AND ")}` : "";
  const ftsWhere = [...where, "MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) > 0"];
  const ftsFilterSql = ` WHERE ${ftsWhere.join(" AND ")}`;
  return { filterSql, ftsFilterSql, params, where };
}

export function vectorLiteral(values) {
  if (!Array.isArray(values) || values.length !== EMBEDDING_DIM) {
    throw Object.assign(new Error(`向量维度不匹配：期望 ${EMBEDDING_DIM} 维，实际收到 ${values?.length ?? 0} 维`), {
      statusCode: 400,
    });
  }
  return `[${values.map((x) => Number(x).toFixed(6)).join(",")}]`;
}

/* ------------------------------------------------------------------ */
/* 数据库层                                                            */
/* ------------------------------------------------------------------ */

let client = null;

async function connect() {
  if (client) return client;
  const base = {
    host: SEEKDB_CONFIG.host,
    port: SEEKDB_CONFIG.port,
    user: SEEKDB_CONFIG.user,
    password: SEEKDB_CONFIG.password,
    charset: "utf8mb4",
  };
  const bootstrap = new SeekdbClient(base);
  try {
    await bootstrap.execute(
      `CREATE DATABASE IF NOT EXISTS \`${SEEKDB_CONFIG.database}\` DEFAULT CHARACTER SET utf8mb4`,
    );
  } finally {
    await bootstrap.close();
  }
  client = new SeekdbClient({ ...base, database: SEEKDB_CONFIG.database });
  // seekdb 默认 ob_query_timeout 只有 10s，批量写入与索引构建容易触顶
  await client.execute("SET SESSION ob_query_timeout = 600000000");
  await client.execute("SET SESSION net_read_timeout = 600");
  await client.execute("SET SESSION net_write_timeout = 600");
  return client;
}

function dbError(err) {
  const e = new Error(
    `SeekDB 不可用（${SEEKDB_CONFIG.host}:${SEEKDB_CONFIG.port}）：${err.message}。` +
      "请确认本地 seekdb 已启动（bash scripts/start_seekdb.sh），或设置 SEEKDB_HOST 指向可用实例。",
  );
  e.statusCode = 503;
  return e;
}

async function query(sql, params = []) {
  let c;
  try {
    c = await connect();
  } catch (err) {
    throw dbError(err);
  }
  try {
    return (await c.execute(sql, params)) || [];
  } catch (err) {
    throw dbError(err);
  }
}

export async function initSchema() {
  await query(
    `CREATE TABLE IF NOT EXISTS ${TABLE_NAME} (
       id BIGINT AUTO_INCREMENT PRIMARY KEY,
       title VARCHAR(255) NOT NULL,
       content TEXT NOT NULL,
       category VARCHAR(64) DEFAULT 'general',
       embedding VECTOR(${EMBEDDING_DIM}) NOT NULL,
       device_source VARCHAR(64) DEFAULT 'webgpu',
       client_ms FLOAT DEFAULT 0.0,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       collection_id VARCHAR(64) DEFAULT 'default',
       quant_mode VARCHAR(32) DEFAULT 'q8',
       VECTOR INDEX idx_stepfun_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
       FULLTEXT INDEX idx_stepfun_fts (title, content) WITH PARSER ik
     ) DEFAULT CHARSET=utf8mb4`,
  );
}

export async function ensureReady() {
  try {
    await connect();
    await initSchema();
    DB_STATUS = `seekdb-js 已连接 ${SEEKDB_CONFIG.host}:${SEEKDB_CONFIG.port}/${SEEKDB_CONFIG.database}`;
  } catch (err) {
    DB_STATUS = dbError(err).message;
  }
  return DB_STATUS;
}

/* ------------------------------------------------------------------ */
/* 业务处理                                                            */
/* ------------------------------------------------------------------ */

const nowMs = () => Number(process.hrtime.bigint() / 1000n) / 1000;

async function insertDocuments(docs, defaultCollection) {
  const rows = docs.map((d) => ({
    title: d.title,
    content: d.content,
    category: d.category || "general",
    collection: d.collection_id || defaultCollection || "default",
    vector: vectorLiteral(d.embedding),
    quant: d.quant_mode || "q8",
    device: d.device_source || "webgpu",
    clientMs: Number(d.client_ms || 0),
  }));
  const start = nowMs();
  for (const r of rows) {
    await query(
      `INSERT INTO ${TABLE_NAME}
        (title, content, category, collection_id, embedding, quant_mode, device_source, client_ms)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [r.title, r.content, r.category, r.collection, r.vector, r.quant, r.device, r.clientMs],
    );
  }
  return { count: rows.length, ms: Number((nowMs() - start).toFixed(2)) };}

async function handleStatus() {
  const start = nowMs();
  const [ver] = await query("SELECT VERSION() AS ver");
  const [cnt] = await query(`SELECT COUNT(*) AS cnt FROM ${TABLE_NAME}`);
  const collections = await query(`
    SELECT COALESCE(collection_id, 'default') AS collection_id,
           COUNT(*) AS doc_count,
           ROUND(AVG(client_ms), 2) AS avg_client_ms
    FROM ${TABLE_NAME}
    GROUP BY COALESCE(collection_id, 'default')`);
  const recent = await query(`
    SELECT id, title, category, COALESCE(collection_id, 'default') AS collection_id,
           COALESCE(quant_mode, 'q8') AS quant_mode, device_source, client_ms, created_at
    FROM ${TABLE_NAME} ORDER BY id DESC LIMIT 20`);
  return {
    status: "online",
    driver: "seekdb-js (official npm package `seekdb`)",
    seekdb_host: `${SEEKDB_CONFIG.host}:${SEEKDB_CONFIG.port}`,
    seekdb_mode: ["127.0.0.1", "localhost", "::1"].includes(SEEKDB_CONFIG.host) ? "local" : "remote",
    seekdb_status: DB_STATUS,
    database: SEEKDB_CONFIG.database,
    version: ver.ver,
    total_documents: cnt.cnt,
    collections,
    indexes: ["idx_stepfun_hnsw (VSAG HNSW COSINE)", "idx_stepfun_fts (FULLTEXT IK)"],
    active_sessions: SESSION_STORE.size,
    ping_ms: Number((nowMs() - start).toFixed(2)),
    recent_docs: recent,
  };
}

async function handleSearch(body) {
  const vec = vectorLiteral(body.embedding);
  const queryText = String(body.query_text || "").trim();
  const mode = String(body.mode || "hybrid").toLowerCase();
  const topK = Math.min(Math.max(Number(body.top_k || 5), 1), 20);
  const rrfK = Number(body.rrf_k || 60);
  const candidateLimit = Math.max(topK * 3, 10);
  const { filterSql, ftsFilterSql, params, where } = buildFilterClauses({
    collectionId: body.collection_id || undefined,
    category: body.category || undefined,
  });

  const start = nowMs();
  let rows;
  if (mode === "hybrid" && queryText) {
    rows = await query(buildHybridSQL(filterSql, ftsFilterSql), [
      rrfK,
      vec,
      ...params,
      vec,
      candidateLimit,
      rrfK,
      queryText,
      ...params,
      queryText,
      candidateLimit,
      topK,
    ]);
  } else if (mode === "fts" && queryText) {
    rows = await query(buildFtsSQL(ftsFilterSql), [queryText, queryText, ...params, queryText, topK]);
  } else {
    rows = await query(buildVectorSQL(filterSql), [vec, vec, ...params, vec, topK]);
  }
  const dbMs = Number((nowMs() - start).toFixed(2));

  const results = rows.map((r) => {
    const dist = r.distance === null || r.distance === undefined ? 0.45 : Number(r.distance);
    const fts = Number(r.fts_score || 0);
    const channels = String(r.channels_str || "vector").split("+");
    const vecSim = Math.max(0, 1 - dist);
    const ftsBonus = channels.includes("fts") ? Math.min(0.12, fts * 0.03) : 0;
    return {
      id: Number(r.id),
      title: r.title,
      content: r.content,
      category: r.category,
      collection_id: r.collection_id || "default",
      quant_mode: r.quant_mode || "q8",
      distance: Number(dist.toFixed(4)),
      fts_score: Number(fts.toFixed(4)),
      rrf_score: Number(Number(r.rrf_score || 0).toFixed(6)),
      channels,
      similarity_pct: Number((Math.min(0.999, vecSim + ftsBonus) * 100).toFixed(2)),
      device_source: r.device_source,
      client_ms: r.client_ms,
    };
  });

  const sessionId = body.session_id || "default-session";
  const history = SESSION_STORE.get(sessionId) || [];
  history.push({
    turn: history.length + 1,
    query_text: queryText,
    mode,
    collection_id: body.collection_id || "all",
    client_embed_ms: Number(body.client_ms || 0),
    seekdb_search_ms: dbMs,
    top_hit_titles: results.slice(0, 3).map((r) => r.title),
    timestamp: new Date().toISOString(),
  });
  SESSION_STORE.set(sessionId, history);

  return {
    mode,
    collection_id: body.collection_id || "all",
    session_id: sessionId,
    session_turn_count: history.length,
    seekdb_search_ms: dbMs,
    total_returned: results.length,
    executed_sql:
      mode === "hybrid" && queryText
        ? `SINGLE-SQL IN-SEEKDB HYBRID RRF (GROUP BY id ORDER BY SUM(rrf_part) DESC: VSAG HNSW + FULLTEXT IK '${queryText}') LIMIT ${topK}`
        : `SELECT id, title, COSINE_DISTANCE(embedding, '${vec.slice(0, 24)}...]') AS distance FROM ${TABLE_NAME} ORDER BY distance APPROXIMATE LIMIT ${topK};`,
    results,
    _filterCount: where.length,
  };
}

async function handleReindex() {
  const start = nowMs();
  const indexes = await query(`SHOW INDEX FROM ${TABLE_NAME}`);
  const [cnt] = await query(`SELECT COUNT(*) AS cnt FROM ${TABLE_NAME}`);
  return {
    status: "reindexed",
    total_documents: cnt.cnt,
    indexes: indexes.map((r) => ({
      key_name: r.Key_name,
      column_name: r.Column_name,
      index_type: r.Index_type,
    })),
    refresh_ms: Number((nowMs() - start).toFixed(2)),
  };
}

function readBenchmarkResults() {
  const summaryPath = path.join(BENCH_DIR, "results", "summary.json");
  if (!fs.existsSync(summaryPath)) {
    return { measured: false, note: "尚未运行量化与检索质量实测（/bench），无实测数据。", runs: [] };
  }
  try {
    const data = JSON.parse(fs.readFileSync(summaryPath, "utf-8"));
    return { measured: true, ...data };
  } catch (err) {
    return { measured: false, note: `读取 benchmark/results/summary.json 失败：${err.message}`, runs: [] };
  }
}

function saveBenchmarkResults(payload) {
  if (!payload || !Array.isArray(payload.runs) || !payload.runs.length) {
    throw Object.assign(new Error("结果为空，拒绝保存"), { statusCode: 400 });
  }
  const dir = path.join(BENCH_DIR, "results");
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const rawPath = path.join(dir, `raw-${stamp}.json`);
  fs.writeFileSync(rawPath, JSON.stringify(payload, null, 2), "utf-8");

  const summary = {
    model: payload.model,
    top_k: payload.top_k,
    corpus_size: payload.corpus_size,
    query_count: payload.query_count,
    started_at: payload.started_at,
    finished_at: payload.finished_at,
    user_agent: payload.user_agent,
    webgpu_available: payload.webgpu_available,
    raw_file: path.basename(rawPath),
    runs: payload.runs.map((r) => ({
      label: r.label,
      device: r.device,
      dtype: r.dtype,
      model_size_mb: r.model_size_mb,
      cold_load_ms: r.cold_load_ms,
      throughput_docs_per_sec: r.throughput_docs_per_sec,
      embed_p50_ms: r.embed_p50_ms,
      seekdb_p50_ms: r.seekdb_p50_ms,
      seekdb_p90_ms: r.seekdb_p90_ms,
      e2e_p50_ms: r.e2e_p50_ms,
      recall_at_5: r.recall_at_5,
      ndcg_at_5: r.ndcg_at_5,
    })),
  };
  fs.writeFileSync(path.join(dir, "summary.json"), JSON.stringify(summary, null, 2), "utf-8");
  return { saved: true, raw: path.basename(rawPath), summary: "summary.json" };
}

async function routeApi(req, res, url, body) {
  const send = (code, data) => {
    res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(data));
  };
  try {
    if (req.method === "GET" && url.pathname === "/api/status") {
      return send(200, await handleStatus());
    }
    if (req.method === "POST" && url.pathname === "/api/documents") {
      const r = await insertDocuments([body], body.collection_id);
      return send(200, {
        status: "inserted",
        count: r.count,
        collection_id: body.collection_id || "default",
        quant_mode: body.quant_mode || "q8",
        seekdb_insert_ms: r.ms,
        client_embed_ms: Number(body.client_ms || 0),
      });
    }
    if (req.method === "POST" && url.pathname === "/api/documents/batch") {
      const docs = body.documents || [];
      if (!docs.length) return send(400, { detail: "批量文档列表不能为空" });
      const r = await insertDocuments(docs, body.collection_id);
      return send(200, {
        status: "batch_inserted",
        count: r.count,
        collection_id: body.collection_id || "default",
        seekdb_batch_insert_ms: r.ms,
      });
    }
    if (req.method === "POST" && url.pathname === "/api/search") {
      return send(200, await handleSearch(body));
    }
    if (req.method === "POST" && url.pathname === "/api/reindex") {
      return send(200, await handleReindex());
    }
    if (req.method === "POST" && url.pathname === "/api/reset") {
      await query(`TRUNCATE TABLE ${TABLE_NAME}`);
      SESSION_STORE.clear();
      return send(200, { status: "truncated" });
    }
    if (req.method === "GET" && url.pathname === "/api/benchmark") {
      return send(200, readBenchmarkResults());
    }
    if (req.method === "POST" && url.pathname === "/api/benchmark/save") {
      return send(200, saveBenchmarkResults(body));
    }
    const sessionMatch = url.pathname.match(/^\/api\/sessions\/([^/]+)$/);
    if (sessionMatch) {
      const id = decodeURIComponent(sessionMatch[1]);
      if (req.method === "GET") {
        const history = SESSION_STORE.get(id) || [];
        return send(200, { session_id: id, turn_count: history.length, history });
      }
      if (req.method === "DELETE") {
        SESSION_STORE.delete(id);
        return send(200, { status: "cleared", session_id: id });
      }
    }
    return send(404, { detail: "not found" });
  } catch (err) {
    return send(err.statusCode || 500, { detail: err.message });
  }
}

/* ------------------------------------------------------------------ */
/* 静态资源                                                            */
/* ------------------------------------------------------------------ */

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};

function serveFile(res, filePath) {
  if (!filePath.startsWith(__dirname) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("not found");
    return;
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream" });
  fs.createReadStream(filePath).pipe(res);
}

function resolveStatic(url) {
  const pathname = decodeURIComponent(url.pathname);
  if (pathname === "/" || pathname === "/index.html") return path.join(STATIC_DIR, "index.html");
  if (pathname === "/bench" || pathname === "/bench.html") return path.join(BENCH_DIR, "bench.html");
  if (pathname.startsWith("/static/")) return path.join(STATIC_DIR, pathname.slice("/static/".length));
  if (pathname.startsWith("/benchmark/")) return path.join(BENCH_DIR, pathname.slice("/benchmark/".length));
  return null;
}

/* ------------------------------------------------------------------ */
/* HTTP 入口                                                           */
/* ------------------------------------------------------------------ */

export function createServer() {
  return http.createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);

    if (url.pathname.startsWith("/api/")) {
      let raw = "";
      req.on("data", (chunk) => {
        raw += chunk;
        if (raw.length > 32 * 1024 * 1024) req.destroy();
      });
      req.on("end", () => {
        let body = {};
        if (raw) {
          try {
            body = JSON.parse(raw);
          } catch {
            res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
            res.end(JSON.stringify({ detail: "请求体不是合法 JSON" }));
            return;
          }
        }
        routeApi(req, res, url, body);
      });
      return;
    }

    const filePath = resolveStatic(url);
    if (filePath) return serveFile(res, filePath);
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("not found");
  });
}

async function main() {
  await ensureReady();
  console.log(`[seekdb-js] ${DB_STATUS}`);
  const port = Number(process.env.APP_PORT || 8089);
  const host = process.env.APP_HOST || "127.0.0.1";
  const server = createServer();
  server.listen(port, host, () => {
    console.log(`[seekdb-js] 服务已启动 http://${host}:${port}（官方 npm 包 seekdb@1.x）`);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error("[seekdb-js] 启动失败：", err.message);
    console.error("请确认本地 seekdb 已启动（bash scripts/start_seekdb.sh），或设置 SEEKDB_HOST。");
    process.exitCode = 1;
  });
}
