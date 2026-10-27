import json, os, sys

docs = [
    # 1. SeekDB 内核
    ("doc-01", "SeekDB 定位与嵌入式部署", "kernel", "SeekDB 是 OceanBase 推出的轻量级 AI 原生数据库，单二进制交付，支持嵌入式与本地 server 两种运行形态，默认监听 2881 端口并使用 MySQL 协议接入。"),
    ("doc-02", "VSAG HNSW 向量索引", "kernel", "SeekDB 内核集成 VSAG 向量引擎，支持 HNSW 图索引与余弦距离，通过 VECTOR INDEX 语法在建表时声明，查询使用 COSINE_DISTANCE 配合 APPROXIMATE 走近似最近邻。"),
    ("doc-03", "Delta HNSW 与 Snapshot HNSW", "kernel", "SeekDB 的向量索引分为内存中的增量 Delta HNSW 与持久化的基线 Snapshot HNSW，写入提交后先进入 Delta，后台异步下刷合并，查询时并行读取两路索引归并结果。"),
    ("doc-04", "IK 中文分词全文索引", "kernel", "SeekDB 的 FULLTEXT 索引支持 WITH PARSER ik 使用 IK 中文分词器，配合 MATCH ... AGAINST 自然语言模式做 BM25 关键词检索，适合中文技术文档。"),
    ("doc-05", "单阶段标量过滤下推", "kernel", "SeekDB 在优化器阶段将 WHERE 标量条件与向量距离计算合并下推，避免先取近邻再过滤导致 TopK 被过滤空的经典问题。"),
    ("doc-06", "Fork Diff Merge 数据工作流", "kernel", "SeekDB 提供 FORK DATABASE 与 FORK TABLE 的写时复制沙箱，Agent 可以在隔离分支上试写，再通过 Diff 比较和 Merge 采纳，无需复制全量数据。"),
    ("doc-07", "seekdb-js 官方 SDK", "kernel", "seekdb-js 是 SeekDB 的官方 JavaScript/TypeScript SDK，现以 npm 包名 seekdb 发布，提供 SeekdbClient 的 execute 方法执行原生 SQL，并支持服务端与嵌入式两种模式。"),
    ("doc-08", "seekdb 资源参数", "kernel", "在内存受限环境启动 SeekDB 需要显式设置 datafile_size、log_disk_size 与 memory_limit 三个参数，否则默认资源需求过高会报 OB_RESOURCE_UNIT_VALUE_INVALID。"),

    # 2. 向量检索与混合检索
    ("doc-09", "余弦距离与归一化", "retrieval", "余弦相似度衡量两个向量方向的一致性，Embedding 模型通常输出归一化向量，此时余弦距离与内积排序等价，距离越小表示语义越接近。"),
    ("doc-10", "RRF 倒数排名融合", "retrieval", "Reciprocal Rank Fusion 用 1/(k+rank) 对各路召回的名次求和，k 常取 60，可以融合向量检索与全文检索两路结果而不需要分数归一化。"),
    ("doc-11", "混合检索的动机", "retrieval", "纯语义检索容易漏掉专有名词与型号，纯关键词检索缺乏语义泛化能力，混合检索同时利用两路信号并通过融合排序提升召回与排序质量。"),
    ("doc-12", "库内单 SQL 混合检索", "retrieval", "赛题要求混合检索由数据库完成，不得在客户端分别获取两路结果后自行拼合，因此需要用一条 SQL 通过 UNION ALL 与 GROUP BY 在库内完成融合。"),
    ("doc-13", "Recall@K 指标", "retrieval", "Recall@K 表示前 K 个检索结果中覆盖了多少比例的相关文档，是检索质量评估中最直观的指标，常与人工标注的相关集配合使用。"),
    ("doc-14", "nDCG@K 指标", "retrieval", "nDCG@K 对排序位置做对数折扣并归一化，越相关的结果排在越前面得分越高，适合衡量排序质量而不仅是命中与否。"),
    ("doc-15", "候选集扩召回", "retrieval", "混合检索通常先按各自通道各取 top_k 的三倍作为候选，再融合排序截断到最终 K 条，扩大候选能降低单路召回不足带来的质量损失。"),
    ("doc-16", "索引刷新与可见性", "retrieval", "新写入的向量进入 Delta 索引后即可被检索到，但持久化与合并发生在后台，刷新接口用于确认索引状态与文档计数是否一致。"),

    # 3. 端侧推理
    ("doc-17", "Transformers.js 浏览器推理", "edge", "Transformers.js 基于 ONNX Runtime Web 在浏览器中运行 Transformer 模型，通过 pipeline 接口加载 feature-extraction 任务即可在本地生成句向量，无需任何云端接口。"),
    ("doc-18", "WebGPU 后端", "edge", "WebGPU 通过 navigator.gpu.requestAdapter 获取显卡适配器，把矩阵乘法与归一化算子交给 GPU 执行，显著提升端侧 Embedding 推理速度。"),
    ("doc-19", "WASM 降级路径", "edge", "当浏览器不支持 WebGPU 或适配器请求失败时，应自动降级到 WebAssembly 后端继续推理，并在界面明确提示当前使用的推理后端。"),
    ("doc-20", "ONNX 量化精度", "edge", "ONNX 模型可以导出为 fp32、fp16 与 INT8 量化三种精度，INT8 动态量化把权重压缩到 8 位，体积约为 fp32 的四分之一，精度损失通常在可接受范围内。"),
    ("doc-21", "bge-small-zh 模型", "edge", "Xenova/bge-small-zh-v1.5 是 BAAI bge 系列中文小模型在 HuggingFace 上的 ONNX 版本，输出 512 维向量，适合在浏览器端侧运行。"),
    ("doc-22", "量化体积与压缩比", "edge", "压缩比按量化前模型文件总体积除以量化后体积计算，fp16 约为 2 倍，INT8 量化约为 4 倍，比较时必须在相同统计口径下进行。"),
    ("doc-23", "首次加载与预热", "edge", "模型首次加载需要下载权重并编译执行图，耗时远高于后续请求，性能报告必须区分冷加载与预热后的稳定延迟，并重复测量取中位数。"),
    ("doc-24", "批处理向量化", "edge", "端侧批量向量化比逐条调用吞吐更高，但浏览器主线程与显存占用也会上升，需要在批大小与延迟之间权衡。"),

    # 4. 上下文工程
    ("doc-25", "Source 证据源", "context", "PowerContext 把用户输入、代码快照和对话日志统一采集为 Source 证据源，保留原始内容与来源元数据，作为后续记忆提炼的唯一依据。"),
    ("doc-26", "Candidate 审核晋升", "context", "模型提炼出的记忆必须先进入 Candidate 候选池，经过人工或策略审核批准后才能晋升为 Artifact，防止幻觉污染长效记忆。"),
    ("doc-27", "Memory 与 retire", "context", "Memory 表示跨任务持久存在的事实、决定与约束，当来源被撤回时可以 retire 停用并保留痕迹，而不是直接物理删除。"),
    ("doc-28", "Handoff 交接包", "context", "Handoff 是临时任务接力包，包含当前目标、已验证事实、阻塞项、下一步与证据引用，接收方按小尺寸 Token 预算无缝接手任务。"),
    ("doc-29", "PreparedContext 装配", "context", "PreparedContext 按 Token 预算与余弦距离从记忆中裁剪装配上下文，并附上可回查的证据引用，保证模型看到的内容可追溯。"),
    ("doc-30", "Scope 租户隔离", "context", "Scope 用于隔离不同租户或不同业务场景的 Source 与 Memory，检索时必须限制在调用方指定的数据范围内，避免跨租户污染。"),
    ("doc-31", "上下文增量传输", "context", "上下文增量传输通过前缀复用协议只发送变化部分，接收端重建出与完整传输一致的上下文，从而降低净传输字节数。"),
    ("doc-32", "证据页检索", "context", "财报 PDF 等长文档需要按物理页码检索证据，返回的引用必须能沿 MemoryCitation 回查到原文与来源，评测会逐条核对。"),

    # 5. 工程实践
    ("doc-33", "可复现性要求", "engineering", "赛题要求按提交说明可以启动应用与数据库并完成全部操作，因此必须提供依赖清单、数据库启动脚本与连接配置，不能依赖未交付的本地状态。"),
    ("doc-34", "依赖清单与虚拟环境", "engineering", "Python 项目应提供 requirements.txt 并使用虚拟环境安装依赖，锁定 FastAPI、uvicorn、pymysql 与 pytest 等版本，避免评测环境缺包。"),
    ("doc-35", "pytest 同名模块冲突", "engineering", "多个子目录下存在同名模块时，从仓库根统一收集测试会命中错误文件，需要在测试文件内把本目录插到 sys.path 首位并清理 sys.modules 缓存。"),
    ("doc-36", "服务端不可用时的降级", "engineering", "数据库不可达时接口应返回明确的 503 与排查提示，而不是抛出未捕获异常导致整个请求栈崩溃。"),
    ("doc-37", "环境变量配置", "engineering", "连接地址、端口、数据库名与启动参数都应通过环境变量覆盖，并提供 .env.example 作为配置示例，不提交任何真实凭据。"),
    ("doc-38", "原始数据与测试日志", "engineering", "性能报告应保留原始测量数据与测试脚本，说明设备、数据规模与计时范围，并注明结果来自提交应用的实际运行。"),
    ("doc-39", "禁用云端推理", "engineering", "赛题明确禁止调用云端推理接口，也禁止预先在服务端算好向量后回放，模型的下载与本地加载不影响推理位置的合规性。"),
    ("doc-40", "端到端延迟拆分", "engineering", "端到端查询延迟应拆分为查询向量化、网络往返、数据库检索与结果渲染几个阶段分别计时，才能定位瓶颈并说明优化效果。"),

    # 6. 生态赛题
    ("doc-41", "阶跃星辰赛题要求", "competition", "阶跃星辰赛题要求构建浏览器本地完成向量化、使用 seekdb 做语义与混合检索的迷你知识库，提交可运行 Web 应用、完整源码与一页技术说明。"),
    ("doc-42", "WebGPU 优先与降级提示", "competition", "赛题要求优先使用 WebGPU 并显式配置推理后端，运行环境不支持时自动降级到 WASM，并在界面上明确提示当前后端。"),
    ("doc-43", "量化报告要求", "competition", "赛题要求对 embedding 模型采用 INT8 或 FP16 方案，说明处理方式并报告量化前后的模型体积、压缩比与检索质量变化。"),
    ("doc-44", "检索质量评测方法", "competition", "检索质量应在相同评测集上对比量化前后表现，使用 Recall@K 或 nDCG@K 等指标并说明变化幅度，不能仅凭主观展示判断。"),
    ("doc-45", "增量导入与集合切换", "competition", "赛题要求管理知识库的增量写入、集合与会话状态，保证连续导入和切换操作后的检索结果与当前知识库一致。"),
    ("doc-46", "索引刷新后可见", "competition", "赛题要求正确处理数据写入和索引刷新，确保新导入的文档在刷新完成后能够被检索到，评测会写入新文档后立即查询。"),
    ("doc-47", "高德赛题与 Skill 提交", "competition", "高德地图赛题只需向提测平台上传一个 SKILL.md，写明名称用途、工具调用步骤与回答格式，运行环境与 API Key 由平台配置。"),
    ("doc-48", "提测平台与执行失败", "competition", "大赛提测平台提供在线提交、自动评测与结果反馈，网络或运行器故障会记为执行失败而不是内容评分，可修正后重新提测。"),
]

queries = [
    ("q-01", "SeekDB 的向量索引用的是哪个引擎？", ["doc-02"]),
    ("q-02", "增量索引和基线索引是怎么配合的？", ["doc-03"]),
    ("q-03", "中文全文检索用什么分词器？", ["doc-04"]),
    ("q-04", "标量条件和向量距离怎么一起下推？", ["doc-05"]),
    ("q-05", "数据库的 Fork 沙箱是怎么工作的？", ["doc-06"]),
    ("q-06", "官方 JavaScript SDK 的包名和入口类是什么？", ["doc-07"]),
    ("q-07", "内存小的机器启动数据库要设置哪些参数？", ["doc-08"]),
    ("q-08", "RRF 融合公式里的 k 一般取多少？", ["doc-10"]),
    ("q-09", "为什么要同时做语义检索和关键词检索？", ["doc-11"]),
    ("q-10", "混合检索为什么必须在数据库内完成？", ["doc-12"]),
    ("q-11", "Recall@K 是怎么定义的？", ["doc-13"]),
    ("q-12", "nDCG 怎么计算排序质量？", ["doc-14"]),
    ("q-13", "候选集为什么要扩大到三倍？", ["doc-15"]),
    ("q-14", "Transformers.js 在浏览器里怎么跑 embedding？", ["doc-17"]),
    ("q-15", "怎么获取 WebGPU 的显卡适配器？", ["doc-18"]),
    ("q-16", "没有 WebGPU 的时候怎么降级？", ["doc-19"]),
    ("q-17", "INT8 量化的模型体积大概是多少？", ["doc-20"]),
    ("q-18", "bge-small-zh 输出多少维向量？", ["doc-21"]),
    ("q-19", "压缩比应该怎么统计？", ["doc-22"]),
    ("q-20", "冷加载和预热后的延迟为什么要分开报告？", ["doc-23"]),
    ("q-21", "PowerContext 里的 Candidate 是什么？", ["doc-26"]),
    ("q-22", "Handoff 交接包里包含哪些内容？", ["doc-28"]),
    ("q-23", "上下文是怎么按预算裁剪装配的？", ["doc-29"]),
    ("q-24", "多个子目录同名模块跑 pytest 会出什么问题？", ["doc-35"]),
    ("q-25", "数据库连不上时接口应该返回什么？", ["doc-36"]),
    ("q-26", "赛题对量化报告有哪些要求？", ["doc-43"]),
    ("q-27", "检索质量应该用什么指标衡量？", ["doc-44"]),
    ("q-28", "写入新文档后多久能被检索到？", ["doc-46"]),
    ("q-29", "部署参数应该怎么配置才能复现？", ["doc-33", "doc-37"]),
    ("q-30", "端到端延迟怎么拆阶段计时？", ["doc-40"]),
]

out = {
    "model": "Xenova/bge-small-zh-v1.5",
    "dimension": 512,
    "documents": [
        {"id": i, "title": t, "category": c, "content": x} for i, t, c, x in docs
    ],
    "queries": [
        {"id": i, "text": t, "relevant": rel} for i, t, rel in queries
    ],
}

path = os.path.join(os.path.dirname(__file__), "corpus.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"wrote {path}: {len(docs)} docs, {len(queries)} queries")
