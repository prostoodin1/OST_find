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

$Archive = Join-Path $Root "dist-portable\OST_find_0.1.1_x64-portable.zip"
Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $Archive -Force
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $Root "dist-portable\SHA256SUMS.txt") -Value "$ArchiveHash  OST_find_0.1.1_x64-portable.zip" -Encoding ascii
Write-Host "Built: $Exe"
Write-Host "SHA-256: $Hash"
Write-Host "Portable archive: $Archive"
Write-Host "Archive SHA-256: $ArchiveHash"
