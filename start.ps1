<#
.SYNOPSIS
    Personal Knowledge OS 启动脚本。

.DESCRIPTION
    默认（单端口模式）：启动一个后端进程，同时提供 API 和前端页面，
    浏览器访问 http://127.0.0.1:8000 即可，不需要额外开 Vite。

    -Dev（开发模式）：额外启动 Vite 开发服务器，改前端代码自动刷新，
    浏览器访问 http://127.0.0.1:5173，/api 请求由 Vite 代理到后端。

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Dev
    .\start.ps1 -NoBrowser
    .\start.ps1 -SkipDoctor
#>

[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$NoBrowser,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"

# 注意：本文件必须是「带 BOM 的 UTF-8」。Windows PowerShell 5.1 对没有 BOM 的文件
# 按 ANSI(GBK) 解码，中文的多字节序列会把后面的引号或大括号一起吃掉，导致解析失败。
# 另外 PowerShell 把中文智能引号 U+201C/U+201D 当作字符串定界符，字符串里不要用它们，
# 改用方括号或「」，否则字符串会被截断。
# 子进程统一 UTF-8，避免中文 Windows 的 GBK 控制台把中文输出变成乱码
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# 让 PowerShell 正确解码 python 通过管道返回的 UTF-8 文本（doctor --json 要用）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location $Root

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Fail {
    param([string]$Message, [string]$Hint)
    Write-Host ""
    Write-Host "[XX] $Message" -ForegroundColor Red
    if ($Hint) { Write-Host "     怎么办：$Hint" -ForegroundColor Yellow }
    Write-Host ""
}

# --------------------------------------------------------------------------
# 读取 .env 里的端口。不硬编码，与 backend/config.py 保持一致。
# --------------------------------------------------------------------------
function Read-PkosConfig {
    $config = @{ Port = 8000; Host = "127.0.0.1"; FrontendPort = 5173 }
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            $trimmed = $line.Trim()
            if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
            $idx = $trimmed.IndexOf("=")
            if ($idx -lt 1) { continue }
            $key = $trimmed.Substring(0, $idx).Trim()
            $value = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
            switch ($key) {
                "PKOS_PORT"           { if ($value) { $config.Port = [int]$value } }
                "PKOS_HOST"           { if ($value) { $config.Host = $value } }
                "PKOS_FRONTEND_PORT"  { if ($value) { $config.FrontendPort = [int]$value } }
            }
        }
    }
    return $config
}

$cfg = Read-PkosConfig

# --------------------------------------------------------------------------
# npm 路径解析
# --------------------------------------------------------------------------
# 这台机器上 Get-Command npm 解析到的是 npm.ps1（一个 PowerShell 脚本），
# Start-Process 无法执行它，会报 [%1 is not a valid Win32 application]。
# 实测四种写法里只有 npm.cmd 和 cmd /c 能正常拉起 Vite，所以统一解析到 npm.cmd。
$script:NpmPath = $null
function Get-NpmPath {
    if ($script:NpmPath) { return $script:NpmPath }
    $found = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Fail "找不到 npm.cmd" "安装 Node.js LTS: https://nodejs.org/ ，装完重开 PowerShell 让 PATH 生效"
        exit 1
    }
    $script:NpmPath = $found.Source
    return $script:NpmPath
}

# --------------------------------------------------------------------------
# 1. 环境自检
# --------------------------------------------------------------------------
if (-not $SkipDoctor) {
    Write-Step "环境自检（想跳过请加 -SkipDoctor）"
    $doctorJson = & python (Join-Path $Root "scripts\doctor.py") --json 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
        Write-Fail "自检脚本本身运行失败" "手动执行看报错：python scripts\doctor.py"
        Write-Host $doctorJson
        exit 1
    }
    try {
        $report = $doctorJson | ConvertFrom-Json
    } catch {
        Write-Fail "无法解析自检结果" "手动执行看报错：python scripts\doctor.py"
        Write-Host $doctorJson
        exit 1
    }

    $problems = @($report.results | Where-Object { $_.status -eq "FAIL" })
    $warnings = @($report.results | Where-Object { $_.status -eq "WARN" })

    foreach ($item in $report.results) {
        $marker = switch ($item.status) {
            "PASS" { "[OK]" }
            "WARN" { "[!!]" }
            default { "[XX]" }
        }
        $color = switch ($item.status) {
            "PASS" { "Green" }
            "WARN" { "Yellow" }
            default { "Red" }
        }
        Write-Host ("{0}  {1}" -f $marker, $item.name) -NoNewline -ForegroundColor $color
        Write-Host ("  {0}" -f $item.detail)
        if ($item.hint) {
            Write-Host ("          怎么办：{0}" -f $item.hint) -ForegroundColor DarkYellow
        }
    }

    if ($problems.Count -gt 0) {
        Write-Fail "有 $($problems.Count) 项必须先解决的问题" "按上面的[怎么办]提示处理后，重新运行 .\start.ps1"
        exit 1
    }
    Write-Host ""
    Write-Host "自检通过（$($warnings.Count) 项警告，不影响启动）" -ForegroundColor Green
}

# --------------------------------------------------------------------------
# 2. 数据库迁移
# --------------------------------------------------------------------------
Write-Step "检查数据库迁移"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Fail "找不到 uv 命令" 'PowerShell 执行：powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    exit 1
}
& uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Fail "数据库迁移失败" "先运行 python scripts\setup.py 完成安装；或看上面的报错定位问题"
    exit 1
}
Write-Host "[OK] 数据库结构已是最新" -ForegroundColor Green

# --------------------------------------------------------------------------
# 3. 前端构建产物（单端口模式需要）
# --------------------------------------------------------------------------
$distIndex = Join-Path $Root "frontend\dist\index.html"
if (-not $Dev -and -not (Test-Path $distIndex)) {
    Write-Step "前端还没有构建，现在构建（只影响首次启动）"
    Push-Location (Join-Path $Root "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            & (Get-NpmPath) ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" }
        }
        & (Get-NpmPath) run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build 失败" }
    } catch {
        Pop-Location
        Write-Fail "前端构建失败：$_" "可以改用开发模式启动：.\start.ps1 -Dev（不需要构建）"
        exit 1
    }
    Pop-Location
    Write-Host "[OK] 前端构建完成" -ForegroundColor Green
}

# --------------------------------------------------------------------------
# 4. 启动服务
# --------------------------------------------------------------------------
$backendUrl = "http://$($cfg.Host):$($cfg.Port)"
$viteUrl = "http://$($cfg.Host):$($cfg.FrontendPort)"
$viteProcess = $null

try {
    if ($Dev) {
        Write-Step "启动 Vite 开发服务器（端口 $($cfg.FrontendPort)）"
        $viteProcess = Start-Process -FilePath (Get-NpmPath) -ArgumentList "run", "dev" `
            -WorkingDirectory (Join-Path $Root "frontend") -PassThru -NoNewWindow

        Write-Step "启动后端（端口 $($cfg.Port)，热重载已开启）"
        Write-Host "    浏览器地址：$viteUrl" -ForegroundColor Magenta
        Write-Host "    改后端代码会自动重启，改前端代码会自动刷新。" -ForegroundColor DarkGray
        Write-Host "    按 Ctrl+C 停止（会自动关掉 Vite）。" -ForegroundColor DarkGray
        Write-Host ""

        if (-not $NoBrowser) {
            # 等 Vite 起来再开浏览器，否则会打开一个连不上的页面
            for ($i = 0; $i -lt 40; $i++) {
                $client = New-Object System.Net.Sockets.TcpClient
                try {
                    $client.Connect($cfg.Host, $cfg.FrontendPort)
                    if ($client.Connected) { break }
                } catch { } finally { $client.Close() }
                Start-Sleep -Milliseconds 500
            }
            Start-Process $viteUrl
        }

        & uv run uvicorn backend.main:app --host $cfg.Host --port $cfg.Port --reload
    } else {
        Write-Step "启动应用（单端口模式）"
        Write-Host "    浏览器地址：$backendUrl" -ForegroundColor Magenta
        Write-Host "    这一个进程同时提供页面和 API。按 Ctrl+C 停止。" -ForegroundColor DarkGray
        Write-Host ""

        if (-not $NoBrowser) {
            for ($i = 0; $i -lt 40; $i++) {
                $client = New-Object System.Net.Sockets.TcpClient
                try {
                    $client.Connect($cfg.Host, $cfg.Port)
                    if ($client.Connected) { break }
                } catch { } finally { $client.Close() }
                Start-Sleep -Milliseconds 500
            }
            Start-Process $backendUrl
        }

        & uv run uvicorn backend.main:app --host $cfg.Host --port $cfg.Port
    }
} finally {
    if ($viteProcess -and -not $viteProcess.HasExited) {
        Write-Host ""
        Write-Host "==> 正在关闭 Vite 开发服务器…" -ForegroundColor Cyan
        # npm 会派生子进程，只 Kill 父进程会留下孤儿 node 进程继续占着端口
        & taskkill /PID $viteProcess.Id /T /F 2>&1 | Out-Null
    }
}
