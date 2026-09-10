# Create a Desktop shortcut that launches Live Agent View with NO visible
# terminal: it targets pythonw.exe (windowless Python), which runs start.py
# (which frees the port, starts the server as a windowless child, and opens
# the browser). Prefers the local .venv if one exists.
#
#   powershell -ExecutionPolicy Bypass -File install_shortcut.ps1
#
# Stop it later from the web page's stop button, or end "pythonw.exe" in
# Task Manager.

$here = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Resolve a windowless python: venv first, then whatever is on PATH.
$venvPw = Join-Path $here ".venv\Scripts\pythonw.exe"
if (Test-Path $venvPw) {
    $pythonw = $venvPw
} else {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $pythonw = $cmd.Source } else { $pythonw = "pythonw.exe" }
}

$startPy = Join-Path $here "start.py"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Live Agent View.lnk"

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = '"' + $startPy + '"'
$lnk.WorkingDirectory = $here
$lnk.IconLocation = "shell32.dll,13"
$lnk.Description = "Live Agent View - brain agent monitor (localhost)"
$lnk.Save()

Write-Host "Created shortcut: $lnkPath"
Write-Host "Target: $pythonw `"$startPy`""
Write-Host "Double-click it to launch (no terminal). Stop from the web page or Task Manager (pythonw.exe)."
