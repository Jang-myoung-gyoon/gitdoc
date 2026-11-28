# Executed from the installed updater with fixed code and data-only environment variables.
$ErrorActionPreference = "Stop"
$setup = [IO.Path]::GetFullPath($env:GITDOC_UPDATE_SETUP)
$stage = [IO.Path]::GetFullPath($env:GITDOC_UPDATE_STAGE)
$install = [IO.Path]::GetFullPath($env:GITDOC_UPDATE_PATH)
$application = Join-Path $install "gitdoc-desktop.exe"
$components = @("app", "python", "gitdoc-desktop.exe", "gitdoc-pdf.exe")

function Assert-Installer {
    $signature = Get-AuthenticodeSignature -LiteralPath $setup
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
        throw "The installer lacks a trusted Authenticode signature."
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $actual = [BitConverter]::ToString($sha.ComputeHash($signature.SignerCertificate.RawData)).Replace("-", "").ToLowerInvariant()
    } finally { $sha.Dispose() }
    if (($env:GITDOC_UPDATE_SIGNERS -split ',') -cnotcontains $actual) {
        throw "The installer signer is not pinned by this GitDoc version."
    }
    if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash -ine $env:GITDOC_UPDATE_SHA256) {
        throw "The installer no longer matches the approved release."
    }
}

if ($env:GITDOC_UPDATE_MODE -eq "verify") {
    try { Assert-Installer; exit 0 } catch { exit 1 }
}
if ($env:GITDOC_UPDATE_MODE -ne "apply") { exit 1 }
if ([IO.Path]::GetDirectoryName($setup) -ine $stage -or [IO.Path]::GetFileName($setup) -ne 'GitDoc-setup.exe') { exit 1 }

$backup = Join-Path $stage "backup"
$keepBackup = $false
$installed = $false
$parent = Get-Process -Id ([int]$env:GITDOC_UPDATE_PARENT) -ErrorAction SilentlyContinue
if ($parent -and $parent.Path -ine $application) { exit 1 }
if ($parent -and !$parent.WaitForExit(30000)) {
    Set-Location $env:TEMP
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
$mutex = [Threading.Mutex]::new($false, 'Local\GitDocUpdater')
if (!$mutex.WaitOne(0)) {
    $mutex.Dispose()
    Set-Location $env:TEMP
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
$changed = $false
try {
    # An installed Python bridge can briefly outlive the native window while it closes stdin.
    $bridge = Join-Path $install "python\python.exe"
    foreach ($worker in (Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $bridge })) {
        if (!$worker.WaitForExit(5000)) { throw "The document engine is still running." }
    }
    if ((Get-Process -Name gitdoc-desktop -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $application })) {
        throw "Close other GitDoc windows and retry the update."
    }
    # Open the approved file read-only and deny writes or replacement through installer launch.
    $stream = [IO.File]::Open($setup, 'Open', 'Read', 'Read')
    try {
        Assert-Installer
        New-Item -ItemType Directory -Path $backup | Out-Null
        foreach ($name in $components) {
            Copy-Item -LiteralPath (Join-Path $install $name) -Destination $backup -Recurse -Force
        }
        $process = Start-Process -FilePath $setup -ArgumentList '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', "/DIR=`"$install`"" -PassThru
        $changed = $true
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Setup exited with code $($process.ExitCode)." }
        $installed = $true
    } finally { $stream.Dispose() }
} catch {
    if ($changed) {
        try {
            foreach ($name in $components) {
                $target = Join-Path $install $name
                if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
                Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $install -Recurse -Force
            }
        } catch { $keepBackup = $true }
    }
    if ($keepBackup) {
        Add-Type -AssemblyName System.Windows.Forms
        [void][Windows.Forms.MessageBox]::Show(
            ("업데이트 복구를 완료하지 못했습니다. 복구 파일: " + $backup), "GitDoc")
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
    Set-Location $env:TEMP
    if (!$keepBackup) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        if ((Test-Path -LiteralPath $application)
            -and !(Get-Process -Name gitdoc-desktop -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $application })) {
            $env:GITDOC_UPDATE_RESTART_STATUS = if ($installed) { "installed" } else { "failed" }
            Start-Process -FilePath $application -WorkingDirectory $install
        }
    }
}
