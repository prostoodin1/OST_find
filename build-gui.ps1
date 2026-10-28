param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& $Python -m PyInstaller --noconfirm --distpath dist-gui --workpath build-gui packaging/OST_Finder_Spider.spec
if ($LASTEXITCODE -ne 0) { throw 'Spider build failed' }
Copy-Item -LiteralPath 'README.md','THIRD_PARTY.md','VERSIONS.md','LICENSE' -Destination 'dist-gui\OST_Finder_Spider'
Copy-Item -LiteralPath 'licenses' -Destination 'dist-gui\OST_Finder_Spider\licenses' -Recurse -Force
Compress-Archive -Path 'dist-gui\OST_Finder_Spider\*' -DestinationPath 'dist-gui\OST_Finder_Spider_0.4.0_x64-portable.zip' -Force
Write-Host 'Spider builds completed in dist-gui/'
