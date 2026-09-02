# Meilisearch management for Windows
# Portable Meilisearch в virtual_env/packages/meilisearch; OS-служба NSSM ergo_ms_meilisearch

$script:MeilisearchServiceName = Get-ErgoServiceName -Role 'meilisearch'

function Sync-MeilisearchServiceName {
    param([string]$Root)
    $script:MeilisearchServiceName = Get-ErgoServiceName -Role 'meilisearch' -ProjectRoot $Root
}

function Get-MeilisearchPackagesDir {
    param([string]$Root)
    return Join-Path $Root 'virtual_env\packages\meilisearch'
}

function Get-MeilisearchDataDir {
    param([string]$Root)
    return Join-Path $Root 'virtual_env\cache\meilisearch\data.ms'
}

function Get-MeilisearchRuntimeDir {
    param([string]$Root)
    return Join-Path $Root 'virtual_env\cache\meilisearch'
}

function Get-MeilisearchBinary {
    param([string]$Root)
    return Join-Path (Get-MeilisearchPackagesDir -Root $Root) 'meilisearch.exe'
}

function Get-MeilisearchLogPath {
    param([string]$Root)
    return Join-Path $Root 'logs\meilisearch.log'
}

function Test-MeilisearchInstalled {
    param([string]$Root)
    return Test-Path (Get-MeilisearchBinary -Root $Root)
}

function Test-MeilisearchProcessRunning {
    $service = Get-Service -Name $script:MeilisearchServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        return $true
    }
    return $null -ne (Get-Process -Name 'meilisearch' -ErrorAction SilentlyContinue)
}

function Get-MeilisearchMasterKey {
    param([string]$Root)

    # Как load_project_env: позже перекрывает раньше (.env → env/search.env).
    $found = $null
    foreach ($envFile in @((Join-Path $Root '.env'), (Join-Path $Root 'env\search.env'))) {
        if (-not (Test-Path $envFile)) { continue }
        foreach ($line in Get-Content -Path $envFile -Encoding UTF8) {
            if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) { continue }
            if ($line -match '^MEILI_MASTER_KEY=(.*)$') {
                $found = $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($found)) {
        return $found
    }
    if ($env:MEILI_MASTER_KEY) {
        return $env:MEILI_MASTER_KEY
    }
    return ''
}

function Invoke-MeilisearchPythonScript {
    param(
        [string]$Root,
        [string[]]$ExtraArgs = @()
    )

    $pythonExe = Join-Path $Root 'virtual_env\python\Scripts\python.exe'
    $scriptPath = Join-Path $Root 'core\deployment\scripts\install_meilisearch.py'
    if (-not (Test-Path $pythonExe)) {
        Write-ErgomsMessage -Key 'python_not_found_setup' -Color Red -Stderr
        return $false
    }
    if (-not (Test-Path $scriptPath)) {
        Write-ErgomsMessage -Key 'script_not_found_path' -Color Red -Stderr -Param @{ path = $scriptPath }
        return $false
    }
    & $pythonExe $scriptPath @ExtraArgs
    return ($LASTEXITCODE -eq 0)
}

function Test-MeilisearchPing {
    param([string]$Root)
    return Invoke-MeilisearchPythonScript -Root $Root -ExtraArgs @('test', '--root', $Root)
}

function Install-Meilisearch {
    param(
        [string]$Root,
        [switch]$AsService
    )

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'heading_install_run' -Color Cyan -Param @{ name = 'Meilisearch' }
    Write-ColorOutput '' White

    $ok = Invoke-MeilisearchPythonScript -Root $Root -ExtraArgs @('install', '--root', $Root)
    if (-not $ok) {
        Write-ErgomsMessage -Key 'error_install_failed' -Color Red -Stderr -Param @{ name = 'Meilisearch' }
        return
    }

    if ($AsService) {
        Install-MeilisearchService -Root $Root
    }
    else {
        Start-MeilisearchProcess -Root $Root
    }

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'ok_installed' -Color Green -Param @{ name = 'Meilisearch' }
    Write-ErgomsMessage -Key 'label_path' -Color Cyan -Param @{ path = (Get-MeilisearchPackagesDir -Root $Root) }
}

function Install-MeilisearchService {
    param([string]$Root)
    Sync-MeilisearchServiceName -Root $Root

    if (-not (Test-MeilisearchInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{
            name = 'Meilisearch'
            cmd = 'ergoms install-meilisearch'
        }
        return
    }

    $runtimeDir = Get-MeilisearchRuntimeDir -Root $Root
    $dataDir = Get-MeilisearchDataDir -Root $Root
    $binary = Get-MeilisearchBinary -Root $Root
    $logPath = Get-MeilisearchLogPath -Root $Root
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
    if (-not (Test-Path $logPath)) {
        New-Item -ItemType File -Path $logPath | Out-Null
    }

    $nssmExe = Install-NSSM -Root $Root
    $masterKey = Get-MeilisearchMasterKey -Root $Root

    $existingService = Get-Service -Name $script:MeilisearchServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-ErgomsMessage -Key 'service_exists_reinstall' -Color Yellow -Param @{ name = $script:MeilisearchServiceName }
        if ($existingService.Status -eq 'Running') {
            & $nssmExe stop $script:MeilisearchServiceName 2>$null
            Start-Sleep -Seconds 2
        }
        & $nssmExe remove $script:MeilisearchServiceName confirm 2>$null
        Start-Sleep -Seconds 1
    }

    Stop-MeilisearchProcess -Root $Root -Quiet

    Write-ErgomsMessage -Key 'arrow_install_as_windows_service' -Color Cyan -Param @{ name = 'Meilisearch' }
    & $nssmExe install $script:MeilisearchServiceName $binary
    & $nssmExe set $script:MeilisearchServiceName AppDirectory $runtimeDir
    & $nssmExe set $script:MeilisearchServiceName AppParameters "--db-path `"$dataDir`" --http-addr 127.0.0.1:8004 --env development --master-key `"$masterKey`" --no-analytics"
    & $nssmExe set $script:MeilisearchServiceName DisplayName 'Ergo MS - Meilisearch'
    & $nssmExe set $script:MeilisearchServiceName Description 'Ergo MS portable Meilisearch (BM25 search)'
    & $nssmExe set $script:MeilisearchServiceName AppEnvironmentExtra `
        "MEILI_ENV=development" `
        "MEILI_HTTP_ADDR=127.0.0.1:8004" `
        "MEILI_DB_PATH=$dataDir" `
        "MEILI_MASTER_KEY=$masterKey" `
        "MEILI_NO_ANALYTICS=true"
    & $nssmExe set $script:MeilisearchServiceName AppStdout $logPath
    & $nssmExe set $script:MeilisearchServiceName AppStderr $logPath
    & $nssmExe set $script:MeilisearchServiceName AppRotateFiles 1
    & $nssmExe set $script:MeilisearchServiceName AppRotateBytes 10485760
    & $nssmExe set $script:MeilisearchServiceName Start SERVICE_AUTO_START
    & $nssmExe set $script:MeilisearchServiceName AppExit Default Restart
    & $nssmExe set $script:MeilisearchServiceName AppRestartDelay 5000

    Start-Service -Name $script:MeilisearchServiceName
    Write-ErgomsMessage -Key 'ok_windows_service_installed_running' -Color Green -Param @{ name = 'Meilisearch' }
}

function Stop-MeilisearchProcess {
    param(
        [string]$Root = '',
        [switch]$Quiet
    )

    if (-not (Test-MeilisearchProcessRunning)) {
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'skip_was_not_running' -Color Gray -Param @{ name = 'Meilisearch' }
        }
        return
    }

    $service = Get-Service -Name $script:MeilisearchServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        Write-ErgomsMessage -Key 'arrow_stopping_service' -Color Cyan -Param @{ name = 'Meilisearch' }
        Stop-Service -Name $script:MeilisearchServiceName -Force
        if (Test-MeilisearchProcessRunning) {
            if (-not $Quiet) {
                Write-ErgomsMessage -Key 'error_stop_service_failed' -Color Red -Stderr -Param @{ name = 'Meilisearch' }
                exit 1
            }
            return
        }
        Write-ErgomsMessage -Key 'ok_service_stopped' -Color Green -Param @{ name = 'Meilisearch' }
        return
    }

    if ($Root) {
        Invoke-MeilisearchPythonScript -Root $Root -ExtraArgs @('stop', '--root', $Root) | Out-Null
    }
    Get-Process -Name 'meilisearch' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    if (Test-MeilisearchProcessRunning) {
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'error_stop_failed' -Color Red -Stderr -Param @{ name = 'Meilisearch' }
            exit 1
        }
        return
    }
    if (-not $Quiet) {
        Write-ErgomsMessage -Key 'ok_stopped' -Color Green -Param @{ name = 'Meilisearch' }
    }
}

function Start-MeilisearchProcess {
    param([string]$Root)

    if (-not (Test-MeilisearchInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{
            name = 'Meilisearch'
            cmd = 'ergoms install-meilisearch'
        }
        exit 1
    }

    $service = Get-Service -Name $script:MeilisearchServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne 'Running') {
            Start-Service -Name $script:MeilisearchServiceName
        }
        Write-ErgomsMessage -Key 'ok_service_started' -Color Green -Param @{ name = 'Meilisearch' }
        return
    }

    if (Test-MeilisearchPing -Root $Root) {
        Write-ErgomsMessage -Key 'ok_started' -Color Green -Param @{ name = 'Meilisearch' }
        return
    }

    Write-ErgomsMessage -Key 'arrow_starting' -Color Cyan -Param @{ name = 'Meilisearch' }
    $ok = Invoke-MeilisearchPythonScript -Root $Root -ExtraArgs @('start', '--root', $Root)
    if ($ok) {
        Write-ErgomsMessage -Key 'ok_started' -Color Green -Param @{ name = 'Meilisearch' }
    }
    else {
        Write-ErgomsMessage -Key 'error_start_failed_check_logs' -Color Red -Stderr -Param @{
            name = 'Meilisearch'
            path = (Get-MeilisearchLogPath -Root $Root)
        }
        exit 1
    }
}

function Restart-MeilisearchProcess {
    param([string]$Root)

    if (-not (Test-MeilisearchInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{
            name = 'Meilisearch'
            cmd = 'ergoms install-meilisearch'
        }
        return
    }

    $service = Get-Service -Name $script:MeilisearchServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Restart-Service -Name $script:MeilisearchServiceName -Force
        Write-ErgomsMessage -Key 'ok_service_restarted' -Color Green -Param @{ name = 'Meilisearch' }
        return
    }

    Stop-MeilisearchProcess -Root $Root
    Start-MeilisearchProcess -Root $Root
}

function Uninstall-Meilisearch {
    param(
        [string]$Root,
        [switch]$PurgeData
    )
    Sync-MeilisearchServiceName -Root $Root

    Write-ErgomsMessage -Key 'heading_remove' -Color Cyan -Param @{ name = 'Meilisearch' }
    Stop-MeilisearchProcess -Root $Root -Quiet

    $service = Get-Service -Name $script:MeilisearchServiceName -ErrorAction SilentlyContinue
    if ($service) {
        $nssmExe = Install-NSSM -Root $Root
        & $nssmExe stop $script:MeilisearchServiceName 2>$null
        & $nssmExe remove $script:MeilisearchServiceName confirm 2>$null
        Write-ErgomsMessage -Key 'ok_service_stopped' -Color Green -Param @{ name = 'Meilisearch' }
    }

    $pkgDir = Get-MeilisearchPackagesDir -Root $Root
    $runtimeDir = Get-MeilisearchRuntimeDir -Root $Root
    $legacyRuntimeDir = Join-Path $Root 'virtual_env\meilisearch'
    if ($PurgeData) {
        if (Test-Path $pkgDir) {
            Remove-Item -Path $pkgDir -Recurse -Force -ErrorAction SilentlyContinue
            Write-ErgomsMessage -Key 'ok_removed_path' -Color Green -Param @{ path = $pkgDir }
        }
        foreach ($dir in @($runtimeDir, $legacyRuntimeDir)) {
            if (Test-Path $dir) {
                Remove-Item -Path $dir -Recurse -Force -ErrorAction SilentlyContinue
                Write-ErgomsMessage -Key 'ok_removed_path' -Color Green -Param @{ path = $dir }
            }
        }
    }
    else {
        Write-ErgomsMessage -Key 'ok_stopped_binaries_kept' -Color Green -Param @{
            name = 'Meilisearch'
            pkg = 'meilisearch'
            purge_flag = '-Purge'
        }
    }
}
