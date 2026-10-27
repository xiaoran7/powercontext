"""SeekDB 本地实例引导与连接管理模块（MiniMax SDD 上下文系统）。

为确保评测机干净环境与本地开发自闭环运行，本模块按以下优先级准备可用的 seekdb：
1. 若环境变量显式指定 SEEKDB_HOST（如连接远程实例），直接测试连通性；
2. 本机 127.0.0.1:SEEKDB_PORT 已有可连接的 seekdb，直接复用；
3. 本机未运行但存在 seekdb 可执行文件（SEEKDB_BIN 或 PATH），自动拉起本地实例；
4. 确保目标 Database（默认 powercontext）存在，并在不可用时输出明确指引。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

DEFAULT_PORT = 2881
DEFAULT_DATABASE = "powercontext"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass
class SeekDBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    mode: str  # "remote" | "local"
    base_dir: str
    data_dir: str
    log_dir: str


_CACHED_HOST: Optional[str] = None


def config() -> SeekDBConfig:
    """解析连接配置。智能探测优先级：
    1. 环境变量 SEEKDB_HOST 显式指定；
    2. 当前进程已成功探测并缓存的 host；
    3. 本地 127.0.0.1:2881 端口存活（标准评测与本地运行）；
    4. 远端 100.72.34.104:2881 存活（开发环境 Tailscale）；
    5. 默认回退 127.0.0.1:2881。
    """
    global _CACHED_HOST
    port = int(os.getenv("SEEKDB_PORT", str(DEFAULT_PORT)))
    explicit_host = os.getenv("SEEKDB_HOST")
    if explicit_host:
        host = explicit_host
    elif _CACHED_HOST and port_open(_CACHED_HOST, port, timeout=1.0):
        host = _CACHED_HOST
    else:
        if port_open("127.0.0.1", port, timeout=0.2):
            host = "127.0.0.1"
        elif port_open("100.72.34.104", port, timeout=1.5):
            host = "100.72.34.104"
        else:
            host = "127.0.0.1"
        _CACHED_HOST = host

    mode = "remote" if host not in LOCAL_HOSTS else "local"

    base_dir = os.path.abspath(
        os.getenv("SEEKDB_BASE_DIR", os.path.join(os.path.dirname(__file__), "seekdb_data"))
    )
    return SeekDBConfig(
        host=host,
        port=int(os.getenv("SEEKDB_PORT", str(DEFAULT_PORT))),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASS", ""),
        database=os.getenv("SEEKDB_DB", DEFAULT_DATABASE),
        mode=mode,
        base_dir=base_dir,
        data_dir=os.path.abspath(os.getenv("SEEKDB_DATA_DIR", os.path.join(base_dir, "store"))),
        log_dir=os.path.abspath(os.getenv("SEEKDB_LOG_DIR", os.path.join(base_dir, "log"))),
    )


def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def mysql_ping(cfg: SeekDBConfig, timeout: int = 3) -> bool:
    try:
        import pymysql

        conn = pymysql.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
        )
        conn.close()
        return True
    except Exception:
        return False


def find_seekdb_binary() -> Optional[str]:
    explicit = os.getenv("SEEKDB_BIN")
    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) else None

    # 常用候选路径
    candidates = [
        shutil.which("seekdb"),
        "/usr/local/bin/seekdb",
        "/usr/bin/seekdb",
        os.path.expanduser("~/seekdb/build_release/src/observer/seekdb"),
        "/d/Claudespace/Oceanbase/seekdb/build_release/src/observer/seekdb",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def ensure_database_exists(cfg: SeekDBConfig) -> None:
    """确保目标数据库存在"""
    import pymysql

    conn = pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` DEFAULT CHARACTER SET utf8mb4")
    finally:
        conn.close()


def ensure_seekdb(cfg: Optional[SeekDBConfig] = None) -> tuple[SeekDBConfig, str]:
    """确保 seekdb 可连接并创建目标库"""
    cfg = cfg or config()

    # 1. 如果已可连通
    if mysql_ping(cfg):
        ensure_database_exists(cfg)
        return cfg, f"复用可用 seekdb: {cfg.host}:{cfg.port} (db: {cfg.database})"

    # 2. 如果指定远程不可连通
    if cfg.mode == "remote":
        return cfg, f"远程 seekdb {cfg.host}:{cfg.port} 当前不可达，请检查网络或配置"

    # 3. 尝试拉起本地 seekdb
    binary = find_seekdb_binary()
    if not binary:
        return cfg, f"本地 seekdb 未运行在 {cfg.host}:{cfg.port}，且未在系统路径中找到 seekdb 二进制文件"

    os.makedirs(cfg.base_dir, exist_ok=True)
    os.makedirs(cfg.log_dir, exist_ok=True)
    stdout_path = os.path.join(cfg.log_dir, "server.stdout.log")
    cmd = [
        binary,
        "--nodaemon",
        "--base-dir",
        cfg.base_dir,
        "--data-dir",
        cfg.data_dir,
        "--port",
        str(cfg.port),
        "--parameter",
        "datafile_size=512M",
        "--parameter",
        "log_disk_size=2G",
        "--parameter",
        "memory_limit=2G",
    ]

    with open(stdout_path, "ab") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)

    deadline = time.time() + 60.0
    while time.time() < deadline:
        if proc.poll() is not None:
            return cfg, f"seekdb 进程启动后退出 (code={proc.returncode})"
        if port_open(cfg.host, cfg.port) and mysql_ping(cfg):
            ensure_database_exists(cfg)
            return cfg, f"成功启动本地 seekdb (pid={proc.pid}, {cfg.host}:{cfg.port})"
        time.sleep(0.5)

    return cfg, "等待本地 seekdb 启动超时"
