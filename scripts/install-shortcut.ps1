# 在桌面创建「个人知识库」快捷方式（图标 + 双击启动）。
# 重复运行是安全的：同名快捷方式会被覆盖更新。
#
# 注意：本文件必须是「带 BOM 的 UTF-8」（原因同 start.ps1 头部注释）。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

# 图标文件名带版本号（pkos-desktop-icon-v2.ico）：Windows 按图标路径缓存，
# 换新图标时必须换文件名，否则桌面快捷方式会一直显示旧图标。
$ico = Join-Path $Root "assets\pkos-desktop-icon-v2.ico"
if (-not (Test-Path $ico)) {
    Write-Host "[XX] 找不到图标文件 $ico" -ForegroundColor Red
    return
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $Desktop "个人知识库.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = Join-Path $env:SystemRoot "System32\wscript.exe"
$lnk.Arguments = '"' + (Join-Path $Root "scripts\launch.vbs") + '"'
$lnk.WorkingDirectory = $Root
$lnk.IconLocation = "$ico,0"
$lnk.Description = "个人知识库 PersonalKnowledgeOS - 双击启动并打开页面"
$lnk.Save()

Write-Host "[OK] 桌面快捷方式已创建：$lnkPath" -ForegroundColor Green
