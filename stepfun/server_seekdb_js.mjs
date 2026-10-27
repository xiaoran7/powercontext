/**
 * StepFun × OceanBase SeekDB Node.js 接入层（可选组件）
 *
 * 主应用是 Python FastAPI（server.py），本文件提供等价的 Node.js 接入方式，
 * 用于验证「通过官方 seekdb SDK 接入 seekdb，并在数据库内核完成混合检索」。
 *
 * 依据阶跃星辰赛题 2.3 节：
 * 1. 通过官方 seekdb JS SDK 接入 seekdb；
 * 2. 向量与全文混合检索完全在 SeekDB 内核中通过单条 SQL 完成（ROW_NUMBER() + RRF GROUP BY），
 *    不在客户端分别拉取两路结果后自行拼合。
 *
 * 用法：
 *   npm install
 *   node server_seekdb_js.mjs            # 自检：连接 seekdb 并跑一次混合检索 SQL
 *
 * 连接参数与 Python 侧一致：SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASS / SEEKDB_DB
 * 默认连接本机 seekdb（127.0.0.1:2881），可用 bash scripts/start_seekdb.sh 拉起。
 */
import { SeekdbClient } from "seekdb";
import { pathToFileURL } from "node:url";

export const SEEKDB_CONFIG = {
  host: process.env.SEEKDB_HOST || "127.0.0.1",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASS || "",
  database: process.env.SEEKDB_DB || "powercontext",
};

export const TABLE_NAME = "stepfun_knowledge_base";

/**
 * 单条 SQL：向量通道 + 全文通道在库内 UNION ALL，随后 GROUP BY id 做 RRF 融合排序。
 * 参数顺序：rrf_k, vec, [filter...], vec, candidate_limit, rrf_k, query_text, [filter...], query_text, candidate_limit, top_k
 */
export function buildInSeekDBHybridSQL(filterSql = "", ftsFilterSql = "") {
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
  const ftsFilterSql = where.length
    ? ` WHERE ${[...where, "MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) > 0"].join(" AND ")}`
    : " WHERE MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) > 0";
  return { filterSql, ftsFilterSql, params };
}

export function hybridSearchParams({ vector, queryText, topK = 5, rrfK = 60, collectionId, category }) {
  const { filterSql, ftsFilterSql, params } = buildFilterClauses({ collectionId, category });
  const candidateLimit = Math.max(topK * 3, 10);
  const sql = buildInSeekDBHybridSQL(filterSql, ftsFilterSql);
  const values = [
    rrfK,
    vector,
    ...params,
    vector,
    candidateLimit,
    rrfK,
    queryText,
    ...params,
    queryText,
    candidateLimit,
    topK,
  ];
  return { sql, values };
}

export async function connect() {
  const client = new SeekdbClient(SEEKDB_CONFIG);
  return client;
}

async function main() {
  const client = await connect();
  await client.execute(
    `CREATE TABLE IF NOT EXISTS ${TABLE_NAME} (
       id BIGINT AUTO_INCREMENT PRIMARY KEY,
       title VARCHAR(255) NOT NULL,
       content TEXT NOT NULL,
       category VARCHAR(64) DEFAULT 'general',
       embedding VECTOR(512) NOT NULL,
       device_source VARCHAR(64) DEFAULT 'webgpu',
       client_ms FLOAT DEFAULT 0.0,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       collection_id VARCHAR(64) DEFAULT 'default',
       quant_mode VARCHAR(32) DEFAULT 'q8',
       VECTOR INDEX idx_stepfun_hnsw (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
       FULLTEXT INDEX idx_stepfun_fts (title, content) WITH PARSER ik
     ) DEFAULT CHARSET=utf8mb4`,
  );
  const vector = `[${Array.from({ length: 512 }, () => "0.001").join(",")}]`;
  const { sql, values } = hybridSearchParams({
    vector,
    queryText: "SeekDB 混合检索",
    topK: 3,
  });
  const rows = await client.execute(sql, values);
  console.log(
    `[seekdb-js] 连接成功 ${SEEKDB_CONFIG.host}:${SEEKDB_CONFIG.port}/${SEEKDB_CONFIG.database}，` +
      `库内混合检索返回 ${(rows || []).length} 行`,
  );
  await client.close();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error("[seekdb-js] 自检失败：", err.message);
    console.error("请确认本地 seekdb 已启动（bash scripts/start_seekdb.sh），或设置 SEEKDB_HOST。");
    process.exitCode = 1;
  });
}
