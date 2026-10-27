# 规有所依——面向软件持续演进的 SDD 上下文管理系统（运行说明）

> **赛题归属**：2026 OceanBase AI 数据库大赛 · MiniMax 赛题（大题 600077 / 子题 800514，满分 20 分）
> **分支目录**：`minimax/`（`2026-competition-ecosystem` 分支）
> **评测验证行**：`OB-P:xI1C-CXPJXT4ztVpZDY6_A`

---

## 一、提交材料清单导航（严格对齐赛题第 3 节交付要求）

| 材料类别 | 对应文件路径 | 关键说明 |
| :--- | :--- | :--- |
| **1. 完整源码** | [`sdd_engine.py`](./sdd_engine.py)、[`sdd_cli.py`](./sdd_cli.py)、[`server.py`](./server.py)、[`seekdb_bootstrap.py`](./seekdb_bootstrap.py)、[`requirements.txt`](./requirements.txt)、[`.env.example`](./.env.example) | 涵盖 SDD 6 大维度建模、单 SQL 混合检索、PowerContext 制品装配与 MiniMax 协同修改 |
| **2. 运行说明** | [`README.md`](./README.md)（本文档） | 依赖安装、数据库引导、API 接口、CLI 命令、测试命令与自闭环指引 |
| **3. 验证样例** | [`VALIDATION_SAMPLE.md`](./VALIDATION_SAMPLE.md) | 明确“改什么”与“哪些原有行为必须保持”、必备上下文来源、逐步验证与重置操作 |
| **4. 代码与测试** | [`snapshots/before/gateway.py`](./snapshots/before/gateway.py)、[`snapshots/after/gateway.py`](./snapshots/after/gateway.py)、[`snapshots/sdd_evolution.diff`](./snapshots/sdd_evolution.diff)、[`target_project/test_gateway.py`](./target_project/test_gateway.py) | 目标网关工程修改前后代码快照、完整差异补丁与一致性测试套件 |
| **5. 技术使用记录** | [`TECH_USAGE_LOG.md`](./TECH_USAGE_LOG.md) | MiniMax 开发记录、SeekDB 混合查询记录、PowerContext 状态保存与复用记录、修改前后测试运行日志与退出码 |
| **6. 一页技术说明** | [`REPORT.md`](./REPORT.md) | 系统四层架构图、三级上下文筛选策略与三项技术职责分工表 |

---

## 二、环境依赖与数据库准备

### 1. 安装依赖
```bash
cd minimax
pip install -r requirements.txt
```
> 注：本工程依托当前代码仓库，已通过内置逻辑自动链接根目录的 `powercontext` 核心制品协议，无需额外从外部网络安装。

### 2. 准备 SeekDB 数据库（支持本地一键拉起或现有实例）
本系统遵循赛题「随应用提供本地 server 的启动脚本与连接配置」规范，内置高可用引导器：

- **方式 A（自动引导，推荐）**：
  直接运行 CLI 或测试，系统会自动检测 `127.0.0.1:2881`。若未检测到运行中的进程，将自动从系统 PATH 或常用安装路径尝试拉起本地 seekdb 并自动创建 `powercontext` 数据库。
- **方式 B（使用随附脚本启动）**：
  ```bash
  # Linux / WSL 环境
  bash scripts/start_seekdb.sh --daemon

  # Windows PowerShell 环境
  powershell -File scripts\start_seekdb.ps1 -Daemon
  ```
- **方式 C（连接已有远程或特定端口实例）**：
  参照 `.env.example` 配置环境变量即可：
  ```bash
  export SEEKDB_HOST="127.0.0.1"
  export SEEKDB_PORT="2881"
  export SEEKDB_USER="root"
  export SEEKDB_PASS=""
  export SEEKDB_DB="powercontext"
  ```

### 3. 可选配置 MiniMax 凭据（未配置时自动采用确定性演进模型）
```bash
export MINIMAX_API_KEY="your-minimax-api-key"
export MINIMAX_MODEL="MiniMax-M2.7"
```

---

## 三、数据初始化、启动服务与端到端闭环验证

### 1. 初始化或恢复初始状态（Reset）
```bash
cd minimax
python sdd_cli.py init --reset
```
- 该命令会清空 `minimax_sdd_context_store` 表并重新导入全部 6 大维度的初始规格、代码位置映射、设计决策、已知防线与测试记录，确保在干净状态下可重复执行。

### 2. 查询并装配开发任务上下文（定位规格、代码、测试与状态）
```bash
python sdd_cli.py query --type 5 --target SPEC-GW-02 --task-id TASK-GW-102
```
- 输出符合标准 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 信封的上下文，准确包含：
  1. 适用规格条目（`SPEC-GW-02`）；
  2. 需要修改的代码文件与符号（`target_project/gateway.py` / `TokenBucketGateway.record_upstream_outcome`）；
  3. 用于验证功能及兼容性的测试（`test_circuit_breaker_state_transitions`、`test_regression_basic_token_consumption`）；
  4. 已完成事项、待办事项和已有验证结果（复用持久化状态）。

### 3. 执行 MiniMax 代码修改助手并自动完成验证
```bash
python sdd_cli.py apply --task-id TASK-GW-102 --target SPEC-GW-02
```
- 将上下文投递给 MiniMax 助手，将修改写入 `target_project/gateway.py`，自动执行全套测试门禁并更新持久化任务状态。

### 4. 启动 HTTP 服务模块
```bash
python server.py
```
- 核心接口清单：
  - `GET /api/sdd/status`：自检 SeekDB 连通性并查看 6 大维度统计
  - `POST /api/sdd/reset`：清空并恢复干净初始状态
  - `POST /api/sdd/query`：执行 5 类 SDD 检索与 PreparedContext 字节预算装配
  - `POST /api/sdd/upsert`：动态更新规格并重新导入
  - `POST /api/sdd/state` & `GET /api/sdd/state/{task_id}`：通过 PowerContext 跨进程重启持久化与复用任务状态
  - `POST /api/sdd/apply`：调用 MiniMax 完成代码修改、落地补丁与测试验收
  - `POST /api/sdd/verify-target`：执行目标工程测试门禁并回写时间戳至 SeekDB

### 5. 一键执行完整闭环演示与全量自动化验收套件
```bash
# 1. 运行 CLI 闭环全流程演示
python sdd_cli.py demo

# 2. 运行全量 pytest 必做项验收测试（严格覆盖 6 项官方通过标准）
pytest test_minimax_sdd.py target_project/test_gateway.py -v
```

---

OB-P:xI1C-CXPJXT4ztVpZDY6_A
