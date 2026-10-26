// 构建公开发布目录 dist/。
// site/engine.js 由 functions/engine.mjs 生成，corpus.json 为语料唯一数据源，
// 保证浏览器侧与 Function 侧共用同一份引擎实现和同一份语料。
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

copyFileSync(resolve(root, 'functions/engine.mjs'), resolve(root, 'site/engine.js'));
copyFileSync(resolve(root, 'functions/corpus.json'), resolve(root, 'site/corpus.json'));
mkdirSync(resolve(root, 'dist'), { recursive: true });
copyFileSync(resolve(root, 'site/index.html'), resolve(root, 'dist/index.html'));
copyFileSync(resolve(root, 'site/engine.js'), resolve(root, 'dist/engine.js'));
copyFileSync(resolve(root, 'site/corpus.json'), resolve(root, 'dist/corpus.json'));

console.log('built dist/index.html + dist/engine.js + dist/corpus.json');
