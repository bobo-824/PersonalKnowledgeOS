# Personal Knowledge OS 一键启动（桌面图标入口）。
# 已经在运行：直接打开浏览器页面；没在运行：后台拉起 start.ps1，
# 等服务就绪后再打开浏览器，避免打开一个连不上的空白页。
#
# 注意：本文件必须是「带 BOM 的 UTF-8」（原因同 start.ps1 头部注释，
# Windows PowerShell 5.1 对无 BOM 文件按 GBK 解码会解析失败）。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 端口与 start.ps1 / backend/config.py 同源：读 .env 里的 PKOS_PORT，默认 8000
$Port = 8000
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
        if ($key -eq "PKOS_PORT" -and $value) { $Port = [int]$value }
    }
}
$Url = "http://127.0.0.1:$Port"

function Test-AppReady {
    try {
        $resp = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (-not (Test-AppReady)) {
    # 服务没起来：用最小化窗口跑 start.ps1。
    # 故意不做成完全隐藏——任务栏那个小黑窗就是「关掉它 = 停止应用」的开关，
    # 对新手来说比去任务管理器找进程好懂得多。
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $Root "start.ps1"), "-NoBrowser"
    ) -WorkingDirectory $Root -WindowStyle Minimized

    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        if (Test-AppReady) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        $popup = New-Object -ComObject WScript.Shell
        $popup.Popup(
            "应用启动超时（60 秒内没有就绪）。" + [char]10 +
            "请看任务栏最小化的黑色窗口里报了什么错，" + [char]10 +
            "或者运行项目根目录的 start.ps1 看详细自检信息。",
            0, "个人知识库", 48) | Out-Null
        return
    }
}

Start-Process $Url
