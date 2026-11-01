param([int]$Port = 8765, [switch]$Native)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ($Native) {
    $nativeBinary = "$projectRoot\native\target\debug\lemfolio-desktop.exe"
    if (-not (Test-Path -LiteralPath $nativeBinary)) { throw 'Build GPUI first: scripts/setup.ps1 -BuildNative' }
    & $nativeBinary --root $projectRoot --port $Port --data "$projectRoot\data"
} else {
    & "$projectRoot\.venv\Scripts\python.exe" -m lemfolio --port $Port --data "$projectRoot\data"
}
