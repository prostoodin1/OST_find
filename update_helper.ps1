param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$target = [IO.Path]::GetFullPath($config.target)
$staging = [IO.Path]::GetFullPath($config.staging)
$backup = [IO.Path]::GetFullPath($config.backup)
$logPath = Join-Path (Split-Path -Parent $ConfigPath) 'install.log'
$installed = [Collections.Generic.List[string]]::new()
$saved = [Collections.Generic.List[string]]::new()
function Child-Path([string]$root, [string]$relative) {
    $resolved = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $resolved.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Update path escapes intended directory'
    }
    return $resolved
}
try {
    if ([IO.Path]::GetDirectoryName($backup) -ne $target -or [IO.Path]::GetFileName($backup) -notlike 'OST-update-backup-*') { throw 'Invalid backup path' }
    if (Test-Path -LiteralPath $backup) { throw 'Backup path already exists' }
    if ([IO.Path]::GetFileName($config.executable) -ne $config.executable) { throw 'Invalid executable name' }
    # A timeout leaves the installed application untouched.
    $running = Get-Process -Id $config.pid -ErrorAction SilentlyContinue
    if ($running) { Wait-Process -Id $config.pid -Timeout 180 }
    foreach ($file in $config.manifest) {
        $source = Child-Path $staging $file.path
        $stream = [IO.File]::OpenRead($source)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try { $hash = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose(); $algorithm.Dispose() }
        if ($hash -ne $file.sha256) { throw 'Staged file hash mismatch' }
    }
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $staging -Force) {
        $destination = Child-Path $target $entry.Name
        $savedPath = Child-Path $backup $entry.Name
        if (Test-Path -LiteralPath $destination) {
            # Junctions can redirect a directory swap outside the install folder.
            if ((Get-Item -LiteralPath $destination -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing reparse point in install directory' }
            Move-Item -LiteralPath $destination -Destination $savedPath
            $saved.Add($entry.Name)
        }
        Move-Item -LiteralPath $entry.FullName -Destination $destination
        $installed.Add($entry.Name)
    }
    $exe = Child-Path $target $config.executable
    $check = Start-Process -FilePath $exe -ArgumentList '--update-healthcheck' -WindowStyle Hidden -PassThru
    if (-not $check.WaitForExit(45000)) { $check.Kill(); throw 'New package health check timed out' }
    if ($check.ExitCode -ne 0) { throw 'New package health check failed' }
    Start-Process -FilePath $exe -ArgumentList '--no-auto-update'
    Set-Content -LiteralPath $logPath -Value ('Updated to ' + $config.version) -Encoding UTF8
} catch {
    $failure = $_.Exception.Message
    # Keep the failed package in staging for inspection; restore only touched entries.
    foreach ($name in $installed) {
        $path = Child-Path $target $name
        if (Test-Path -LiteralPath $path) { Move-Item -LiteralPath $path -Destination (Child-Path $staging $name) }
    }
    foreach ($name in $saved) {
        Move-Item -LiteralPath (Child-Path $backup $name) -Destination (Child-Path $target $name)
    }
    Set-Content -LiteralPath $logPath -Value ('Update failed; restored previous files. ' + $failure) -Encoding UTF8
    if ($installed.Count -gt 0 -and (Test-Path -LiteralPath (Child-Path $target $config.executable))) {
        Start-Process -FilePath (Child-Path $target $config.executable) -ArgumentList '--no-auto-update'
    }
    exit 1
}
