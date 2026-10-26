"""
OceanBase 2026 大赛专用专业语料库 (包含精准函数名、版本号、架构术语与实战代码样本)
用于深度验证 Hybrid Search (SeekDB Vector + BM25) 与 Rerank 精排的优势。
"""

SAMPLE_CORPUS = [
    {
        "id": 1,
        "title": "SeekDB 核心定位与 vsag 向量加速引擎",
        "category": "kernel",
        "source": "official_docs",
        "content": "SeekDB 是 OceanBase 推出的单二进制嵌入式 AI 数据库（最新版本 v1.4.0.0），专为端侧与单机 Agent 设计。内核深度集成 vsag 向量引擎，原生支持 Delta HNSW 与 Snapshot HNSW 双层索引结构，并在单阶段完成标量谓词过滤与向量距离计算。"
    },
    {
        "id": 2,
        "title": "AMD ROCm 批量向量检索接口 dbms_vector.batch_knn",
        "category": "amd",
        "source": "official_docs",
        "content": "在 AMD ROCm GPU 加速环境下，严禁在应用层使用线性 for 循环单条调用向量检索。必须使用专用内置存储过程 dbms_vector.batch_knn(query_vectors, top_k, metric) 进行批处理计算，以此规避多次网络 RPC 开销并最大化利用 HIP 算力核心。"
    },
    {
        "id": 3,
        "title": "Dify 混合检索（Hybrid Search）满分流水线配置规范",
        "category": "dify",
        "source": "official_docs",
        "content": "根据 2026 OceanBase 大赛评卷标准，Dify 知识库检索节点必须配置混合检索模式 method: hybrid_search。第一阶段粗排 top_k: 20 结合向量相似度与 BM25Okapi 关键词打分；第二阶段精排挂载 rerank_model 进行 Cross-Encoder 语义交叉打分，截取 top_n: 5。"
    },
    {
        "id": 4,
        "title": "Dify 外部知识库 API 标准协议规范 (POST /retrieval)",
        "category": "dify",
        "source": "official_docs",
        "content": "Dify 外部知识库对接遵循统一的 RESTful 协议：请求方法为 POST /retrieval，Payload 包含 knowledge_id, query 与 retrieval_setting (包含 top_k 和 score_threshold)。响应数据包含 records 数组，每条记录携带 title, content, score 及自定义 metadata。"
    },
    {
        "id": 5,
        "title": "阶跃星辰赛道 6 大硬性死线与 WebGPU 降级要求",
        "category": "stepfun",
        "source": "official_docs",
        "content": "阶跃星辰赛道禁止任何预计算回放与伪装端侧的欺骗行为。文本 Embedding 必须在用户浏览器本地通过 WebGPU 运行开源模型（如 Xenova/bge-small-zh-v1.5，512 维）。当客户端缺少 WebGPU 环境时，系统必须自动无缝降级至 WASM (WebAssembly) 多线程 CPU 推理。"
    },
    {
        "id": 6,
        "title": "PowerContext 状态机五大核心概念定义",
        "category": "state_machine",
        "source": "official_docs",
        "content": "PowerContext 具备严谨的状态机体系：Scope 保证多租户与任务幂等；Source 负责不可变原始证据存证；Memory 固化原子长期知识；PreparedContext 在 token 窗口内动态装配截断上下文；Handoff 生成附带严格证据引用的跨 Agent 移交制品。"
    },
    {
        "id": 7,
        "title": "高德空间智能 MCP Server 与 SeekDB 空间混合选址",
        "category": "gaode",
        "source": "official_docs",
        "content": "高德地图空间智能赛题中，Agent 通过 MCP (Model Context Protocol) 协议调用高德开放平台接口（如望京 SOHO、未来科技城周围 3 公里 POI 搜索与客流统计），再将空间地理属性与语义标签写入 SeekDB 进行多维打分，最终生成商业选址建议。"
    },
    {
        "id": 8,
        "title": "原力灵机具身智能轨迹数据质量四项风控指标",
        "category": "robotics",
        "source": "official_docs",
        "content": "原力灵机机器人数据工程的核心准则是'先判断样本质量，再决定是否入库'。四大核心风控指标分别为：hand_rate (手部关键点覆盖率需达 1.00)、tracking_ok_rate (追踪稳定度需达 1.00)、unreachable_ratio (末端不可达比率) 以及 obs_outlier_ratio (观测异常值比率)。"
    },
    {
        "id": 9,
        "title": "SeekDB 单阶段过滤检索（Single-Stage Filtered Search）原理解析",
        "category": "kernel",
        "source": "official_docs",
        "content": "传统向量数据库通常采用先标量过滤后向量比对（Pre-filtering）或先向量召回后标量剔除（Post-filtering），容易导致检索效率崩塌或召回数不足。SeekDB vsag 引擎将标量 WHERE 条件直接下推到 HNSW 图遍历内部，边走图边过滤，实现单阶段高效过滤。"
    },
    {
        "id": 10,
        "title": "OceanBase 独立版本 seekdb 兼容性模式探针 patch 说明",
        "category": "kernel",
        "source": "code_samples",
        "content": "在 Standalone / 嵌入式 seekdb 1.4.0 中，SHOW VARIABLES LIKE 'ob_compatibility_mode' 查询为空。官方驱动需要检查 SELECT @@version 是否包含 seekdb 字符，以此将其确认为原生 MySQL 兼容模式，从而消除 UnsupportedOceanBaseTenantError 报错。"
    },
    {
        "id": 11,
        "title": "BM25Okapi 关键词检索算法原理与参数调优",
        "category": "algorithm",
        "source": "official_docs",
        "content": "BM25 算法是信息检索的黄金标准，通过词频 (TF) 与逆文档频率 (IDF) 结合文档长度惩罚计算相关度。核心公式包含参数 k1 (词频饱和度，通常设为 1.5) 和 b (文档长度归一化因子，通常设为 0.75)。在面对专有名词、代码符号时表现极为优越。"
    },
    {
        "id": 12,
        "title": "倒数排名融合算法（RRF - Reciprocal Rank Fusion）公式与实现",
        "category": "algorithm",
        "source": "official_docs",
        "content": "RRF (Reciprocal Rank Fusion) 是一种无监督的混合排名算法。对于文档 d，其得分为不同检索系统排名的倒数加权和：Score(d) = sum( weight_i / (k + rank_i(d)) )。常数 k 通常设置为 60，能有效平滑不同打分系统的绝对分值差异并突出重合高排位文档。"
    },
    {
        "id": 13,
        "title": "Cross-Encoder 重排模型与 Bi-Encoder 双塔模型差异分析",
        "category": "algorithm",
        "source": "official_docs",
        "content": "双塔模型（Bi-Encoder）分别独立编码 Query 与 Document，生成向量后通过余弦相似度计算，速度极快但缺乏深层交叉注意力交互；交叉编码器（Cross-Encoder / Reranker）将 Query 与 Document 拼接送入 Transformer 计算全局全注意力，精度极高但计算开销大，故适合放在第二阶段精排。"
    },
    {
        "id": 14,
        "title": "Dify 沙箱执行安全规范与容器网络隔离红线",
        "category": "dify",
        "source": "official_docs",
        "content": "在 Dify 企业级生产与比赛评测部署中，Dify Sandbox 沙箱环境严禁赋予 root 特权，严禁挂载宿主机敏感目录（如 /etc, /var/run/docker.sock）。沙箱网络必须与核心生产业务网段严格隔离，禁止访问内网敏感服务以防代码注入。"
    },
    {
        "id": 15,
        "title": "SeekDB 向量索引创建语法与距离度量标准",
        "category": "kernel",
        "source": "code_samples",
        "content": "在 SeekDB 中为 VECTOR(512) 字段建立 HNSW 向量索引的标准 DDL 为：CREATE VECTOR INDEX idx_name ON table_name (embedding_col) WITH (distance=cosine, type=hnsw, lib=vsag); 支持的距离度量包含 cosine (余弦距离), l2 (欧式距离) 以及 ip (内积)。"
    },
    {
        "id": 16,
        "title": "SeekDB 文件虚拟外部表 (File SQL) 快速导入技术",
        "category": "kernel",
        "source": "code_samples",
        "content": "SeekDB 支持通过 File SQL 将外部 CSV 或 Parquet 格式数据映射为虚拟表：CREATE EXTERNAL TABLE ext_docs (id INT, text_data VARCHAR(1000)) LOCATION = 'file:///data/docs/' FORMAT = (TYPE = 'CSV'); Agent 可直接对外部文件执行 SQL 联合查询与特征提取。"
    },
    {
        "id": 17,
        "title": "MiniMax 大模型可观测性与全链路 SQL 审计规范",
        "category": "competition",
        "source": "official_docs",
        "content": "MiniMax 生态赛道强调 Agent 决策的可观测性与追溯性。系统必须记录每一轮思考过程、下发的具体 SQL 语句、数据库返回结果 Diff 差分、以及最终生成的 Markdown 格式决策审计报告，禁止无法溯源的黑盒操作。"
    },
    {
        "id": 18,
        "title": "SeekDB Delta HNSW 与 Snapshot HNSW 读写分离机制",
        "category": "kernel",
        "source": "official_docs",
        "content": "SeekDB 创新性地结合了内存动态索引 Delta HNSW 与持久化静态索引 Snapshot HNSW。新插入的向量即时写入 Delta HNSW，保证写入立即可查（零等待）；后台异步压缩合并至 Snapshot HNSW，消除大规模更新对内存与查询性能的影响。"
    },
    {
        "id": 19,
        "title": "混合检索常见失败场景：纯向量漏词与纯关键词缺语义",
        "category": "algorithm",
        "source": "community_qa",
        "content": "在检索'dbms_vector.batch_knn 错误码 1064'时，纯向量模型往往只理解'数据库报错'的概念语义，却把包含该精确函数的关键文档排在几十名开外；而纯 BM25 在检索'如何加速批量向量比对'时，由于完全没有出现相同汉字而直接零召回。混合检索是唯一解法。"
    },
    {
        "id": 20,
        "title": "Dify 混合检索权重分配推荐策略与最佳实践",
        "category": "dify",
        "source": "official_docs",
        "content": "在专业技术文档与知识库场景下，推荐设置向量权重 (Vector Weight) 0.6 ~ 0.7，BM25 权重 0.3 ~ 0.4；在法律条文、代码 API 等强精确匹配场景下，推荐将 BM25 权重提升至 0.5 以上，以保障精确编号与符号的绝对召回。"
    }
]
