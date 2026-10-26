# 规有所依——面向软件持续演进的 SDD 上下文管理系统（运行说明）

> **赛题归属**：2026 OceanBase AI 数据库大赛 · MiniMax 赛题（20 分）
> **分支目录**：`minimax/`（`2026-competition-ecosystem` 分支）
> **验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、提交材料清单导航（对齐赛题第 3 节要求）

| 材料类别 | 对应文件路径 |
| :--- | :--- |
| **1. 完整源码** | [`sdd_engine.py`](./sdd_engine.py)、[`sdd_cli.py`](./sdd_cli.py)、[`server.py`](./server.py)、[`requirements.txt`](./requirements.txt)、[`.env.example`](./.env.example) |
| **2. 运行说明** | [`README.md`](./README.md)（本文档） |
| **3. 验证样例** | [`VALIDATION_SAMPLE.md`](./VALIDATION_SAMPLE.md)（任务描述、必备上下文来源、操作步骤与重置方法） |
| **4. 代码与测试** | 修改前 [`snapshots/before/gateway.py`](./snapshots/before/gateway.py)、修改后 [`snapshots/after/gateway.py`](./snapshots/after/gateway.py)（同步位于 [`target_project/gateway.py`](./target_project/gateway.py)）、差异补丁 [`snapshots/sdd_evolution.diff`](./snapshots/sdd_evolution.diff)、统一测试套件 [`target_project/test_gateway.py`](./target_project/test_gateway.py) |
| **5. 技术使用记录** | [`TECH_USAGE_LOG.md`](./TECH_USAGE_LOG.md)（MiniMax 参与开发记录、SeekDB 查询记录、PowerContext 状态复用记录、修改前后测试日志与退出码） |
| **6. 一页技术说明** | [`REPORT.md`](./REPORT.md)（模块结构、上下文选择方式与三项技术分工） |

---

## 二、如何准备环境与配置参数

### 1. 安装依赖
```bash
pip install -r minimax/requirements.txt
```

### 2. 配置环境变量（参照 `.env.example`）
```bash
export SEEKDB_HOST="100.72.34.104"
export SEEKDB_PORT="2881"
export SEEKDB_USER="root"
export SEEKDB_PASS=""
export SEEKDB_DB="powercontext"
export MINIMAX_API_KEY="your-minimax-api-key"
export MINIMAX_MODEL="MiniMax-M2.7"
```

---

## 三、如何初始化数据、启动模块、运行验证样例及恢复初始状态

### 1. 初始化或恢复初始状态（Reset）
```bash
cd minimax
python sdd_cli.py init --reset
```
- 该命令会清空 `minimax_sdd_context_store` 表并重新导入全部初始规格、代码位置映射、设计决策、已知陷阱与测试记录，确保从干净初始状态重复执行。

### 2. 启动 HTTP 服务模块（或直接使用 CLI）
```bash
cd minimax
python server.py
```
- 提供接口：
  - `GET /api/sdd/status`：初始化并查看 6 大维度统计
  - `POST /api/sdd/reset`：清空并恢复初始状态
  - `POST /api/sdd/query`：执行 5 类 SDD 查询与上下文装配
  - `POST /api/sdd/upsert`：动态更新并重新导入规格条目
  - `POST /api/sdd/state` & `GET /api/sdd/state/{task_id}`：通过 PowerContext 保存并跨实例重启读取任务状态
  - `POST /api/sdd/verify-target`：运行目标工程测试并回写结果

### 3. 一键运行完整闭环演示与自动化评测套件
```bash
# 运行 CLI 闭环演示
python minimax/sdd_cli.py demo

# 运行全套 pytest 必做项验收测试（覆盖全部 6 项通过标准）
pytest minimax/test_minimax_sdd.py minimax/target_project/test_gateway.py -v
```

OB-P:xI1C-CXPJXT4ztVpZDY6_A
