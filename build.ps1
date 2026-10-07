$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m pytest -q
python -m PyInstaller --noconfirm --clean --onefile --console --name "OST_find" --collect-all rich ".\ost_find.py"

$Exe = Join-Path $Root "dist\OST_find.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Build did not produce $Exe"
}

$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $Root "dist\SHA256SUMS.txt") -Value "$Hash  OST_find.exe" -Encoding ascii
Write-Host "Built: $Exe"
Write-Host "SHA-256: $Hash"
