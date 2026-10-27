# 启动本地 seekdb 实例（Windows / PowerShell 版本）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/start_seekdb.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/start_seekdb.ps1 -Daemon
#   powershell -ExecutionPolicy Bypass -File scripts/start_seekdb.ps1 -Stop
#
# 查找 seekdb 可执行文件的顺序：$env:SEEKDB_BIN → PATH 中的 seekdb(.exe) → 仓库内编译产物。
param(
    [switch]$Daemon,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Split-Path -Parent $ScriptDir

$BaseDir = if ($env:SEEKDB_BASE_DIR) { $env:SEEKDB_BASE_DIR } else { Join-Path $AppDir "seekdb_data" }
$DataDir = if ($env:SEEKDB_DATA_DIR) { $env:SEEKDB_DATA_DIR } else { Join-Path $BaseDir "store" }
$LogDir = if ($env:SEEKDB_LOG_DIR) { $env:SEEKDB_LOG_DIR } else { Join-Path $BaseDir "log" }
$Port = if ($env:SEEKDB_PORT) { $env:SEEKDB_PORT } else { "2881" }
$PidFile = Join-Path $BaseDir "seekdb.pid"

function Find-Seekdb {
    if ($env:SEEKDB_BIN -and (Test-Path $env:SEEKDB_BIN)) { return $env:SEEKDB_BIN }
    $cmd = Get-Command seekdb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            (Join-Path $AppDir "..\seekdb\build_release\src\observer\seekdb.exe"),
            (Join-Path $AppDir "..\seekdb\build_release\src\observer\seekdb"))) {
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }
    return $null
}

function Stop-Instance {
    if ((Test-Path $PidFile)) {
        $procId = Get-Content $PidFile
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $procId -Force
            Write-Host "已停止 seekdb (pid=$procId)"
        }
        Remove-Item $PidFile -Force
    }
    else {
        Write-Host "没有由本脚本启动的 seekdb 实例（$PidFile 不存在）"
    }
}

if ($Stop) {
    Stop-Instance
    exit 0
}

$SeekdbBin = Find-Seekdb
if (-not $SeekdbBin) {
    Write-Host @"
未找到 seekdb 可执行文件。任选一种方式获取后重试：

  1) 官方发行包（Linux）：https://github.com/oceanbase/seekdb/releases
  2) 从源码编译：git clone https://github.com/oceanbase/seekdb; cd seekdb; bash build.sh
  3) 已在别处安装：`$env:SEEKDB_BIN = "C:\path\to\seekdb.exe"
  4) 使用已有远程实例：`$env:SEEKDB_HOST = "<host>"，无需本地启动。
"@
    exit 1
}

New-Item -ItemType Directory -Force -Path $BaseDir, $DataDir, $LogDir | Out-Null

$CommonArgs = @(
    "--nodaemon",
    "--base-dir", $BaseDir,
    "--data-dir", $DataDir,
    "--port", $Port,
    "--parameter", "datafile_size=$($env:SEEKDB_DATAFILE_SIZE ?? '512M')",
    "--parameter", "log_disk_size=$($env:SEEKDB_LOG_DISK_SIZE ?? '2G')",
    "--parameter", "memory_limit=$($env:SEEKDB_MEMORY_LIMIT ?? '2G')"
)

if ($Daemon) {
    $proc = Start-Process -FilePath $SeekdbBin -ArgumentList $CommonArgs `
        -RedirectStandardOutput (Join-Path $LogDir "server.stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "server.stderr.log") `
        -PassThru -WindowStyle Hidden
    Set-Content -Path $PidFile -Value $proc.Id
    Write-Host "seekdb 已在后台启动 (pid=$($proc.Id), port=$Port, base_dir=$BaseDir)"
    Write-Host "日志：$LogDir\server.stdout.log"
}
else {
    Write-Host "启动 seekdb（前台）：$SeekdbBin"
    Write-Host "  base_dir=$BaseDir  data_dir=$DataDir  port=$Port"
    & $SeekdbBin @CommonArgs
}
