param([string]$Action)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts/common.ps1')
function Backup-BetterTube {
    [IO.Directory]::CreateDirectory((Join-Path (Get-Location).Path 'backups')) > $null
    $include = @()
    if ((Read-Host 'Include downloaded videos? This may be large [y/N]') -match '^(y|yes)$') { $include = @('--media') }
    $running = @(& docker compose ps --status running --services)
    if ($LASTEXITCODE -ne 0) { throw 'Could not check running services.' }
    if ($running.Count) { Invoke-Compose @('stop') }
    try {
        $helperArgs = @('-v', "$((Get-Location).Path)/backups:/backup", '-v', "$((Get-Location).Path)/.env:/config.env:ro", 'bt-api', '-', 'backup') + $include
        Invoke-StorageHelper $helperArgs
    } finally {
        if ($running.Count) { Invoke-Compose (@('start') + $running) }
    }
    Write-Host 'Backup is in the backups folder. Copy it to another drive for safekeeping; it contains secrets.'
}
try {
    Require-Docker
    if (-not (Test-Path '.env')) { throw 'Run setup.bat first. For restore, use powershell -File setup.ps1 -ConfigureOnly.' }
    Invoke-Compose @('config', '--quiet')
    if (-not $Action) {
        Write-Host '1) Start  2) Update  3) Backup  4) Restore  5) Status  6) Stop  7) Reset profile PIN'
        $Action = Read-Host 'Choose an action'
    }
    switch -Regex ($Action) {
        '^(1|start)$' { Start-BetterTube; break }
        '^(2|update)$' {
            Write-Host 'A backup will be made before updating.'
            Backup-BetterTube
            if (Test-Path '.git') {
                & git diff --quiet
                if ($LASTEXITCODE -ne 0) { throw 'Save or commit local source changes before updating.' }
                & git diff --cached --quiet
                if ($LASTEXITCODE -ne 0) { throw 'Save or commit local source changes before updating.' }
                & git pull --ff-only
                if ($LASTEXITCODE -ne 0) { throw 'Could not update source. Check your network and Git branch.' }
            } else {
                Write-Host 'ZIP installation: extract the new release over this folder first, keeping .env, data, media and backups. Rebuilding the source currently in this folder.'
            }
            Start-BetterTube; break
        }
        '^(3|backup)$' { Backup-BetterTube; break }
        '^(4|restore)$' {
            [IO.Directory]::CreateDirectory((Join-Path (Get-Location).Path 'backups')) > $null
            Write-Host 'Restore requires empty destination data and video folders. Existing files will not be overwritten.'
            Write-Host 'Put your .tar.gz archive in the backups folder.'
            $archive = Read-Host 'Backup filename'
            if ([IO.Path]::GetFileName($archive) -ne $archive -or -not (Test-Path -LiteralPath (Join-Path 'backups' $archive) -PathType Leaf)) { throw 'Choose a filename in backups.' }
            if ((Read-Host 'Stop BetterTube and restore this archive? [y/N]') -notmatch '^(y|yes)$') { break }
            Invoke-Compose @('stop')
            Invoke-Compose @('build', 'bt-api')
            Invoke-StorageHelper @('-v', "$((Get-Location).Path)/backups:/backup", 'bt-api', '-', 'restore', $archive)
            Write-Host 'Restore finished. Run setup.bat to start BetterTube.'; break
        }
        '^(5|status)$' { Invoke-Compose @('ps'); break }
        '^(6|stop)$' { Invoke-Compose @('stop'); break }
        '^(7|reset-pin)$' {
            Invoke-StorageHelper @('bt-api', '-', 'list-profiles')
            $profileId = Read-Host 'Profile ID to reset the PIN for'
            if ($profileId -notmatch '^[0-9]+$') { throw 'Enter the profile ID shown above.' }
            if ((Read-Host 'Reset this profile PIN? Adult PINs are replaced when child profiles are enabled [y/N]') -match '^(y|yes)$') { Invoke-StorageHelper @('bt-api', '-', 'reset-pin', $profileId) }
            break
        }
        default { throw 'Choose start, update, backup, restore, status, stop or reset-pin.' }
    }
} catch { Write-Host "BetterTube: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
