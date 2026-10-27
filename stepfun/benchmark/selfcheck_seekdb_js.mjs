/**
 * Node.js 侧的库内混合检索自检（无 HTTP，直接走官方 seekdb SDK）。
 *
 * 用法：
 *   npm install
 *   node benchmark/selfcheck_seekdb_js.mjs
 *
 * 它会：建表（HNSW + IK 全文双索引）→ 写入 3 篇样例 → 执行单条
 * UNION ALL + GROUP BY RRF 的混合检索 SQL → 打印结果。
 */
import { pathToFileURL } from "node:url";
import {
  SEEKDB_CONFIG,
  TABLE_NAME,
  EMBEDDING_DIM,
  buildHybridSQL,
  vectorLiteral,
  ensureReady,
} from "../server_seekdb_js.mjs";
import { SeekdbClient } from "seekdb";

function unitVector(seed, secondary = -1, weight = 0.25) {
  const v = Array(EMBEDDING_DIM).fill(0.001);
  v[seed % EMBEDDING_DIM] = 1.0;
  if (secondary >= 0) v[secondary % EMBEDDING_DIM] = weight;
  const norm = Math.sqrt(v.reduce((a, x) => a + x * x, 0));
  return v.map((x) => Number((x / norm).toFixed(6)));
}

async function main() {
  await ensureReady();
  const client = new SeekdbClient({
    host: SEEKDB_CONFIG.host,
    port: SEEKDB_CONFIG.port,
    user: SEEKDB_CONFIG.user,
    password: SEEKDB_CONFIG.password,
    database: SEEKDB_CONFIG.database,
    charset: "utf8mb4",
  });

  const collection = "node_selfcheck";
  await client.execute(
    `DELETE FROM ${TABLE_NAME} WHERE COALESCE(collection_id, 'default') = ?`,
    [collection],
  );

  const docs = [
    ["SeekDB VSAG HNSW 向量索引", "SeekDB 内核集成 VSAG 向量引擎，支持余弦距离与 HNSW 近似最近邻检索。", "kernel", unitVector(10, 11)],
    ["浏览器 WebGPU ONNX 量化推理", "Transformers.js 在 WebGPU 与 WASM 运行 bge-small-zh INT8 量化模型。", "stepfun", unitVector(20, 21)],
    ["RRF 倒数排名融合", "用 1/(k+rank) 融合向量与全文两路召回，k 通常取 60。", "retrieval", unitVector(30, 31)],
  ];
  for (const [title, content, category, vec] of docs) {
    await client.execute(
      `INSERT INTO ${TABLE_NAME} (title, content, category, collection_id, embedding, quant_mode, device_source, client_ms)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [title, content, category, collection, vectorLiteral(vec), "q8", "selfcheck", 0],
    );
  }

  const queryVec = unitVector(10, 20, 0.6);
  const sql = buildHybridSQL(
    " WHERE COALESCE(collection_id, 'default') = ?",
    " WHERE COALESCE(collection_id, 'default') = ? AND MATCH(title, content) AGAINST (? IN NATURAL LANGUAGE MODE) > 0",
  );
  const rows = await client.execute(sql, [
    60, vectorLiteral(queryVec), collection, vectorLiteral(queryVec), 10,
    60, "WebGPU ONNX 量化 VSAG", collection, "WebGPU ONNX 量化 VSAG", 10,
    3,
  ]);

  console.log(`[selfcheck] seekdb ${SEEKDB_CONFIG.host}:${SEEKDB_CONFIG.port}/${SEEKDB_CONFIG.database}`);
  console.log(`[selfcheck] 单 SQL 库内混合检索返回 ${rows.length} 行：`);
  for (const r of rows) {
    console.log(`  - ${r.title} | rrf=${Number(r.rrf_score).toFixed(6)} | 通道=${r.channels_str}`);
  }
  await client.execute(
    `DELETE FROM ${TABLE_NAME} WHERE COALESCE(collection_id, 'default') = ?`,
    [collection],
  );
  await client.close();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main()
    .then(() => {
      // seekdb-js 的连接池句柄在 Windows 上不会自行释放事件循环，显式退出。
      process.exit(0);
    })
    .catch((err) => {
      console.error("[selfcheck] 失败：", err.message);
      console.error("请确认本地 seekdb 已启动（bash scripts/start_seekdb.sh），或设置 SEEKDB_HOST。");
      process.exit(1);
    });
}
