# Serve this checkout's sources on a separate port with isolated data, for frontend
# acceptance. It never touches the main service or its papers.
param([int]$Port = 8791, [string]$Data = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $Data) { $Data = Join-Path $projectRoot '.test-data\frontend-acceptance' }
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    # A linked worktree shares the main checkout's environment and overrides the source path.
    $common = (git rev-parse --path-format=absolute --git-common-dir).Trim()
    $python = Join-Path (Split-Path -Parent $common) '.venv\Scripts\python.exe'
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'
if (-not $env:LEMFOLIO_DIFF_BINARY) {
    $common = (git rev-parse --path-format=absolute --git-common-dir).Trim()
    $diff = Join-Path (Split-Path -Parent $common) 'target\diff\release\lemfolio-diff.exe'
    if (Test-Path -LiteralPath $diff) { $env:LEMFOLIO_DIFF_BINARY = $diff }
}
& $python -m lemfolio --port $Port --data $Data
