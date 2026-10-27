"""SeekDB 本地实例引导模块。

赛题要求「seekdb 使用嵌入式方式，或随应用提供本地 server 的启动脚本与连接配置」。
本模块负责按以下优先级为应用准备一个可用的 seekdb：

1. ``SEEKDB_HOST`` 显式指定（远程 / 已有本地服务）——直接使用，不做任何启动动作；
2. 本机 ``127.0.0.1:SEEKDB_PORT`` 已有可连接的 seekdb——直接复用；
3. 找到 seekdb 可执行文件（``SEEKDB_BIN`` 或 PATH）——拉起本地实例并等待端口就绪；
4. 都没有时给出明确的报错与获取方式，而不是静默失败。

所有路径、端口、资源参数都可以通过环境变量覆盖，见 ``config()``。
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


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def config() -> SeekDBConfig:
    """解析连接配置。

    默认值为本机 seekdb（``127.0.0.1:2881``），与 ``scripts/start_seekdb.sh``
    拉起的实例一致；设置 ``SEEKDB_HOST`` 可切换到任意远程实例。
    """
    explicit_host = os.getenv("SEEKDB_HOST")
    host = explicit_host or "127.0.0.1"
    mode = "local" if host in LOCAL_HOSTS else "remote"
    if explicit_host and host not in LOCAL_HOSTS:
        mode = "remote"

    base_dir = os.path.abspath(os.getenv("SEEKDB_BASE_DIR", os.path.join(os.path.dirname(__file__), "seekdb_data")))
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
    """用 MySQL 协议做一次真实握手，避免把「端口开着但不是 seekdb」误判为可用。"""
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
    return shutil.which("seekdb")


def _wait_ready(cfg: SeekDBConfig, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_open(cfg.host, cfg.port) and mysql_ping(cfg):
            return True
        time.sleep(0.5)
    return False


def start_local_seekdb(cfg: SeekDBConfig, wait_timeout_s: float = 120.0) -> tuple[bool, str]:
    """拉起本地 seekdb 实例。返回 (是否就绪, 说明信息)。"""
    binary = find_seekdb_binary()
    if binary is None:
        return False, (
            "未找到 seekdb 可执行文件。请先安装 seekdb（见 README「获取 seekdb」），"
            "或用 SEEKDB_BIN 指定路径，或用 SEEKDB_HOST 指向已有实例。"
        )

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

    deadline = time.time() + wait_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, f"seekdb 进程已退出（code={proc.returncode}），日志见 {stdout_path}"
        if port_open(cfg.host, cfg.port) and mysql_ping(cfg):
            return True, f"本地 seekdb 已就绪（pid={proc.pid}, {cfg.host}:{cfg.port}, base_dir={cfg.base_dir}）"
        time.sleep(0.5)

    return False, f"等待 seekdb 就绪超时（{wait_timeout_s:.0f}s），日志见 {stdout_path}"


def ensure_seekdb(cfg: Optional[SeekDBConfig] = None, auto_start: Optional[bool] = None) -> tuple[SeekDBConfig, str]:
    """确保有一个可连接的 seekdb，返回 (配置, 状态说明)。

    远程模式下只做连通性检查，不会尝试启动任何进程。
    """
    cfg = cfg or config()
    if auto_start is None:
        auto_start = _env_flag("SEEKDB_AUTO_START", True)

    if cfg.mode == "remote":
        ok = mysql_ping(cfg)
        return cfg, f"使用远程 seekdb {cfg.host}:{cfg.port}（{'连接正常' if ok else '当前不可达'}）"

    if mysql_ping(cfg):
        return cfg, f"复用已有本地 seekdb {cfg.host}:{cfg.port}"

    if not auto_start:
        return cfg, f"本地 seekdb {cfg.host}:{cfg.port} 未运行，且 SEEKDB_AUTO_START=0"

    ok, message = start_local_seekdb(cfg)
    return cfg, message


def main() -> int:
    """命令行入口：``python seekdb_bootstrap.py`` 用于手动准备数据库。"""
    cfg, message = ensure_seekdb()
    print(message)
    if not mysql_ping(cfg):
        print("seekdb 当前不可用。", file=sys.stderr)
        return 1
    print(f"可用：{cfg.host}:{cfg.port} database={cfg.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
