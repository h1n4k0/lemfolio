$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& "$projectRoot\.venv\Scripts\python.exe" scripts/generate_contracts.py --check
if ($LASTEXITCODE -ne 0) { throw 'Generated contract drift' }
& "$projectRoot\.venv\Scripts\python.exe" scripts/generate_openapi.py --check
if ($LASTEXITCODE -ne 0) { throw 'OpenAPI contract drift' }
npm run check
if ($LASTEXITCODE -ne 0) { throw 'TypeScript type check failed' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'React bundle build failed' }
npm run test:web
if ($LASTEXITCODE -ne 0) { throw 'React behavior regressions failed' }
$testParent = Join-Path $projectRoot '.test-data'
New-Item -ItemType Directory -Path $testParent -Force | Out-Null
$pytestBase = [IO.Path]::GetFullPath((Join-Path $testParent ('pytest-check-' + [guid]::NewGuid().ToString('N'))))
$resolvedTestParent = [IO.Path]::GetFullPath($testParent).TrimEnd('\') + '\'
if (-not $pytestBase.StartsWith($resolvedTestParent,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $pytestBase)) { throw 'Refused an existing or out-of-workspace pytest base directory' }
& "$projectRoot\.venv\Scripts\python.exe" -m pytest --basetemp $pytestBase -q
if ($LASTEXITCODE -ne 0) { throw 'Python/functional tests failed' }
cargo test --locked --manifest-path rust/lemfolio-diff/Cargo.toml --target-dir target/diff
if ($LASTEXITCODE -ne 0) { throw 'Rust Diff tests failed' }
Push-Location -LiteralPath "$projectRoot\native"
try {
    cargo test --locked
    if ($LASTEXITCODE -ne 0) { throw 'GPUI native contract tests failed' }
} finally { Pop-Location }
