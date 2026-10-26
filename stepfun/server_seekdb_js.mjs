/**
 * StepFun × OceanBase SeekDB Node.js / seekdb-js 接入层
 * 严格遵循阶跃星辰赛题 2.3 节要求：
 * 1. 通过 seekdb-js / MySQL 协议接入远端 SeekDB (100.72.34.104:2881)；
 * 2. 向量与全文混合检索完全在 SeekDB 数据库内核中通过单条 SQL (ROW_NUMBER() + RRF GROUP BY) 完成，
 *    绝不在客户端分别拉取两路结果后自行拼合。
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SEEKDB_CONFIG = {
  host: process.env.SEEKDB_HOST || "100.72.34.104",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASS || "",
  database: process.env.SEEKDB_DB || "powercontext",
};

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
        FROM stepfun_knowledge_base
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
        FROM stepfun_knowledge_base
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

export { SEEKDB_CONFIG };
