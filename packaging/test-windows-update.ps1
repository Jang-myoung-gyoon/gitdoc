# The runner's certificate store and all fixture data are disposable.
$ErrorActionPreference = "Stop"
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows') { throw "Use an ephemeral Windows Actions runner." }
Set-Location (Split-Path $PSScriptRoot -Parent)
$testRoot = Join-Path $env:RUNNER_TEMP ("gitdoc-update-test-" + [guid]::NewGuid())
$old = Join-Path $testRoot "old"
$new = Join-Path $testRoot "new"
$install = Join-Path $testRoot "installed"
$data = Join-Path $testRoot "user-data"
$marker = Join-Path $testRoot "restarted.txt"
$env:GITDOC_UPDATE_TEST_FAILURE_MARKER = Join-Path $testRoot "failure-stage.txt"
$env:GITDOC_UPDATE_TEST_RESTART_MARKER = $marker
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (!$iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
$winPS = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$helper = Get-Content -Raw -Encoding utf8 desktop\windows-update.ps1
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($helper))

foreach ($dir in @($old, $new)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $dir "app"), (Join-Path $dir "python") | Out-Null
    Set-Content (Join-Path $dir "python\fixture.txt") "runner only"
}
New-Item -ItemType Directory -Path $data | Out-Null
$db = Join-Path $data "documents.sqlite3"
python -c "import sqlite3, sys; c=sqlite3.connect(sys.argv[1]); c.execute('create table versions(id integer)'); c.execute('insert into versions values (1)'); c.commit()" $db
if ($LASTEXITCODE) { throw "Could not construct the isolated document store." }
Set-Content (Join-Path $data "settings.json") '{"review":true}' -Encoding ascii
$hashes = Get-FileHash (Join-Path $data '*') | Select-Object -ExpandProperty Hash

$compile = @'
$source = @"
using System;
using System.IO;
class DesktopFixture {
  static void Main() {
    var marker = Environment.GetEnvironmentVariable("GITDOC_UPDATE_TEST_RESTART_MARKER");
    if (marker != null) {
      var root = AppDomain.CurrentDomain.BaseDirectory;
      File.WriteAllText(marker, File.ReadAllText(Path.Combine(root, "app", "version.txt")).Trim()
          + "|" + Environment.GetEnvironmentVariable("GITDOC_UPDATE_RESTART_STATUS"));
    }
  }
}
"@
Add-Type -TypeDefinition $source -Language CSharp -OutputAssembly $env:GITDOC_FIXTURE_BINARY -OutputType ConsoleApplication
'@
$env:GITDOC_FIXTURE_BINARY = Join-Path $old "gitdoc-desktop.exe"
$compileEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($compile))
& $winPS -NoProfile -NonInteractive -EncodedCommand $compileEncoded
if ($LASTEXITCODE) { throw "Fixture executable build failed." }
Copy-Item $env:GITDOC_FIXTURE_BINARY (Join-Path $old "gitdoc-pdf.exe")
Copy-Item (Join-Path $old "*.exe") $new
Set-Content (Join-Path $old "app\version.txt") 'old' -Encoding ascii
Set-Content (Join-Path $new "app\version.txt") 'new' -Encoding ascii

function Build-Fixture($source, $version, $recipe) {
    $icon = (Resolve-Path desktop\icons\gitdoc.ico).Path
    & $iscc /Qp "/DAppVersion=$version" "/DSourceDir=$source" "/DOutputDir=$testRoot" "/DProductIcon=$icon" $recipe | Out-Host
    if ($LASTEXITCODE) { throw "Inno fixture build failed." }
    return Join-Path $testRoot "GitDoc-$version-windows-x64-setup.exe"
}
$prior = Build-Fixture $old '0.1.3' "packaging\gitdoc.iss"
$success = Build-Fixture $new '0.1.4' "packaging\gitdoc.iss"
& "$PSScriptRoot\check-windows-icon.ps1" -Executables @($success)
$brokenRecipe = Join-Path $testRoot "failing.iss"
Get-Content packaging\gitdoc.iss | Set-Content $brokenRecipe
@'

[Code]
procedure FixtureExit(ExitCode: Cardinal);
external 'ExitProcess@kernel32.dll stdcall';
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    SaveStringToFile(GetEnv('GITDOC_UPDATE_TEST_FAILURE_MARKER'), 'post-install copy complete', False);
    FixtureExit(27);
  end;
end;
'@ | Add-Content $brokenRecipe
$broken = Build-Fixture $old '0.1.5' $brokenRecipe
$certificate = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=GitDoc isolated update fixture' -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddDays(1)
try {
    $public = Join-Path $testRoot "fixture.cer"
    Export-Certificate -Cert $certificate -FilePath $public | Out-Null
    Write-Output 'Adding the disposable signer to the ephemeral machine trust store.'
    Import-Certificate -FilePath $public -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    Write-Output 'Disposable test certificate imported.'
    $pin = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($certificate.RawData)).Replace('-','').ToLowerInvariant()
    foreach ($file in @($success, $broken)) {
        $signature = Set-AuthenticodeSignature -FilePath $file -Certificate $certificate -HashAlgorithm SHA256
        if ($signature.Status -ne 'Valid') { throw "Test signature was not trusted: $($signature.Status)" }
    }
    $dirArg = '/DIR="' + $install + '"'
    $initial = Start-Process $prior -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES',$dirArg -PassThru
    if (!$initial.WaitForExit(30000)) { throw 'Old fixture setup timed out.' }
    if ($initial.ExitCode -ne 0) { throw "Old fixture did not install." }
    & "$PSScriptRoot\check-windows-icon.ps1" -Executables @("$install\unins000.exe")
    $env:GITDOC_UPDATE_PATH = $install
    $env:GITDOC_UPDATE_MODE = "verify"
    $env:GITDOC_UPDATE_PARENT = "2147483647"
    $env:GITDOC_UPDATE_SIGNERS = $pin
    $env:GITDOC_UPDATE_SETUP = $prior
    $env:GITDOC_UPDATE_STAGE = $testRoot
    $env:GITDOC_UPDATE_SHA256 = (Get-FileHash -Algorithm SHA256 $prior).Hash.ToLowerInvariant()
    & $winPS -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -eq 0) { throw "Unsigned setup was accepted." }

    $env:GITDOC_UPDATE_SETUP = $success
    $env:GITDOC_UPDATE_SHA256 = (Get-FileHash -Algorithm SHA256 $success).Hash.ToLowerInvariant()
    & $winPS -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -ne 0) { throw "The signed fixture failed verification." }
    $env:GITDOC_UPDATE_SIGNERS = '0' * 64
    & $winPS -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -eq 0) { throw "An unexpected signer was accepted." }
    $env:GITDOC_UPDATE_SIGNERS = $pin
    $tampered = Join-Path $testRoot "tampered.exe"
    $bytes = [IO.File]::ReadAllBytes($success)
    $bytes[512] = $bytes[512] -bxor 1
    [IO.File]::WriteAllBytes($tampered, $bytes)
    $env:GITDOC_UPDATE_SETUP = $tampered
    & $winPS -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -eq 0) { throw "A tampered file was accepted." }
    $env:GITDOC_UPDATE_SHA256 = (Get-FileHash -Algorithm SHA256 $tampered).Hash.ToLowerInvariant()
    & $winPS -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -eq 0) { throw "A tampered file with a replacement digest was accepted." }

    function Exercise-Fixture($setupPath, $expected) {
        $stage = Join-Path $testRoot ([guid]::NewGuid())
        New-Item -ItemType Directory -Path $stage | Out-Null
        $env:GITDOC_UPDATE_SETUP = Join-Path $stage "GitDoc-setup.exe"
        Copy-Item $setupPath $env:GITDOC_UPDATE_SETUP
        $env:GITDOC_UPDATE_STAGE = $stage
        $env:GITDOC_UPDATE_SHA256 = (Get-FileHash -Algorithm SHA256 $setupPath).Hash.ToLowerInvariant()
        $env:GITDOC_UPDATE_MODE = "apply"
        if (Test-Path $marker) { Remove-Item -LiteralPath $marker }
        $stderr = Join-Path $testRoot "updater-stderr.log"
        $run = Start-Process $winPS -ArgumentList "-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded -RedirectStandardError $stderr -PassThru
        if (!$run.WaitForExit(90000)) { throw "Update helper timed out." }
        Get-Content $stderr | Write-Output
        if ($run.ExitCode -ne 0) { throw "Update helper exited with $($run.ExitCode)." }
        $result = $null
        for ($i = 0; $i -lt 50 -and !$result; $i++) {
            if (Test-Path $marker) { $result = Get-Content -Raw $marker }
            if (!$result) { Start-Sleep -Milliseconds 100 }
        }
        if ($result -ne $expected) {
            throw "Installed fixture returned '$result', expected '$expected'."
        }
    }
    Write-Output 'Checking the signed Inno upgrade.'
    Exercise-Fixture $success 'new|installed'
    $uninstall = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6A2E4C0B-3C0F-4F6E-9B57-7D1B6C1F5E21}_is1'
    $record = Get-ItemProperty $uninstall
    if ($record.DisplayVersion -ne '0.1.4' -or $record.InstallLocation.TrimEnd('\') -ine $install) {
        throw "The signed installer changed the per-user identity or install location."
    }
    Write-Output 'Checking failure recovery after fixture files were copied.'
    Exercise-Fixture $broken 'new|failed'
    if (!(Test-Path $env:GITDOC_UPDATE_TEST_FAILURE_MARKER)) { throw "The injected post-install failure did not run." }
    if ((Get-ItemProperty $uninstall).DisplayVersion -ne '0.1.4') {
        throw "Recovery did not restore the prior uninstall registration."
    }
    $checked = Get-FileHash (Join-Path $data '*') | Select-Object -ExpandProperty Hash
    if (Compare-Object $hashes $checked) { throw "User data or settings changed during update." }
    Write-Output "Verified unsigned rejection, signed in-place update, restart and recovery after injected setup failure."
} finally {
    Remove-Item "Cert:\LocalMachine\Root\$($certificate.Thumbprint)" -ErrorAction SilentlyContinue
    Remove-Item "Cert:\CurrentUser\My\$($certificate.Thumbprint)" -ErrorAction SilentlyContinue
}

# The last directly invoked verifier intentionally returned nonzero for the tampered fixture.
exit 0
