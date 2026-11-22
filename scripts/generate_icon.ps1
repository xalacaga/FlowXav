[CmdletBinding()]
param(
    [string]$OutputPath = ""
)

# Icône FlowXav : l'anneau orange de guidage autour d'un interrupteur de cockpit.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
if (-not $OutputPath) {
    $OutputPath = Join-Path $PSScriptRoot "..\assets\flowxav.ico"
}

function New-IconPng([int]$Size) {
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $s = $Size / 256.0

    $background = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $r = 54 * $s; $d = 2 * $r; $l = 8 * $s; $w = 240 * $s
    $background.AddArc($l, $l, $d, $d, 180, 90)
    $background.AddArc($l + $w - $d, $l, $d, $d, 270, 90)
    $background.AddArc($l + $w - $d, $l + $w - $d, $d, $d, 0, 90)
    $background.AddArc($l, $l + $w - $d, $d, $d, 90, 90)
    $background.CloseFigure()
    $backgroundBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.PointF]::new(28 * $s, 18 * $s), [System.Drawing.PointF]::new(224 * $s, 238 * $s),
        [System.Drawing.ColorTranslator]::FromHtml("#22303d"), [System.Drawing.ColorTranslator]::FromHtml("#0c1116"))
    $graphics.FillPath($backgroundBrush, $background)

    # Anneau de guidage
    $ring = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml("#ffb000"), [float](20 * $s))
    $graphics.DrawEllipse($ring, 48 * $s, 48 * $s, 160 * $s, 160 * $s)
    $halo = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(110, 255, 176, 0), [float](6 * $s))
    $graphics.DrawEllipse($halo, 26 * $s, 26 * $s, 204 * $s, 204 * $s)

    # Interrupteur : embase et levier basculé vers le haut
    $baseBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml("#3a4756"))
    $graphics.FillEllipse($baseBrush, 104 * $s, 132 * $s, 48 * $s, 48 * $s)
    $lever = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml("#e6edf3"), [float](22 * $s))
    $lever.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $lever.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $graphics.DrawLine($lever, 128 * $s, 156 * $s, 128 * $s, 86 * $s)

    $stream = [System.IO.MemoryStream]::new()
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $stream.ToArray()
    foreach ($item in @($stream, $lever, $baseBrush, $halo, $ring, $backgroundBrush, $background, $graphics, $bitmap)) {
        $item.Dispose()
    }
    return $bytes
}

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$images = [System.Collections.Generic.List[byte[]]]::new()
foreach ($size in $sizes) {
    [byte[]]$image = New-IconPng $size
    $images.Add($image)
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($resolvedOutput)) | Out-Null
$file = [System.IO.File]::Create($resolvedOutput)
$writer = [System.IO.BinaryWriter]::new($file)
try {
    $writer.Write([uint16]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]$sizes.Count)
    $offset = 6 + (16 * $sizes.Count)
    for ($index = 0; $index -lt $sizes.Count; $index++) {
        $size = $sizes[$index]
        $bytes = $images[$index]
        $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
        $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]32)
        $writer.Write([uint32]$bytes.Length)
        $writer.Write([uint32]$offset)
        $offset += $bytes.Length
    }
    foreach ($bytes in $images) { $writer.Write($bytes) }
}
finally {
    $writer.Dispose()
    $file.Dispose()
}
Write-Host "Icône créée : $resolvedOutput"
