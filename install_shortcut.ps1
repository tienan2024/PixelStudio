$ErrorActionPreference = 'Stop'
$pythonCommand = Get-Command python.exe -ErrorAction Stop
$pythonPath = (& $pythonCommand.Source -c 'import sys; print(sys.executable)').Trim()
$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) { throw 'Python 安装中没有 pythonw.exe。' }
$scriptPath = Join-Path $PSScriptRoot 'widget.py'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopPath '模型额度小组件.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if ($existing.Arguments -ne ('"' + $scriptPath + '"')) {
        throw '桌面存在同名但目标不同的快捷方式，请先自行改名。'
    }
}
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonwPath
$shortcut.Arguments = '"' + $scriptPath + '"'
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.Description = 'Codex 与 Kimi Code 额度桌面组件'
$shortcut.Save()
Write-Output $shortcutPath
