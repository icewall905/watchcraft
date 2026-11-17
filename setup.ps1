param([switch]$ConfigureOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts/common.ps1')
try {
    Require-Docker
    if (-not (Test-Path '.env')) {
        Write-Host 'BetterTube setup. Press Enter to use the defaults.'
        while ($true) {
            $data = Read-Host 'Folder for app data [./data]'
            $media = Read-Host 'Folder for downloaded videos [./media]'
            if (-not $data) { $data = './data' }
            if (-not $media) { $media = './media' }
            $data = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($data)
            $media = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($media)
            $repoPrefix = (Get-Location).Path.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
            $dataPrefix = $data.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
            $mediaPrefix = $media.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
            if ($repoPrefix.StartsWith($dataPrefix, [StringComparison]::OrdinalIgnoreCase) -or $repoPrefix.StartsWith($mediaPrefix, [StringComparison]::OrdinalIgnoreCase) -or $dataPrefix.StartsWith($mediaPrefix, [StringComparison]::OrdinalIgnoreCase) -or $mediaPrefix.StartsWith($dataPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                Write-Host 'Choose separate, non-nested storage folders, not the repository or one of its parents.'
                continue
            }
            foreach ($folder in @($data, $media)) {
                [IO.Directory]::CreateDirectory($folder) > $null
                $probe = Join-Path $folder ([IO.Path]::GetRandomFileName())
                [IO.File]::WriteAllText($probe, 'BetterTube storage check')
                Remove-Item -LiteralPath $probe
            }
            break
        }
        while ($true) {
            $port = Read-Host 'Web port [6645]'
            if (-not $port) { $port = '6645' }
            $number = 0
            if ([int]::TryParse($port, [ref]$number) -and $number -ge 1 -and $number -le 65535) { break }
            Write-Host 'Choose a number from 1 to 65535.'
        }
        function Quote-EnvValue([string]$Value) {
            return '"' + $Value.Replace('\', '\\').Replace('"', '\"').Replace('$', '$$') + '"'
        }
        $data = Quote-EnvValue ($data.Replace('\', '/'))
        $media = Quote-EnvValue ($media.Replace('\', '/'))
        $content = @"
BT_DATA_HOST_DIR=$data
BT_MEDIA_HOST_DIR=$media
BT_PORT=$number
BT_AUTH=accounts
BT_SECRET=
BT_LLM_BASE_URL=
BT_LLM_MODEL=
BT_LLM_API_KEY=
BT_POT_URL=http://pot:4416
BT_YTDLP_AUTOUPDATE=1
"@
        [IO.File]::WriteAllText((Join-Path (Get-Location).Path '.env'), $content + "`n", (New-Object Text.UTF8Encoding($false)))
        Write-Host 'Saved configuration to .env. More settings are available in the web app.'
    } else { Write-Host 'Using your existing .env. Your sign-in mode and storage choices are preserved.' }
    if ($ConfigureOnly) {
        Invoke-Compose @('config', '--quiet')
        Write-Host 'Configuration saved. You can now restore a backup with manage.bat.'
    } else {
        Start-BetterTube
        Write-Host 'Open the site. On a fresh install the sign-in screen offers "create the first admin".'
        Write-Host 'On an existing install, create an invite link for each person (add --admin for administrators):'
        Write-Host '  docker compose exec bt-api python -m bt.accounts_cli invite <username> [--admin]'
    }
} catch { Write-Host "BetterTube: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
