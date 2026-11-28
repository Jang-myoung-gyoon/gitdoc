param([Parameter(Mandatory)][string[]]$Executables)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
# Check the colors from the shared product mark in the icon chosen by Windows.
$palette = @(
    [System.Drawing.Color]::FromArgb(0x12, 0x28, 0x3e).ToArgb(),
    [System.Drawing.Color]::FromArgb(0xf8, 0xfc, 0xff).ToArgb(),
    [System.Drawing.Color]::FromArgb(0x00, 0x79, 0x6f).ToArgb()
)
foreach ($executable in $Executables) {
    $path = (Get-Item -LiteralPath $executable).FullName
    $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($path)
    if (!$icon) { throw "Windows could not extract the icon: $path" }
    try {
        $bitmap = $icon.ToBitmap()
        try {
            $colors = [Collections.Generic.HashSet[int]]::new()
            for ($y = 0; $y -lt $bitmap.Height; $y++) {
                for ($x = 0; $x -lt $bitmap.Width; $x++) {
                    [void]$colors.Add($bitmap.GetPixel($x, $y).ToArgb())
                }
            }
            if ($palette | Where-Object { !$colors.Contains($_) }) {
                throw "Windows extracted an icon that does not match GitDoc: $path"
            }
            Write-Output "Verified the GitDoc icon in $([IO.Path]::GetFileName($path))"
        } finally { $bitmap.Dispose() }
    } finally { $icon.Dispose() }
}
