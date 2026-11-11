# 在桌面创建「个人知识库」快捷方式：双击 = 后台起服务 + 弹出独立应用窗口。
# 重复运行是安全的：同名快捷方式会被覆盖更新。
# 项目搬家、重建了 .venv、或者不小心删掉图标之后，重新跑一次这个脚本就行。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1
#
# 注意：本文件必须是「带 BOM 的 UTF-8」（原因同 start.ps1 头部注释，
# Windows PowerShell 5.1 对无 BOM 文件按 GBK 解码会解析失败）。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

$launcher = Join-Path $Root "desktop_launcher.py"
if (-not (Test-Path $launcher)) { throw "找不到启动器 $launcher" }

# 启动器用 pythonw.exe 跑（没有黑窗口），而它又用同一个 .venv 的解释器去起服务，
# 所以这里直接指向项目自己的虚拟环境，不去 PATH 上碰运气：
# 全局 Python 没装本项目依赖，双击只会报「找不到模块」。
$pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "找不到 $pythonw。请先在项目目录运行：python scripts\setup.py"
}

# 图标文件名带版本号（pkos-desktop-icon-v2.ico）：Windows 按图标路径缓存，
# 换新图标时必须换文件名，否则桌面快捷方式会一直显示旧图标。
$ico = Join-Path $Root "assets\pkos-desktop-icon-v2.ico"
if (-not (Test-Path $ico)) {
    Write-Warning "没有 $ico，图标会退回默认样式。"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $Desktop "个人知识库.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = '"' + $launcher + '"'
$lnk.WorkingDirectory = $Root
if (Test-Path $ico) { $lnk.IconLocation = "$ico,0" }
$lnk.Description = "双击打开个人知识库窗口；关掉窗口，后台服务自动停止。"
$lnk.Save()

Write-Host "[OK] 桌面快捷方式已创建：$lnkPath" -ForegroundColor Green
Write-Host "     启动器解释器：$pythonw"