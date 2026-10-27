# PowerShell 启动本地 seekdb 实例（赛题要求的「随应用提供本地 server 的启动脚本与连接配置」）。
#
# 用法：
#   powershell -File scripts\start_seekdb.ps1           # 前台启动（Ctrl-C 停止）
#   powershell -File scripts\start_seekdb.ps1 -Daemon   # 后台启动
#   powershell -File scripts\start_seekdb.ps1 -Stop     # 停止由本脚本启动的实例

param(
    [switch]$Daemon,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Split-Path -Parent $ScriptDir

$BaseDir = if ($env:SEEKDB_BASE_DIR) { $env:SEEKDB_BASE_DIR } else { Join-Path $AppDir "seekdb_data" }
$DataDir = if ($env:SEEKDB_DATA_DIR) { $env:SEEKDB_DATA_DIR } else { Join-Path $BaseDir "store" }
$LogDir  = if ($env:SEEKDB_LOG_DIR)  { $env:SEEKDB_LOG_DIR }  else { Join-Path $BaseDir "log" }
$Port    = if ($env:SEEKDB_PORT)     { $env:SEEKDB_PORT }     else { "2881" }
$PidFile = Join-Path $BaseDir "seekdb.pid"

function Find-SeekDB {
    if ($env:SEEKDB_BIN -and (Test-Path $env:SEEKDB_BIN)) { return $env:SEEKDB_BIN }
    $cmd = Get-Command seekdb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

if ($Stop) {
    if (Test-Path $PidFile) {
        $savedPid = Get-Content $PidFile
        try {
            Stop-Process -Id $savedPid -Force -ErrorAction SilentlyContinue
            Write-Host "已停止 seekdb (pid=$savedPid)"
        } catch {
            Write-Host "进程可能已退出"
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "未找到运行中的 PID 文件 ($PidFile)"
    }
    exit 0
}

$Binary = Find-SeekDB
if (-not $Binary) {
    Write-Host @"
未在系统 PATH 中找到 seekdb。任选一种方式获取后重试：
  1) 使用已有的远程实例：$env:SEEKDB_HOST = '<host>'
  2) 指定本地安装路径：$env:SEEKDB_BIN = 'C:\path\to\seekdb.exe'
"@
    exit 1
}

New-Item -ItemType Directory -Force -Path $BaseDir, $DataDir, $LogDir | Out-Null
$StdoutLog = Join-Path $LogDir "server.stdout.log"

$ArgsList = @(
    "--nodaemon",
    "--base-dir", $BaseDir,
    "--data-dir", $DataDir,
    "--port", $Port,
    "--parameter", "datafile_size=512M",
    "--parameter", "log_disk_size=2G",
    "--parameter", "memory_limit=2G"
)

if ($Daemon) {
    $proc = Start-Process -FilePath $Binary -ArgumentList $ArgsList -PassThru -NoNewWindow -RedirectStandardOutput $StdoutLog -RedirectStandardError $StdoutLog
    $proc.Id | Out-File -FilePath $PidFile -Encoding ascii
    Write-Host "seekdb 已在后台启动 (pid=$($proc.Id), port=$Port, base_dir=$BaseDir)"
} else {
    Write-Host "启动 seekdb（前台）：$Binary"
    & $Binary @ArgsList
}
