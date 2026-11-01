param(
    [Parameter(Mandatory = $true)][string]$Python,   # a relocatable CPython 3.11 folder, such as one from `uv python install 3.11`
    [string]$Iscc                                    # ISCC.exe from Inno Setup 6; without it the build stops at dist\stage
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$version = [regex]::Match((Get-Content -Raw native\Cargo.toml), '(?m)^version = "([^"]+)"').Groups[1].Value
if (-not $version) { throw 'No version in native/Cargo.toml' }

cargo build --release --locked --manifest-path native/Cargo.toml
if ($LASTEXITCODE -ne 0) { throw 'GPUI build failed' }
cargo build --release --manifest-path rust/lemfolio-diff/Cargo.toml --target-dir target/diff
if ($LASTEXITCODE -ne 0) { throw 'Rust Diff build failed' }

$stage = "$projectRoot\dist\stage"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force "$stage\target\diff\release", "$stage\contracts\v1" | Out-Null

# The installed folder has the same shape as the repository, so the service finds its parts the same way.
Copy-Item native\target\release\lemfolio-desktop.exe $stage
Copy-Item target\diff\release\lemfolio-diff.exe "$stage\target\diff\release"
Copy-Item contracts\v1\schema.json "$stage\contracts\v1"
Copy-Item -Recurse src\lemfolio "$stage\src\lemfolio" -Exclude __pycache__
Copy-Item -Recurse web "$stage\web"
Copy-Item LICENSE.md, NOTICE.md, Dockerfile.tex $stage
Copy-Item scripts\installer\build-tex-image.cmd $stage

# robocopy reports success with exit codes below 8
robocopy $Python "$stage\runtime" /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ include libs tcl Scripts test idlelib tkinter ensurepip site-packages /XF '*.lib' '*.pdb' /XF pythonw.exe | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the Python runtime failed: $LASTEXITCODE" }
$LASTEXITCODE = 0
$site = "$stage\runtime\Lib\site-packages"
New-Item -ItemType Directory -Force $site | Out-Null
$requirements = "$projectRoot\dist\requirements.runtime.txt"
Get-Content requirements.lock.txt | Where-Object { $_ -notmatch '^(pytest|pluggy|iniconfig|Pygments)==' } | Set-Content -Encoding ascii $requirements
& "$Python\python.exe" -m pip install --no-deps --no-compile --target $site -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
Remove-Item -LiteralPath "$site\bin" -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $site -Recurse -Directory -Filter tests | Remove-Item -Recurse -Force

if (-not $Iscc) { Write-Output "Stage ready: $stage"; return }
& $Iscc "/DAppVersion=$version" "/O$projectRoot\dist" scripts\installer\lemfolio.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
Get-Item "$projectRoot\dist\Lemfolio-$version-Setup-x64.exe"
