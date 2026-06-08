param(
    [string]$Python = "python",
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$Release = Join-Path $Root "release"
$PortableName = "ThemeForge-$Version-windows-portable"
$PortableDir = Join-Path $Release $PortableName
$ZipPath = Join-Path $Release "$PortableName.zip"

Set-Location $Root

& $Python -m pip install --upgrade pyinstaller

if (Test-Path $Build) { Remove-Item -LiteralPath $Build -Recurse -Force }
if (Test-Path $Dist) { Remove-Item -LiteralPath $Dist -Recurse -Force }
if (Test-Path $PortableDir) { Remove-Item -LiteralPath $PortableDir -Recurse -Force }
if (Test-Path $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }

& $Python -m PyInstaller `
    --noconfirm `
    --windowed `
    --name ThemeForge `
    --collect-submodules themeforge `
    (Join-Path $Root "themeforge_gui.py")

New-Item -ItemType Directory -Path $PortableDir | Out-Null
Copy-Item -Path (Join-Path $Dist "ThemeForge\*") -Destination $PortableDir -Recurse
Copy-Item -Path (Join-Path $Root "README.md") -Destination $PortableDir
Copy-Item -Path (Join-Path $Root "LICENSE") -Destination $PortableDir
Copy-Item -Path (Join-Path $Root "release_notes\v$Version.md") -Destination $PortableDir

Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $ZipPath
Write-Host "Created $ZipPath"
