[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonPath,
    [Parameter(Mandatory)][string]$Destination
)

# Copie la licence de FlowXav et celles des bibliothèques embarquées dans la distribution.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ThirdPartyDir = Join-Path $Destination "third-party"

New-Item -ItemType Directory -Path $ThirdPartyDir -Force | Out-Null
foreach ($Name in @("LICENSE", "NOTICE")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $Destination -Force
}

$Distributions = @(
    "annotated_types", "anyio", "bottle", "cffi", "click", "clr_loader", "colorama", "fastapi", "h11",
    "idna", "proxy_tools", "pycparser", "pydantic", "pydantic_core", "pythonnet", "pywebview",
    "starlette", "typing_extensions", "typing_inspection", "uvicorn"
)
$SitePackages = & $PythonPath -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
if ($LASTEXITCODE -ne 0 -or -not $SitePackages) { throw "Répertoire site-packages introuvable." }

foreach ($Name in $Distributions) {
    $InfoDir = Get-ChildItem -LiteralPath $SitePackages -Directory -Filter "$Name-*.dist-info" | Select-Object -First 1
    if (-not $InfoDir) {
        Write-Warning "Métadonnées absentes pour $Name (bibliothèque non installée ?)."
        continue
    }
    $LicenseFiles = Get-ChildItem -LiteralPath $InfoDir.FullName -Recurse -File |
        Where-Object { $_.Name -match '^(LICENSE|LICENCE|COPYING|NOTICE)(\.|$)' }
    if (-not $LicenseFiles) { throw "Texte de licence introuvable pour $Name." }
    $PackageDir = Join-Path $ThirdPartyDir $Name
    New-Item -ItemType Directory -Path $PackageDir -Force | Out-Null
    foreach ($LicenseFile in $LicenseFiles) { Copy-Item -LiteralPath $LicenseFile.FullName -Destination $PackageDir -Force }
}

$PythonLicense = & $PythonPath -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix)/'LICENSE.txt')"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonLicense)) { throw "Licence de Python introuvable." }
Copy-Item -LiteralPath $PythonLicense -Destination (Join-Path $ThirdPartyDir "PYTHON_LICENSE.txt") -Force

$PyInstallerInfo = Get-ChildItem -LiteralPath $SitePackages -Directory -Filter "pyinstaller-*.dist-info" | Select-Object -First 1
if ($PyInstallerInfo) {
    $PyInstallerLicense = Get-ChildItem -LiteralPath $PyInstallerInfo.FullName -Recurse -File |
        Where-Object { $_.Name -match '^(COPYING|LICENSE)' } | Select-Object -First 1
    if ($PyInstallerLicense) {
        Copy-Item -LiteralPath $PyInstallerLicense.FullName -Destination (Join-Path $ThirdPartyDir "PYINSTALLER_COPYING.txt") -Force
    }
}
Write-Host "Licences copiées dans $Destination" -ForegroundColor Green
