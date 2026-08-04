# Redis management for Windows
# Установка и управление portable Redis в virtual_env/packages/redis

$script:RedisServiceName = 'ergo_ms_redis'



function Invoke-RedisCli {
    param(
        [string]$CliExe,
        [string[]]$Arguments
    )

    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = @()
    $exitCode = 0

    try {
        $output = @(& $CliExe @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEa
    }

    return @{
        Output = $output
        ExitCode = $exitCode
    }
}

function Test-RedisProcessRunning {
    $service = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        return $true
    }
    return $null -ne (Get-Process -Name 'redis-server' -ErrorAction SilentlyContinue)
}

function Get-RedisPackagesRelativePath {
    return Join-Path 'virtual_env' (Join-Path 'packages' 'redis')
}

function Get-RedisDir {
    param([string]$Root)
    return Join-Path $Root (Get-RedisPackagesRelativePath)
}

function Get-RedisServerExe {
    param([string]$Root)
    $dir = Get-RedisDir -Root $Root
    $direct = Join-Path $dir 'redis-server.exe'
    if (Test-Path $direct) { return $direct }
    return Join-Path $dir 'bin\redis-server.exe'
}

function Get-RedisCliExe {
    param([string]$Root)
    $dir = Get-RedisDir -Root $Root
    $direct = Join-Path $dir 'redis-cli.exe'
    if (Test-Path $direct) { return $direct }
    return Join-Path $dir 'bin\redis-cli.exe'
}

function Get-RedisConfPath {
    param([string]$Root)
    return Join-Path (Get-RedisDir -Root $Root) 'conf\redis.conf'
}

function Get-ProjectPythonExe {
    param([string]$Root)
    return Join-Path $Root 'virtual_env\python\Scripts\python.exe'
}

function Test-RedisInstalled {
    param([string]$Root)
    return (Test-Path (Get-RedisServerExe -Root $Root)) -and (Test-Path (Get-RedisConfPath -Root $Root))
}

function Read-RedisEnv {
    param([string]$Root)

    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) { return @{} }

    $result = @{}
    $lines = Get-Content -Path $envFile -Encoding UTF8
    foreach ($line in $lines) {
        if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -match '^(REDIS_[A-Z_]+|API_CACHE_REDIS_URL|CHANNEL_LAYER_REDIS_URL|API_CACHE_BACKEND|CHANNEL_LAYER_BACKEND)=(.*)$') {
            $result[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $result
}

function Get-RedisCliEndpoint {
    param([string]$Root)

    $bind = '127.0.0.1'
    $port = '6379'
    $envVars = Read-RedisEnv -Root $Root
    if ($envVars['REDIS_PORT']) {
        $port = $envVars['REDIS_PORT']
    }

    $conf = Get-RedisConfPath -Root $Root
    if (Test-Path $conf) {
        foreach ($line in Get-Content -Path $conf -Encoding UTF8) {
            if ($line -match '^\s*bind\s+(\S+)') {
                $bind = $Matches[1]
            }
            if ($line -match '^\s*port\s+(\d+)') {
                $port = $Matches[1]
            }
        }
    }

    return @{
        Host = $bind
        Port = $port
    }
}

function Invoke-RedisPythonScript {
    param(
        [string]$Root,
        [string]$ScriptName,
        [string[]]$ExtraArgs = @()
    )

    $pythonExe = Get-ProjectPythonExe -Root $Root
    $scriptPath = Join-Path $Root "core\deployment\scripts\$ScriptName"
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

function Install-Redis {
    param(
        [string]$Root,
        [string]$ListenPort = '',
        [switch]$AsService,
        [switch]$Configure
    )

    $envVars = Read-RedisEnv -Root $Root
    if (-not $ListenPort) {
        $ListenPort = if ($envVars['REDIS_PORT']) { $envVars['REDIS_PORT'] } else { '6379' }
    }

    if ($Configure) {
        Write-ErgomsMessage -Key 'redis_configure_deprecated' -Color Yellow
    }

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'heading_install_run' -Color Cyan -Param @{ name = 'Redis' }
    Write-ColorOutput '' White

    $ok = Invoke-RedisPythonScript -Root $Root -ScriptName 'install_redis.py' -ExtraArgs @(
        '--root', $Root,
        '--port', $ListenPort
    )
    if (-not $ok) {
        Write-ErgomsMessage -Key 'error_install_failed' -Color Red -Stderr -Param @{ name = 'Redis' }
        return
    }

    if ($AsService) {
        Install-RedisService -Root $Root
    }
    else {
        Start-RedisProcess -Root $Root
    }

    $redisDir = Get-RedisDir -Root $Root
    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'ok_installed' -Color Green -Param @{ name = 'Redis' }
    Write-ErgomsMessage -Key 'label_listening' -Color Cyan -Param @{ addr = "127.0.0.1:${ListenPort}" }
    Write-ErgomsMessage -Key 'label_path' -Color Cyan -Param @{ path = $redisDir }
    Write-ErgomsMessage -Key 'label_config' -Color Cyan -Param @{ path = (Get-RedisConfPath -Root $Root) }
    if (-not $Configure) {
        Write-ErgomsMessage -Key 'redis_hint_enable_env' -Color Yellow
    }
}

function Install-RedisService {
    param([string]$Root)

    if (-not (Test-RedisInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Redis'; cmd = 'ergoms install-redis' }
        return
    }

    $redisDir = Get-RedisDir -Root $Root
    $serverExe = Get-RedisServerExe -Root $Root
    $confPath = Get-RedisConfPath -Root $Root
    $nssmExe = Install-NSSM -Root $Root

    $existingService = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-ErgomsMessage -Key 'service_exists_reinstall' -Color Yellow -Param @{ name = $script:RedisServiceName }
        if ($existingService.Status -eq 'Running') {
            & $nssmExe stop $script:RedisServiceName 2>$null
            Start-Sleep -Seconds 2
        }
        & $nssmExe remove $script:RedisServiceName confirm 2>$null
        Start-Sleep -Seconds 1
    }

    Stop-RedisProcess -Root $Root -Quiet

    Write-ErgomsMessage -Key 'arrow_install_as_windows_service' -Color Cyan -Param @{ name = 'Redis' }
    & $nssmExe install $script:RedisServiceName $serverExe
    & $nssmExe set $script:RedisServiceName AppParameters 'conf\redis.conf'
    & $nssmExe set $script:RedisServiceName AppDirectory $redisDir
    & $nssmExe set $script:RedisServiceName DisplayName 'Ergo MS - Redis'
    & $nssmExe set $script:RedisServiceName Description 'Ergo MS portable Redis (cache / channel layer)'

    $logsDir = Join-Path $redisDir 'logs'
    & $nssmExe set $script:RedisServiceName AppStdout (Join-Path $logsDir 'service_stdout.log')
    & $nssmExe set $script:RedisServiceName AppStderr (Join-Path $logsDir 'service_stderr.log')
    & $nssmExe set $script:RedisServiceName Start SERVICE_AUTO_START
    & $nssmExe set $script:RedisServiceName AppExit Default Restart
    & $nssmExe set $script:RedisServiceName AppRestartDelay 5000

    Start-Service -Name $script:RedisServiceName
    Write-ErgomsMessage -Key 'ok_windows_service_installed_running' -Color Green -Param @{ name = 'Redis' }
}

function Stop-RedisProcess {
    param(
        [string]$Root = '',
        [switch]$Quiet
    )

    if (-not (Test-RedisProcessRunning)) {
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'skip_was_not_running' -Color Gray -Param @{ name = 'Redis' }
        }
        return
    }

    $service = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        Write-ErgomsMessage -Key 'arrow_stopping_service' -Color Cyan -Param @{ name = 'Redis' }
        Stop-Service -Name $script:RedisServiceName -Force
        if (Test-RedisProcessRunning) {
            if (-not $Quiet) {
                Write-ErgomsMessage -Key 'error_stop_service_failed' -Color Red -Stderr -Param @{ name = 'Redis' }
                exit 1
            }
            return
        }
        Write-ErgomsMessage -Key 'ok_service_stopped' -Color Green -Param @{ name = 'Redis' }
        return
    }

    if ($Root) {
        $cli = Get-RedisCliExe -Root $Root
        if (Test-Path $cli) {
            if (-not $Quiet) {
                Write-ErgomsMessage -Key 'redis_arrow_shutdown' -Color Cyan
            }
            $endpoint = Get-RedisCliEndpoint -Root $Root
            # Windows redis-cli: -c включает cluster mode, не путь к конфигу (в отличие от Linux).
            Invoke-RedisCli -CliExe $cli -Arguments @(
                '-h', $endpoint.Host,
                '-p', $endpoint.Port,
                'shutdown'
            ) | Out-Null
            Start-Sleep -Seconds 1
        }
        $pidFile = Join-Path (Get-RedisDir -Root $Root) 'run\redis.pid'
        if (Test-Path $pidFile) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
    }

    Get-Process -Name 'redis-server' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    if (Test-RedisProcessRunning) {
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'error_stop_failed' -Color Red -Stderr -Param @{ name = 'Redis' }
            exit 1
        }
        return
    }
    if (-not $Quiet) {
        Write-ErgomsMessage -Key 'ok_stopped' -Color Green -Param @{ name = 'Redis' }
    }
}

function Get-RedisLogPath {
    param([string]$Root)
    return Join-Path $Root 'logs\redis.log'
}

function Start-RedisProcess {
    param([string]$Root)

    if (-not (Test-RedisInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Redis'; cmd = 'ergoms install-redis' }
        exit 1
    }

    $service = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne 'Running') {
            Start-Service -Name $script:RedisServiceName
        }
        Write-ErgomsMessage -Key 'ok_service_started' -Color Green -Param @{ name = 'Redis' }
        return
    }

    # Уже отвечает — не перезапускаем (избегаем гонки warmup + start-redis-dev).
    if (Test-RedisPing -Root $Root) {
        Write-ErgomsMessage -Key 'ok_started' -Color Green -Param @{ name = 'Redis' }
        return
    }

    if (Test-RedisProcessRunning) {
        Stop-RedisProcess -Root $Root -Quiet
    }

    $redisDir = Get-RedisDir -Root $Root
    $serverExe = Get-RedisServerExe -Root $Root

    Write-ErgomsMessage -Key 'arrow_starting' -Color Cyan -Param @{ name = 'Redis' }
    # MSYS2-сборка redis-windows не принимает абсолютный путь к конфигу (C:\...).
    Start-Process -FilePath $serverExe -ArgumentList 'conf\redis.conf' -WindowStyle Hidden -WorkingDirectory $redisDir

    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-RedisPing -Root $Root) {
            $ready = $true
            break
        }
    }

    if ($ready) {
        Write-ErgomsMessage -Key 'ok_started' -Color Green -Param @{ name = 'Redis' }
    }
    else {
        Write-ErgomsMessage -Key 'error_start_failed_check_logs' -Color Red -Stderr -Param @{ name = 'Redis'; path = (Get-RedisLogPath -Root $Root) }
        exit 1
    }
}

function Restart-RedisProcess {
    param([string]$Root)

    if (-not (Test-RedisInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Redis'; cmd = 'ergoms install-redis' }
        return
    }

    $service = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Restart-Service -Name $script:RedisServiceName -Force
        Write-ErgomsMessage -Key 'ok_service_restarted' -Color Green -Param @{ name = 'Redis' }
        return
    }

    Stop-RedisProcess -Root $Root
    Start-RedisProcess -Root $Root
}

function Test-RedisPing {
    param([string]$Root)

    return Invoke-RedisPythonScript -Root $Root -ScriptName 'install_redis.py' -ExtraArgs @(
        '--root', $Root,
        '--ping-only'
    )
}

function Show-RedisStatus {
    param([string]$Root)

    $redisDir = Get-RedisDir -Root $Root
    $installed = Test-RedisInstalled -Root $Root

    if (-not $installed) {
        Write-ErgomsMessage -Key 'component_not_installed' -Color DarkGray -Param @{ name = 'Redis' }
        Write-ErgomsMessage -Key 'label_expected_path' -Color DarkGray -Param @{ path = $redisDir }
        return
    }

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'heading_status' -Color Cyan -Param @{ name = 'Redis' }

    $service = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($service) {
        $statusColor = switch ($service.Status) {
            'Running' { 'Green' }
            'Stopped' { 'Red' }
            default { 'Yellow' }
        }
        Write-ErgomsMessage -Key 'label_service_status' -Color $statusColor -Param @{ name = $script:RedisServiceName; status = $service.Status }
    }
    else {
        $procs = Get-Process -Name 'redis-server' -ErrorAction SilentlyContinue
        if ($procs) {
            Write-ErgomsMessage -Key 'status_running_pid_process' -Color Green -Param @{ pid = $procs[0].Id }
        }
        else {
            Write-ErgomsMessage -Key 'status_process_not_running' -Color Red
        }
    }

    Write-ErgomsMessage -Key 'label_path_indent2' -Color Cyan -Param @{ path = $redisDir }
    Write-ErgomsMessage -Key 'label_config_indent2' -Color Cyan -Param @{ path = (Get-RedisConfPath -Root $Root) }

    if (Test-RedisPing -Root $Root) {
        Write-ColorOutput '  Ping: PONG' Green
    }
    else {
        Write-ErgomsMessage -Key 'ping_failed_server_down' -Color Yellow
    }
}

function Uninstall-Redis {
    param(
        [string]$Root,
        [switch]$PurgeData
    )

    Write-ErgomsMessage -Key 'heading_remove' -Color Cyan -Param @{ name = 'Redis' }
    Stop-RedisProcess -Root $Root -Quiet

    $service = Get-Service -Name $script:RedisServiceName -ErrorAction SilentlyContinue
    if ($service) {
        $nssmExe = Install-NSSM -Root $Root
        & $nssmExe stop $script:RedisServiceName 2>$null
        & $nssmExe remove $script:RedisServiceName confirm 2>$null
        Write-ErgomsMessage -Key 'redis_ok_service_removed' -Color Green
    }

    $redisDir = Get-RedisDir -Root $Root
    if ($PurgeData -and (Test-Path $redisDir)) {
        Remove-Item -Path $redisDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-ErgomsMessage -Key 'ok_removed_path' -Color Green -Param @{ path = $redisDir }
    }
    else {
        Write-ErgomsMessage -Key 'ok_stopped_binaries_kept' -Color Green -Param @{ name = 'Redis'; pkg = 'redis'; purge_flag = '-Purge' }
    }
}
