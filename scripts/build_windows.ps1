[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipToolInstall
)

# Construit FlowXav : tests, application autonome (PyInstaller), archive portable,
# installateur Inno Setup qui installe WebView2 si besoin, sommes SHA-256.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VersionSource = Get-Content -LiteralPath (Join-Path $ProjectRoot "flowxav\__init__.py") -Raw
if ($VersionSource -notmatch '__version__\s*=\s*"(?<version>\d+\.\d+\.\d+)"') {
    throw "Version FlowXav introuvable dans flowxav\__init__.py."
}
$Version = $Matches.version
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ReleaseDir = Join-Path $ProjectRoot "release"
$ModernSimConnect = "C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll"
$WebViewBootstrapper = Join-Path $ProjectRoot "installer\assets\MicrosoftEdgeWebView2Setup.exe"
$WebViewBootstrapperUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$AppIcon = Join-Path $ProjectRoot "assets\flowxav.ico"

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }

function Find-Iscc {
    $command = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    return $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

function Invoke-NativeTool([scriptblock]$Command) {
    # PyInstaller, pip et ISCC écrivent leur journal sur stderr : seul le code de sortie fait foi.
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ }
        } | Out-Host
    }
    finally { $ErrorActionPreference = $PreviousPreference }
}

function Test-PythonImports([string]$Imports) {
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $VenvPython -c "import $Imports" 2>$null
    $Succeeded = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $PreviousPreference
    return $Succeeded
}

Set-Location $ProjectRoot
Write-Step "Contrôle de l'environnement de construction"
if (-not [Environment]::Is64BitOperatingSystem) { throw "La construction FlowXav nécessite Windows 64 bits." }
if (-not (Test-Path -LiteralPath $ModernSimConnect)) {
    throw "Le SDK MSFS 2024 et sa DLL SimConnect sont requis pour construire la distribution."
}

Write-Step "Génération de l'icône FlowXav"
& (Join-Path $PSScriptRoot "generate_icon.ps1") -OutputPath $AppIcon

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if (-not $launcher) { throw "Python 3.11+ est absent : installez Python 3.12 puis relancez." }
    Write-Step "Création de l'environnement Python isolé"
    & $launcher.Source -3 -m venv (Join-Path $ProjectRoot ".venv")
}
& $VenvPython -c "import sys; assert sys.version_info >= (3, 11), sys.version"
& $VenvPython -c "import pip; assert tuple(map(int, pip.__version__.split('.')[:2])) >= (26, 2), pip.__version__" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step "Mise à niveau sécurisée de pip"
    Invoke-NativeTool { & $VenvPython -m pip install --disable-pip-version-check --upgrade "pip>=26.2" }
    if ($LASTEXITCODE -ne 0) { throw "pip 26.2 ou plus récent est requis pour corriger ses vulnérabilités connues." }
}

Write-Step "Contrôle des bibliothèques du projet"
if (-not (Test-PythonImports "fastapi, uvicorn, pydantic, webview, pytest")) {
    Invoke-NativeTool { & $VenvPython -m pip install --disable-pip-version-check -e "$ProjectRoot[dev]" }
}
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Les dépendances Python installées sont incohérentes." }
if (-not (Test-PythonImports "pip_audit")) {
    if ($SkipToolInstall) { throw "pip-audit est absent. Relancez sans -SkipToolInstall." }
    Write-Step "Installation de l'auditeur de dépendances"
    Invoke-NativeTool { & $VenvPython -m pip install --disable-pip-version-check "pip-audit>=2.10,<3" }
}

Write-Step "Audit des vulnérabilités Python"
$AuditCache = Join-Path $env:TEMP "FlowXav-pip-audit-$PID"
Invoke-NativeTool { & $VenvPython -m pip_audit --local --progress-spinner off --cache-dir $AuditCache }
if ($LASTEXITCODE -ne 0) { throw "Une dépendance Python présente une vulnérabilité connue." }
if (-not (Test-PythonImports "PyInstaller")) {
    if ($SkipToolInstall) { throw "PyInstaller est absent. Relancez sans -SkipToolInstall." }
    Write-Step "Installation de PyInstaller"
    Invoke-NativeTool { & $VenvPython -m pip install --disable-pip-version-check "pyinstaller>=6.10,<7" }
}

Write-Step "Contrôle du composant Microsoft WebView2"
if (-not (Test-Path -LiteralPath $WebViewBootstrapper)) {
    New-Item -ItemType Directory -Path (Split-Path $WebViewBootstrapper) -Force | Out-Null
    Invoke-WebRequest -Uri $WebViewBootstrapperUrl -OutFile $WebViewBootstrapper
}
$WebViewSignature = Get-AuthenticodeSignature -LiteralPath $WebViewBootstrapper
if ($WebViewSignature.Status -ne "Valid" -or $WebViewSignature.SignerCertificate.Subject -notlike "*Microsoft Corporation*") {
    throw "Le programme d'installation WebView2 n'a pas une signature Microsoft valide."
}

Write-Step "Tests automatisés"
$TestTemp = Join-Path $env:TEMP "FlowXav-tests-$PID"
Invoke-NativeTool { & $VenvPython -m pytest -m "not live_msfs and not live_network and not installed" --basetemp $TestTemp -p no:cacheprovider }
if ($LASTEXITCODE -ne 0) { throw "Les tests ont échoué ; distribution annulée." }

Write-Step "Construction de l'application autonome"
Invoke-NativeTool { & $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "FlowXav.spec") }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller n'a pas produit l'application." }

Write-Step "Smoke test de l'exécutable autonome"
$SmokeListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$SmokeListener.Start()
$SmokePort = $SmokeListener.LocalEndpoint.Port
$SmokeListener.Stop()
$BuiltExe = Join-Path $ProjectRoot "dist\FlowXav\FlowXav.exe"
Invoke-NativeTool { & $BuiltExe --smoke-test --port $SmokePort }
if ($LASTEXITCODE -ne 0) { throw "L'exécutable autonome ne démarre ou ne s'arrête pas correctement." }

New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
$DistDir = Join-Path $ProjectRoot "dist\FlowXav"
Get-ChildItem -LiteralPath $ProjectRoot -Filter "README*.md" -File | Copy-Item -Destination $DistDir -Force
& (Join-Path $PSScriptRoot "collect_licenses.ps1") -PythonPath $VenvPython -Destination (Join-Path $DistDir "licenses")
$PortableArchive = Join-Path $ReleaseDir "FlowXav-$Version-windows-x64-portable.zip"
if (Test-Path -LiteralPath $PortableArchive) { Remove-Item -LiteralPath $PortableArchive -Force }
Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $PortableArchive -CompressionLevel Optimal

if (-not $SkipInstaller) {
    $Iscc = Find-Iscc
    if (-not $Iscc -and -not $SkipToolInstall) {
        $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Step "Installation d'Inno Setup"
            & $winget.Source install --id JRSoftware.InnoSetup --exact --silent --accept-package-agreements --accept-source-agreements
            $Iscc = Find-Iscc
        }
    }
    if (-not $Iscc) { throw "Inno Setup 6 est absent. Utilisez -SkipInstaller ou autorisez son installation." }
    Write-Step "Construction de l'installateur Windows"
    Invoke-NativeTool { & $Iscc "/DMyAppVersion=$Version" (Join-Path $ProjectRoot "installer\FlowXav.iss") }
    if ($LASTEXITCODE -ne 0) { throw "La compilation de l'installateur a échoué." }
}

Write-Step "Sommes de contrôle SHA-256"
Get-ChildItem -LiteralPath $ReleaseDir -File | Where-Object Extension -In ".exe", ".zip" | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    Set-Content -LiteralPath ($_.FullName + ".sha256") -Value "$hash  $($_.Name)" -Encoding ascii
    Write-Host "$($_.Name)  $hash"
}
Write-Host "`nDistribution terminée : $ReleaseDir" -ForegroundColor Green
