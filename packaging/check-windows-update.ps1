# Run on Windows before publishing an installer to the stable release channel.
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Signers
)
$ErrorActionPreference = "Stop"
$pins = $Signers.Trim() -split '\s+'
if ($pins.Count -gt 2 -or ($pins | Where-Object { $_ -cnotmatch '^[0-9a-f]{64}$' })) {
    throw "Pass one or two lowercase SHA-256 certificate digests."
}
$resolved = (Resolve-Path -LiteralPath $Setup).Path
$env:GITDOC_UPDATE_SETUP = $resolved
$env:GITDOC_UPDATE_STAGE = Split-Path $resolved -Parent
$env:GITDOC_UPDATE_PATH = $env:GITDOC_UPDATE_STAGE
$env:GITDOC_UPDATE_SHA256 = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
$env:GITDOC_UPDATE_SIGNERS = $pins -join ','
$env:GITDOC_UPDATE_MODE = "verify"
$helper = Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot "../desktop/windows-update.ps1")
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($helper))
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -EncodedCommand $encoded
if ($LASTEXITCODE) { throw "Windows does not trust this installer or its signer pin is different." }
Write-Output "Verified the installer with Windows Authenticode and the published GitDoc signer pin."
