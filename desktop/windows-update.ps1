# Executed from the installed updater with fixed code and data-only environment variables.
$ErrorActionPreference = "Stop"
$setup = [IO.Path]::GetFullPath($env:GITDOC_UPDATE_SETUP)
$stage = [IO.Path]::GetFullPath($env:GITDOC_UPDATE_STAGE)
$install = [IO.Path]::GetFullPath($env:GITDOC_UPDATE_PATH)
$application = Join-Path $install "gitdoc-desktop.exe"
$components = @("app", "python", "gitdoc-desktop.exe", "gitdoc-pdf.exe")
$registration = @(
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6A2E4C0B-3C0F-4F6E-9B57-7D1B6C1F5E21}_is1",
    "HKCU\Software\Classes\gitdoc"
)
$reg = Join-Path $env:SystemRoot "System32\reg.exe"

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
        $uninstallFiles = @(Get-ChildItem -LiteralPath $install -File | Where-Object { $_.Name -match '^unins[0-9]+\.(exe|dat|msg)$' })
        foreach ($name in $components + @($uninstallFiles.Name)) {
            Copy-Item -LiteralPath (Join-Path $install $name) -Destination $backup -Recurse -Force
        }
        for ($index = 0; $index -lt $registration.Count; $index++) {
            $key = $registration[$index]
            if (Test-Path ($key -replace '^HKCU', 'HKCU:')) {
                & $reg export $key (Join-Path $backup "registry-$index.reg") /y | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Cannot back up installer registration." }
            }
        }
        $process = Start-Process -FilePath $setup -Verb Open -ArgumentList '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', "/DIR=`"$install`"" -PassThru
        $changed = $true
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Setup exited with code $($process.ExitCode)." }
        $installed = $true
    } finally { $stream.Dispose() }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($changed) {
        try {
            foreach ($name in $components) {
                $target = Join-Path $install $name
                if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
                Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $install -Recurse -Force
            }
            Get-ChildItem -LiteralPath $install -File |
                Where-Object { $_.Name -match '^unins[0-9]+\.(exe|dat|msg)$' } |
                Remove-Item -Force
            Get-ChildItem -LiteralPath $backup -File |
                Where-Object { $_.Name -match '^unins[0-9]+\.(exe|dat|msg)$' } |
                Copy-Item -Destination $install -Force
            for ($index = 0; $index -lt $registration.Count; $index++) {
                $key = $registration[$index]
                if (Test-Path ($key -replace '^HKCU', 'HKCU:')) {
                    & $reg delete $key /f | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw "Cannot restore installer registration." }
                }
                $savedKey = Join-Path $backup "registry-$index.reg"
                if (Test-Path -LiteralPath $savedKey) {
                    & $reg import $savedKey | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw "Cannot restore installer registration." }
                }
            }
        } catch {
            [Console]::Error.WriteLine($_.Exception.Message)
            $keepBackup = $true
        }
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
        $running = Get-Process -Name gitdoc-desktop -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $application }
        if ((Test-Path -LiteralPath $application) -and !$running) {
            $env:GITDOC_UPDATE_RESTART_STATUS = if ($installed) { "installed" } else { "failed" }
            Start-Process -FilePath $application -WorkingDirectory $install
        }
    }
}
