# Build the self-contained GitDoc folder and its Inno Setup installer for Windows x64.
#   GITDOC_NATIVE_DIR      folder with prebuilt gitdoc-desktop.exe and gitdoc-pdf.exe (skips Cargo)
#   GITDOC_PYTHON_ARCHIVE  local copy of the pinned Python archive (skips the download)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location (Split-Path $PSScriptRoot -Parent)

$pythonArchive = "cpython-3.12.16+20261124-x86_64-pc-windows-msvc-install_only.tar.gz"
$pythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261124/cpython-3.12.16%2B20261124-x86_64-pc-windows-msvc-install_only.tar.gz"
$pythonSha256 = "8152ff5b6c6c044ddbbaa3531dda59cb9ce5bd6cb670d4c9ac2e3ab91afa8778"

function Check($message) { if ($LASTEXITCODE -ne 0) { throw $message } }

$project = "desktop\native"
$version = (Select-String -Path "$project\Cargo.toml" -Pattern '^version = "(.*)"$' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $version) { throw "version not found in $project\Cargo.toml" }
$stage = "dist\windows\GitDoc"
if ($env:GITDOC_DISTRIBUTION -eq 'true' -and [string]::IsNullOrWhiteSpace($env:GITDOC_WINDOWS_UPDATE_SIGNERS)) {
    throw "A stable Windows package must embed the approved Authenticode signer pin."
}

$native = $env:GITDOC_NATIVE_DIR
if (-not $native) {
    # Link the C runtime statically so the app starts without the Visual C++ redistributable.
    $env:RUSTFLAGS = "-C target-feature=+crt-static"
    cargo build --release --locked --target x86_64-pc-windows-msvc --manifest-path "$project\Cargo.toml"
    Check "cargo build failed"
    $target = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { "$project\target" }
    $native = "$target\x86_64-pc-windows-msvc\release"
}

Remove-Item -Recurse -Force "dist\windows" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\app\desktop", "dist\cache" | Out-Null
Copy-Item "$native\gitdoc-desktop.exe", "$native\gitdoc-pdf.exe" $stage
Copy-Item *.py, LICENSE "$stage\app"
Copy-Item desktop\*.py, desktop\windows-update.ps1 "$stage\app\desktop"
if ($env:GITDOC_WINDOWS_UPDATE_SIGNERS) {
    $pins = $env:GITDOC_WINDOWS_UPDATE_SIGNERS.Trim() -split '\s+'
    if ($pins.Count -gt 2 -or ($pins | Where-Object { $_ -cnotmatch '^[0-9a-f]{64}$' })) {
        throw "Windows update signer pins must be one or two lowercase SHA-256 certificate digests."
    }
    Set-Content -Path "$stage\app\WINDOWS_UPDATE_SIGNERS" -Value $pins -Encoding ascii
}
Copy-Item -Recurse templates, static "$stage\app"
Set-Content -Path "$stage\app\PACKAGED" -Value $version -Encoding ascii

$archive = if ($env:GITDOC_PYTHON_ARCHIVE) { $env:GITDOC_PYTHON_ARCHIVE } else { "dist\cache\$pythonArchive" }
if (-not (Test-Path $archive)) { Invoke-WebRequest -Uri $pythonUrl -OutFile $archive }
if ((Get-FileHash -Algorithm SHA256 $archive).Hash -ne $pythonSha256) { throw "checksum mismatch: $archive" }
& "$env:SystemRoot\System32\tar.exe" -xzf $archive -C $stage
Check "could not unpack $archive"
$runtime = "$stage\python"
$python = "$runtime\python.exe"
& $python -m pip install --disable-pip-version-check --no-cache-dir --no-warn-script-location `
    -r requirements.txt -r requirements-desktop.txt -r requirements-mcp.txt
Check "pip install failed"

# Drop what the engine never loads; installed entry scripts point at the build folder.
$library = "$runtime\Lib"
$unused = @("$library\test", "$library\idlelib", "$library\tkinter", "$library\turtledemo", "$library\ensurepip",
    "$library\site-packages\pip", "$runtime\tcl", "$runtime\Scripts", "$runtime\include", "$runtime\libs", "$runtime\share") +
    @(Get-ChildItem "$library\site-packages" -Directory -Filter "pip-*.dist-info" | ForEach-Object FullName) +
    @(Get-ChildItem "$runtime\DLLs" | Where-Object Name -Match '^(_tkinter|tcl|tk)' | ForEach-Object FullName)
$unused | Where-Object { Test-Path $_ } | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
# Hash-based bytecode stays valid after installation, so nothing is written into the install folder.
& $python -m compileall -q --invalidation-mode unchecked-hash "$stage\app" $library | Out-Null
$global:LASTEXITCODE = 0

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 (iscc) is required." }
$root = (Get-Location).Path
& $iscc /Qp "/DAppVersion=$version" "/DSourceDir=$root\$stage" "/DOutputDir=$root\dist" packaging\gitdoc.iss
Check "Inno Setup failed"
Write-Output "dist\GitDoc-$version-windows-x64-setup.exe"
