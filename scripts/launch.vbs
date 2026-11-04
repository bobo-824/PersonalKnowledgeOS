' 双击桌面快捷方式真正执行的东西：用 wscript 拉起 launch-app.ps1，
' 这样启动器本身完全无窗口（.lnk 直接指向 powershell 会闪一下黑框）。
' VBS 是 ANSI 读取，本文件只写 ASCII 字符，不要加中文。

Option Explicit
Dim fso, shell, scriptPath, root, command
Set fso = CreateObject("Scripting.FileSystemObject")
scriptPath = WScript.ScriptFullName
root = fso.GetParentFolderName(fso.GetParentFolderName(scriptPath))
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = root
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & root & "\scripts\launch-app.ps1"""
shell.Run command, 0, False
