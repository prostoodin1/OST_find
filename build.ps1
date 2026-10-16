$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m pytest -q
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --noupx `
    --name "OST_find" `
    --version-file ".\version_info.txt" `
    --distpath ".\dist-portable" `
    ".\ost_find.py"

$PortableDir = Join-Path $Root "dist-portable\OST_find"
$Exe = Join-Path $PortableDir "OST_find.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Build did not produce $Exe"
}

$Archive = Join-Path $Root "dist-portable\OST_find_0.3.0_x64-portable.zip"
Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $Archive -Force

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --noupx `
    --name "OST_find_0.3.0_x64" `
    --version-file ".\version_info.txt" `
    --distpath ".\dist-direct" `
    --workpath ".\build-direct" `
    ".\ost_find.py"

$DirectExe = Join-Path $Root "dist-direct\OST_find_0.3.0_x64.exe"
if (-not (Test-Path -LiteralPath $DirectExe)) {
    throw "Build did not produce $DirectExe"
}

$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
$DirectHash = (Get-FileHash -LiteralPath $DirectExe -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $Root "dist-portable\SHA256SUMS.txt") -Value @(
    "$DirectHash  OST_find_0.3.0_x64.exe",
    "$ArchiveHash  OST_find_0.3.0_x64-portable.zip"
) -Encoding ascii
Write-Host "Built: $Exe"
Write-Host "SHA-256: $Hash"
Write-Host "Direct executable: $DirectExe"
Write-Host "Direct SHA-256: $DirectHash"
Write-Host "Portable archive: $Archive"
Write-Host "Archive SHA-256: $ArchiveHash"
