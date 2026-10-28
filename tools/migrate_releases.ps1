param([switch]$DownloadOnly)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $Root '.release-migration'
New-Item -ItemType Directory -Path $Cache -Force | Out-Null
$raw = "protocol=https`nhost=github.com`n`n" | git credential fill
$credential = @{}
foreach ($line in $raw) {
    if ($line -match '^([^=]+)=(.*)$') { $credential[$matches[1]] = $matches[2] }
}
if (-not $credential.password) { throw 'GitHub credential unavailable' }
$headers = @{
    Authorization = "Bearer $($credential.password)"
    Accept = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent' = 'OST-Finder-Release-Migration'
}
$source = 'https://api.github.com/repos/prostoodin1/Ideas_projects'
$destination = 'https://api.github.com/repos/prostoodin1/OST_find'
$releases = Invoke-RestMethod -Uri "${source}/releases?per_page=100" -Headers $headers
$releases = @($releases | Where-Object { $_.tag_name -match '^ost-find-v\d+\.\d+\.\d+$' } | Sort-Object { [version]($_.tag_name -replace '^ost-find-v','') })
$manifest = @()
foreach ($release in $releases) {
    $folder = Join-Path $Cache $release.tag_name
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    foreach ($asset in $release.assets) {
        $path = Join-Path $folder $asset.name
        $downloadHeaders = $headers.Clone()
        $downloadHeaders.Accept = 'application/octet-stream'
        if (-not (Test-Path -LiteralPath $path)) {
            Invoke-WebRequest -Uri $asset.url -Headers $downloadHeaders -OutFile $path
        }
        if ((Get-Item -LiteralPath $path).Length -ne $asset.size) { throw "Size mismatch: $($asset.name)" }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($asset.digest -and $asset.digest -ne "sha256:$hash") { throw "Hash mismatch: $($asset.name)" }
    }
    $manifest += [pscustomobject]@{version=($release.tag_name -replace '^ost-find-','');assets=$release.assets.Count;source=$release.html_url}
}
if ($DownloadOnly) { $manifest | Format-Table; return }
$repo = Invoke-RestMethod -Uri $destination -Headers $headers
if (-not $repo.private) { throw 'Expected private OST_find repository' }
if ($repo.archived) {
    $null = Invoke-RestMethod -Uri $destination -Headers $headers -Method Patch -Body (@{archived=$false} | ConvertTo-Json) -ContentType 'application/json'
}
foreach ($release in $releases) {
    $tag = $release.tag_name -replace '^ost-find-',''
    git -C $Root rev-parse --verify "refs/tags/$tag" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Create source tag first: $tag" }
    git -C $Root push origin "refs/tags/$tag"
    if ($LASTEXITCODE -ne 0) { throw "Tag push failed: $tag" }
    $existing = $null
    try { $existing = Invoke-RestMethod -Uri "$destination/releases/tags/$tag" -Headers $headers }
    catch { if ([int]$_.Exception.Response.StatusCode -ne 404) { throw } }
    $body = $release.body.Replace('https://github.com/prostoodin1/Ideas_projects/releases/tag/ost-find-', 'https://github.com/prostoodin1/OST_find/releases/tag/')
    $body += "`n`n---`nПеренесено из Ideas в личный репозиторий OST Finder 28.10.2026. Оригинальные файлы сохранены побайтно; размеры и SHA-256 проверены. Исходный релиз: $($release.html_url)."
    if ($tag -eq 'v0.1.0') { $body += "`nИсторическая версия: для текущего использования выбирайте последнюю сборку." }
    $payload = @{tag_name=$tag;name="OST Finder $($tag.TrimStart('v'))";body=$body;draft=$true;prerelease=$true} | ConvertTo-Json
    if ($existing) {
        $target = Invoke-RestMethod -Uri "$destination/releases/$($existing.id)" -Headers $headers -Method Patch -Body $payload -ContentType 'application/json; charset=utf-8'
    } else {
        $target = Invoke-RestMethod -Uri "$destination/releases" -Headers $headers -Method Post -Body $payload -ContentType 'application/json; charset=utf-8'
    }
    $upload = $target.upload_url -replace '\{\?name,label\}$',''
    foreach ($asset in $release.assets) {
        $copied = $target.assets | Where-Object name -eq $asset.name
        if ($tag -eq 'v0.1.0' -and $asset.name -eq 'SHA256SUMS.txt' -and $copied -and $copied.size -ne $asset.size) {
            $null = Invoke-RestMethod -Uri "$destination/releases/assets/$($copied.id)" -Headers $headers -Method Patch -Body (@{name='SHA256SUMS-original.txt';label='Original standalone release checksum'} | ConvertTo-Json) -ContentType 'application/json'
            $copied = $null
        }
        if ($copied) {
            if ($copied.size -ne $asset.size -or ($copied.digest -and $asset.digest -and $copied.digest -ne $asset.digest)) { throw "Existing asset differs: $tag / $($asset.name)" }
            continue
        }
        $path = Join-Path (Join-Path $Cache $release.tag_name) $asset.name
        $uploaded = Invoke-RestMethod -Uri "${upload}?name=$([uri]::EscapeDataString($asset.name))" -Headers $headers -Method Post -InFile $path -ContentType 'application/octet-stream'
        if ($uploaded.size -ne $asset.size -or ($uploaded.digest -and $asset.digest -and $uploaded.digest -ne $asset.digest)) { throw "Uploaded asset differs: $($asset.name)" }
    }
    $published = Invoke-RestMethod -Uri "$destination/releases/$($target.id)" -Headers $headers -Method Patch -Body (@{draft=$false;prerelease=$true;make_latest='false'} | ConvertTo-Json) -ContentType 'application/json'
    Write-Host "Copied $tag -> $($published.html_url)"
}
