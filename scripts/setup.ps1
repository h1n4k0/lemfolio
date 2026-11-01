param([switch]$BuildTex, [switch]$BuildNative)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath "$projectRoot\.venv\Scripts\python.exe")) {
    python -m venv "$projectRoot\.venv"
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& "$projectRoot\.venv\Scripts\python.exe" -m pip install -r requirements.lock.txt -e '.[test]'
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
npm ci
if ($LASTEXITCODE -ne 0) { throw 'TypeScript dependency installation failed' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'TypeScript build failed' }
cargo build --release --manifest-path rust/lemfolio-diff/Cargo.toml --target-dir target/diff
if ($LASTEXITCODE -ne 0) { throw 'Rust Diff build failed' }
if ($BuildTex) {
    docker build -f Dockerfile.tex -t lemfolio/tex:0.1 .
    if ($LASTEXITCODE -ne 0) { throw 'TeX image build failed' }
}
if ($BuildNative) {
    Push-Location -LiteralPath "$projectRoot\native"
    try {
        cargo build --locked
        if ($LASTEXITCODE -ne 0) { throw 'GPUI build failed' }
    } finally { Pop-Location }
}
