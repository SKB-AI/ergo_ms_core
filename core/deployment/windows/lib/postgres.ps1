# PostgreSQL management for Windows
# Установка и управление portable PostgreSQL в virtual_env/packages/postgres

. (Join-Path $PSScriptRoot 'portable_env.ps1')

$script:PostgresServiceNameDefault = 'ergo_ms_postgres'
$script:PostgresServiceName = $script:PostgresServiceNameDefault
$script:PostgresServiceDisplayName = 'Ergo MS - PostgreSQL'
$script:PostgresServiceRestartDelayMs = '5000'
# LocalSystem = admin → postgres отказывается стартовать; NetworkService — без elevated token.
$script:PostgresServiceAccount = 'NT AUTHORITY\NetworkService'
$script:PostgresServiceAccountSid = 'S-1-5-20'

function Initialize-PostgresServiceConfig {
    param([string]$Root)
    if (-not $Root) { return }

    $name = Get-ErgoEnvValue -Root $Root -Name 'POSTGRES_SERVICE_WINDOWS'
    if ($name) { $script:PostgresServiceName = $name } else { $script:PostgresServiceName = $script:PostgresServiceNameDefault }

    $display = Get-ErgoEnvValue -Root $Root -Name 'POSTGRES_SERVICE_DISPLAY_NAME'
    if ($display) { $script:PostgresServiceDisplayName = $display } else { $script:PostgresServiceDisplayName = 'Ergo MS - PostgreSQL' }

    $delay = Get-ErgoEnvValue -Root $Root -Name 'POSTGRES_SERVICE_RESTART_DELAY_MS'
    if ($delay) { $script:PostgresServiceRestartDelayMs = $delay } else { $script:PostgresServiceRestartDelayMs = '5000' }
}

function Get-PostgresServiceName {
    param([string]$Root)
    Initialize-PostgresServiceConfig -Root $Root
    return $script:PostgresServiceName
}

function Get-PostgresPackagesRelativePath {
    return Join-Path 'virtual_env' (Join-Path 'packages' 'postgres')
}

function Get-PostgresDir {
    param([string]$Root)
    return Join-Path $Root (Get-PostgresPackagesRelativePath)
}

function Get-PostgresBinDir {
    param([string]$Root)
    $dir = Get-PostgresDir -Root $Root
    $direct = Join-Path $dir 'bin'
    if (Test-Path (Join-Path $direct 'postgres.exe')) { return $direct }
    $nested = Join-Path $dir 'pgsql\bin'
    if (Test-Path (Join-Path $nested 'postgres.exe')) { return $nested }
    return $direct
}

function Get-PostgresExe {
    param([string]$Root, [string]$Name)
    return Join-Path (Get-PostgresBinDir -Root $Root) "$Name.exe"
}

function Get-PostgresDataDir {
    param([string]$Root)
    return Join-Path (Get-PostgresDir -Root $Root) 'data'
}

function Get-ProjectPythonExeForPostgres {
    param([string]$Root)
    return Join-Path $Root 'virtual_env\python\Scripts\python.exe'
}

function Test-PostgresInstalled {
    param([string]$Root)
    return (Test-Path (Get-PostgresExe -Root $Root -Name 'postgres')) -and (Test-Path (Get-PostgresExe -Root $Root -Name 'pg_ctl'))
}

function Invoke-PostgresCtl {
    param(
        [string]$PgCtl,
        [string[]]$Arguments
    )

    # Native stderr (например «PID file does not exist») при Stop становится terminating.
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $PgCtl @Arguments 2>&1 | Out-Null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
}

function Stop-PostgresClusterIfRunning {
    param(
        [string]$Root,
        [string]$DataDir = ''
    )

    $pgCtl = Get-PostgresExe -Root $Root -Name 'pg_ctl'
    if (-not (Test-Path $pgCtl)) { return }
    if (-not $DataDir) { $DataDir = Get-PostgresDataDir -Root $Root }
    $pidFile = Join-Path $DataDir 'postmaster.pid'
    if (-not (Test-Path $pidFile)) { return }
    Invoke-PostgresCtl -PgCtl $pgCtl -Arguments @('stop', '-D', $DataDir, '-m', 'fast', '-w') | Out-Null
}

function Grant-PostgresServiceDirectoryAccess {
    param(
        [string]$PgDir,
        [string]$NssmDir = '',
        [string]$LogsDir = ''
    )

    # Служба идёт от NetworkService: SCM стартует nssm.exe, тот уже запускает postgres.
    # Права только на packages/postgres недостаточны — образ службы лежит в packages/nssm.
    # log_directory в postgresql.conf указывает на корневой logs/ — туда тоже нужна запись.
    $targets = @($PgDir)
    if ($NssmDir) { $targets += $NssmDir }
    if ($LogsDir) { $targets += $LogsDir }

    # SID NETWORK SERVICE — стабильнее локализованного имени для icacls.
    $grant = "*$($script:PostgresServiceAccountSid):(OI)(CI)M"
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        foreach ($target in $targets) {
            if (-not $target -or -not (Test-Path $target)) { continue }
            & icacls $target /grant $grant /T /C /Q | Out-Null
        }
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
}

function Wait-PostgresServiceRemoved {
    param(
        [string]$ServiceName = $script:PostgresServiceName,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return -not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)
}

function Test-PostgresPortablePresent {
    param([string]$Root)
    Initialize-PostgresServiceConfig -Root $Root
    if (Test-PostgresInstalled -Root $Root) { return $true }
    $svc = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    return $null -ne $svc
}

function Invoke-PostgresPythonScript {
    param(
        [string]$Root,
        [string]$ScriptName,
        [string[]]$ExtraArgs = @()
    )

    $pythonExe = Get-ProjectPythonExeForPostgres -Root $Root
    $scriptPath = Join-Path $Root "core\deployment\scripts\$ScriptName"
    if (-not (Test-Path $pythonExe)) {
        Write-ErgomsMessage -Key 'python_not_found_setup' -Color Red -Stderr
        return $false
    }
    if (-not (Test-Path $scriptPath)) {
        Write-ErgomsMessage -Key 'script_not_found_path' -Color Red -Stderr -Param @{ path = $scriptPath }
        return $false
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    & $pythonExe -u $scriptPath @ExtraArgs
    return ($LASTEXITCODE -eq 0)
}

function Sync-PostgresBackupSchedule {
    param(
        [string]$Root,
        [switch]$Uninstall
    )
    $extra = @('--root', $Root)
    if ($Uninstall) {
        $extra += '--uninstall'
    }
    Invoke-PostgresPythonScript -Root $Root -ScriptName 'install_postgres_backup_schedule.py' -ExtraArgs $extra | Out-Null
}

function Test-SystemPostgresqlService {
    param([string]$Root)
    return Invoke-PostgresPythonScript -Root $Root -ScriptName 'install_postgres.py' -ExtraArgs @(
        '--root', $Root,
        '--check-system-only'
    )
}

function Test-PostgresForceInstall {
    param([string]$Root)
    return Invoke-PostgresPythonScript -Root $Root -ScriptName 'install_postgres.py' -ExtraArgs @(
        '--root', $Root,
        '--check-force-only'
    )
}

function Install-Postgres {
    param(
        [string]$Root,
        [string]$ListenPort = '',
        [switch]$NoSkipSystem
    )

    Initialize-PostgresServiceConfig -Root $Root

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'heading_install_run' -Color Cyan -Param @{ name = 'PostgreSQL' }
    Write-ColorOutput '' White

    $forceEnv = Test-PostgresForceInstall -Root $Root
    if ((Test-SystemPostgresqlService -Root $Root) -and -not $forceEnv -and -not $NoSkipSystem) {
        Write-ErgomsMessage -Key 'pg_skip_system_service' -Color Gray
        Write-ErgomsMessage -Key 'pg_info_force_install' -Color Cyan
        return
    }

    $extra = @('--root', $Root, '--no-start')
    if ($ListenPort) {
        $extra += @('--port', $ListenPort)
    }
    if ($forceEnv -or $NoSkipSystem) {
        $extra += '--no-skip-system'
    }

    $ok = Invoke-PostgresPythonScript -Root $Root -ScriptName 'install_postgres.py' -ExtraArgs $extra
    if (-not $ok) {
        Write-ErgomsMessage -Key 'error_install_failed' -Color Red -Stderr -Param @{ name = 'PostgreSQL' }
        exit 1
    }

    if (-not (Test-PostgresInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'pg_skip_portable' -Color Gray
        return
    }

    Install-PostgresService -Root $Root

    $dbOk = Invoke-PostgresPythonScript -Root $Root -ScriptName 'install_postgres.py' -ExtraArgs @(
        '--root', $Root,
        '--ensure-db-only'
    )
    if (-not $dbOk) {
        Write-ErgomsMessage -Key 'pg_error_create_dbs' -Color Red -Stderr
        exit 1
    }

    $pgDir = Get-PostgresDir -Root $Root
    $listenPort = Get-PostgresListenPort -Root $Root
    $listenBind = Get-PostgresListenBind -Root $Root
    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'ok_installed' -Color Green -Param @{ name = 'PostgreSQL' }
    Write-ErgomsMessage -Key 'label_path' -Color Cyan -Param @{ path = $pgDir }
    Write-ErgomsMessage -Key 'label_service' -Color Cyan -Param @{ name = $script:PostgresServiceName }
    Write-ErgomsMessage -Key 'label_listening' -Color Cyan -Param @{ addr = "${listenBind}:${listenPort}" }
    Write-PostgresDbAccessSummary -Root $Root
    Write-PostgresYamlPortHint -Root $Root -ListenPort $listenPort
}

function Get-NssmParameterValue {
    param(
        [string]$NssmExe,
        [string]$ServiceName,
        [string]$Parameter
    )

    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & $NssmExe get $ServiceName $Parameter 2>&1
        if ($LASTEXITCODE -ne 0) { return '' }
        # nssm get пишет UTF-16; в PowerShell строка содержит NUL между символами.
        $text = [string]($raw | Out-String)
        return ($text -replace "`0", '' -replace '[\r\n]+', '').Trim()
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
}

function ConvertTo-NormalizedFsPath {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) { return '' }
    $trimmed = $PathValue.Trim().Trim('"')
    try {
        return [System.IO.Path]::GetFullPath($trimmed).TrimEnd('\')
    }
    catch {
        return $trimmed.TrimEnd('\')
    }
}

function ConvertTo-NormalizedPostgresAppParameters {
    param(
        [string]$Value,
        [string]$DataDir
    )

    $normalizedData = ConvertTo-NormalizedFsPath -PathValue $DataDir
    if ($Value -match '(?i)^-D\s+"?(.+?)"?\s*$') {
        return "-D $(ConvertTo-NormalizedFsPath -PathValue $Matches[1])"
    }
    return "-D $normalizedData"
}

function Test-PostgresNssmServiceMatches {
    param(
        [string]$NssmExe,
        [string]$PostgresExe,
        [string]$PgDir,
        [string]$DataDir,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    $name = $script:PostgresServiceName
    $checks = @(
        @{
            Param = 'Application'
            Expected = ConvertTo-NormalizedFsPath -PathValue $PostgresExe
            Actual = ConvertTo-NormalizedFsPath -PathValue (Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'Application')
        },
        @{
            Param = 'AppDirectory'
            Expected = ConvertTo-NormalizedFsPath -PathValue $PgDir
            Actual = ConvertTo-NormalizedFsPath -PathValue (Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'AppDirectory')
        },
        @{
            Param = 'AppParameters'
            Expected = ConvertTo-NormalizedPostgresAppParameters -Value "-D `"$DataDir`"" -DataDir $DataDir
            Actual = ConvertTo-NormalizedPostgresAppParameters -Value (Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'AppParameters') -DataDir $DataDir
        },
        @{
            Param = 'ObjectName'
            Expected = $script:PostgresServiceAccount
            Actual = Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'ObjectName'
        },
        @{
            Param = 'Start'
            Expected = 'SERVICE_AUTO_START'
            Actual = Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'Start'
        },
        @{
            Param = 'AppStdout'
            Expected = ConvertTo-NormalizedFsPath -PathValue $StdoutLog
            Actual = ConvertTo-NormalizedFsPath -PathValue (Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'AppStdout')
        },
        @{
            Param = 'AppStderr'
            Expected = ConvertTo-NormalizedFsPath -PathValue $StderrLog
            Actual = ConvertTo-NormalizedFsPath -PathValue (Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'AppStderr')
        },
        @{
            Param = 'AppRestartDelay'
            Expected = $script:PostgresServiceRestartDelayMs
            Actual = Get-NssmParameterValue -NssmExe $NssmExe -ServiceName $name -Parameter 'AppRestartDelay'
        }
    )

    foreach ($item in $checks) {
        if (-not [string]::Equals([string]$item.Expected, [string]$item.Actual, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
    }
    return $true
}

function Start-PostgresServiceAndVerify {
    param(
        [string]$StderrLog,
        [string]$OkKey = 'pg_ok_service_started',
        [string]$OkMessage = ''
    )

    try {
        Start-Service -Name $script:PostgresServiceName -ErrorAction Stop
    }
    catch {
        Write-ErgomsMessage -Key 'pg_error_start_service' -Color Red -Stderr
        if (Test-Path $StderrLog) {
            Get-Content $StderrLog -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object {
                Write-ColorOutput "    $_" Red
            }
        }
        throw
    }
    Start-Sleep -Seconds 2
    $running = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if (-not $running -or $running.Status -ne 'Running') {
        Write-ErgomsMessage -Key 'pg_error_not_running' -Color Red -Stderr
        if (Test-Path $StderrLog) {
            Get-Content $StderrLog -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object {
                Write-ColorOutput "    $_" Red
            }
        }
        exit 1
    }
    if ($OkMessage) {
        Write-ColorOutput (Format-ErgoConsole -Level ok -Message $OkMessage) Green
    } else {
        Write-ErgomsMessage -Key $OkKey -Color Green
    }
}

function Install-PostgresService {
    param([string]$Root)

    Initialize-PostgresServiceConfig -Root $Root

    if (-not (Test-PostgresInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'PostgreSQL'; cmd = 'ergoms install-postgres' }
        return
    }

    $pgDir = Get-PostgresDir -Root $Root
    $postgresExe = Get-PostgresExe -Root $Root -Name 'postgres'
    $dataDir = Get-PostgresDataDir -Root $Root
    $nssmExe = Install-NSSM -Root $Root

    $logsDir = Join-Path $pgDir 'logs'
    if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
    $stderrLog = Join-Path $logsDir 'service_stderr.log'
    $stdoutLog = Join-Path $logsDir 'service_stdout.log'

    $existingService = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if (
        $existingService -and
        (Test-PostgresNssmServiceMatches -NssmExe $nssmExe -PostgresExe $postgresExe -PgDir $pgDir -DataDir $dataDir -StdoutLog $stdoutLog -StderrLog $stderrLog)
    ) {
        Grant-PostgresServiceDirectoryAccess -PgDir $pgDir -NssmDir (Split-Path -Parent $nssmExe) -LogsDir (Get-ProjectLogsDir -ProjectRoot $Root)
        if ($existingService.Status -eq 'Running') {
            Write-ErgomsMessage -Key 'pg_service_already_configured_running' -Color Gray -Param @{ name = $script:PostgresServiceName }
            Sync-PostgresBackupSchedule -Root $Root
            return
        }
        Write-ErgomsMessage -Key 'pg_service_configured_starting' -Color Cyan -Param @{ name = $script:PostgresServiceName }
        Start-PostgresServiceAndVerify -StderrLog $stderrLog -OkKey 'pg_ok_service_started'
        Sync-PostgresBackupSchedule -Root $Root
        return
    }

    if ($existingService) {
        Write-ErgomsMessage -Key 'pg_service_stale_reinstall' -Color Cyan -Param @{ name = $script:PostgresServiceName }
    }

    Stop-PostgresClusterIfRunning -Root $Root -DataDir $dataDir

    if ($existingService) {
        if ($existingService.Status -eq 'Running' -or $existingService.Status -eq 'Paused') {
            $prevEa = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                & $nssmExe stop $script:PostgresServiceName 2>&1 | Out-Null
                Stop-Service -Name $script:PostgresServiceName -Force -ErrorAction SilentlyContinue
            }
            finally {
                $ErrorActionPreference = $prevEa
            }
            Start-Sleep -Seconds 2
        }
        & $nssmExe remove $script:PostgresServiceName confirm 2>$null
        if (-not (Wait-PostgresServiceRemoved)) {
            Write-ErgomsMessage -Key 'pg_error_marked_for_deletion' -Color Red -Stderr
            exit 1
        }
    }

    Write-ErgomsMessage -Key 'pg_installing_windows_service' -Color Cyan
    & $nssmExe install $script:PostgresServiceName $postgresExe
    if ($LASTEXITCODE -ne 0) {
        Write-ErgomsMessage -Key 'pg_error_nssm_register' -Color Red -Stderr
        exit 1
    }
    & $nssmExe set $script:PostgresServiceName AppParameters "-D `"$dataDir`""
    & $nssmExe set $script:PostgresServiceName AppDirectory $pgDir
    & $nssmExe set $script:PostgresServiceName DisplayName $script:PostgresServiceDisplayName
    & $nssmExe set $script:PostgresServiceName Description 'Ergo MS portable PostgreSQL'
    # LocalSystem запрещён для postmaster; пустой пароль — для встроенной учётки.
    & $nssmExe set $script:PostgresServiceName ObjectName $script:PostgresServiceAccount ''

    if (Test-Path $stderrLog) { Clear-Content $stderrLog -ErrorAction SilentlyContinue }
    if (Test-Path $stdoutLog) { Clear-Content $stdoutLog -ErrorAction SilentlyContinue }
    & $nssmExe set $script:PostgresServiceName AppStdout $stdoutLog
    & $nssmExe set $script:PostgresServiceName AppStderr $stderrLog
    & $nssmExe set $script:PostgresServiceName Start SERVICE_AUTO_START
    & $nssmExe set $script:PostgresServiceName AppExit Default Restart
    & $nssmExe set $script:PostgresServiceName AppRestartDelay $script:PostgresServiceRestartDelayMs

    Grant-PostgresServiceDirectoryAccess -PgDir $pgDir -NssmDir (Split-Path -Parent $nssmExe) -LogsDir (Get-ProjectLogsDir -ProjectRoot $Root)
    Start-PostgresServiceAndVerify -StderrLog $stderrLog -OkKey 'pg_ok_service_installed_running'
    Sync-PostgresBackupSchedule -Root $Root
}

function Stop-PostgresProcess {
    param(
        [string]$Root = '',
        [switch]$Quiet
    )

    Initialize-PostgresServiceConfig -Root $Root

    $service = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'arrow_stopping_service' -Color Cyan -Param @{ name = 'PostgreSQL' }
        }
        Stop-Service -Name $script:PostgresServiceName -Force
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'ok_service_stopped' -Color Green -Param @{ name = 'PostgreSQL' }
        }
        return
    }

    if ($Root -and (Test-PostgresInstalled -Root $Root)) {
        $dataDir = Get-PostgresDataDir -Root $Root
        $pidFile = Join-Path $dataDir 'postmaster.pid'
        if (Test-Path $pidFile) {
            if (-not $Quiet) {
                Write-ErgomsMessage -Key 'pg_arrow_stop_pg_ctl' -Color Cyan
            }
            Stop-PostgresClusterIfRunning -Root $Root -DataDir $dataDir
        }
    }

    if (-not $Quiet) {
        Write-ErgomsMessage -Key 'ok_stopped' -Color Green -Param @{ name = 'PostgreSQL' }
    }
}

function Start-PostgresProcess {
    param([string]$Root)

    Initialize-PostgresServiceConfig -Root $Root

    if (-not (Test-PostgresInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'PostgreSQL'; cmd = 'ergoms install-postgres' }
        return
    }

    $service = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne 'Running') {
            Start-Service -Name $script:PostgresServiceName
        }
        Write-ErgomsMessage -Key 'pg_ok_service_started' -Color Green
        return
    }

    $pgCtl = Get-PostgresExe -Root $Root -Name 'pg_ctl'
    $dataDir = Get-PostgresDataDir -Root $Root
    $logFile = Join-Path (Get-PostgresDir -Root $Root) 'logs\pg_ctl.log'
    Write-ErgomsMessage -Key 'arrow_starting' -Color Cyan -Param @{ name = 'PostgreSQL' }
    & $pgCtl start -D $dataDir -l $logFile -w -t 60
    if (Test-PostgresPing -Root $Root) {
        Write-ErgomsMessage -Key 'ok_started' -Color Green -Param @{ name = 'PostgreSQL' }
    }
    else {
        Write-ErgomsMessage -Key 'pg_error_start_failed' -Color Red -Stderr
        exit 1
    }
}

function Restart-PostgresProcess {
    param([string]$Root)

    Initialize-PostgresServiceConfig -Root $Root

    if (-not (Test-PostgresInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'PostgreSQL'; cmd = 'ergoms install-postgres' }
        return
    }

    $service = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Restart-Service -Name $script:PostgresServiceName -Force
        Write-ErgomsMessage -Key 'ok_service_restarted' -Color Green -Param @{ name = 'PostgreSQL' }
        return
    }

    Stop-PostgresProcess -Root $Root
    Start-PostgresProcess -Root $Root
}

function Test-PostgresPing {
    param([string]$Root)

    return Invoke-PostgresPythonScript -Root $Root -ScriptName 'install_postgres.py' -ExtraArgs @(
        '--root', $Root,
        '--ping-only'
    )
}

function Migrate-PostgresToPortable {
    param(
        [string]$Root,
        [string[]]$ExtraArgs = @()
    )

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'pg_heading_migrate' -Color Cyan
    Write-ColorOutput '' White

    if (-not (Test-PostgresInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'PostgreSQL'; cmd = 'ergoms install-postgres' }
        exit 1
    }

    $scriptArgs = @('--root', $Root)
    if ($ExtraArgs) {
        $scriptArgs += $ExtraArgs
    }
    $ok = Invoke-PostgresPythonScript -Root $Root -ScriptName 'migrate_postgres_to_portable.py' -ExtraArgs $scriptArgs
    if (-not $ok) {
        exit 1
    }
}

function Get-PostgresYamlDefaultField {
    param(
        [string]$Root,
        [string]$FieldName
    )

    $yamlPath = Join-Path $Root 'databases.yaml'
    if (-not (Test-Path $yamlPath)) { return $null }
    $inDefault = $false
    foreach ($line in Get-Content $yamlPath -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*default:\s*$') { $inDefault = $true; continue }
        if ($inDefault -and $line -match '^\s{2}\w+:\s*$') { break }
        if ($inDefault -and $line -match ("^\s+" + [regex]::Escape($FieldName) + ":\s*(.+)$")) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-PostgresListenPort {
    param([string]$Root)

    $portFile = Join-Path (Get-PostgresDir -Root $Root) 'PORT'
    if (Test-Path $portFile) {
        $raw = (Get-Content $portFile -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($raw -match '^\d+$') { return $raw.Trim() }
    }
    $yamlPort = Get-PostgresYamlDefaultField -Root $Root -FieldName 'port'
    if ($yamlPort -match '^\d+$') { return $yamlPort }
    return '5433'
}

function Get-PostgresListenBind {
    param([string]$Root)

    $yamlHost = Get-PostgresYamlDefaultField -Root $Root -FieldName 'host'
    if ($yamlHost) {
        if ($yamlHost -in @('localhost', '::1')) { return '127.0.0.1' }
        return $yamlHost
    }
    return '127.0.0.1'
}

function Get-PostgresDbAccessDefaults {
    param([string]$Root)

    $name = Get-PostgresYamlDefaultField -Root $Root -FieldName 'name'
    if (-not $name) { $name = 'ergo_ms' }
    $user = Get-PostgresYamlDefaultField -Root $Root -FieldName 'user'
    if (-not $user) { $user = 'postgres' }
    $password = Get-PostgresYamlDefaultField -Root $Root -FieldName 'password'
    if (-not $password) { $password = 'admin' }
    return @{
        Name     = $name
        User     = $user
        Password = $password
    }
}

function Write-PostgresDbAccessSummary {
    param([string]$Root)

    $access = Get-PostgresDbAccessDefaults -Root $Root
    Write-ErgomsMessage -Key 'pg_label_db' -Color Cyan -Param @{ name = $access.Name }
    Write-ErgomsMessage -Key 'pg_label_user' -Color Cyan -Param @{ user = $access.User }
    Write-ErgomsMessage -Key 'pg_label_password' -Color Cyan -Param @{ password = $access.Password }
    Write-ErgomsMessage -Key 'pg_info_credentials_source' -Color Cyan
}

function Write-PostgresYamlPortHint {
    param(
        [string]$Root,
        [string]$ListenPort
    )

    $yamlPort = Get-PostgresYamlDefaultField -Root $Root -FieldName 'port'
    if (-not $yamlPort) {
        Write-ErgomsMessage -Key 'pg_info_set_default_port' -Color Cyan -Param @{ port = $ListenPort }
        return
    }
    if ($yamlPort -ne $ListenPort) {
        Write-ErgomsMessage -Key 'pg_warn_port_mismatch' -Color Yellow -Param @{ yaml_port = $yamlPort; listen_port = $ListenPort }
        Write-ErgomsMessage -Key 'pg_info_reinstall_or_align_port' -Color Cyan
    }
}

function Show-PostgresStatus {
    param([string]$Root)

    Initialize-PostgresServiceConfig -Root $Root

    $pgDir = Get-PostgresDir -Root $Root
    $installed = Test-PostgresInstalled -Root $Root

    if (-not $installed) {
        Write-ErgomsMessage -Key 'component_not_installed' -Color DarkGray -Param @{ name = 'PostgreSQL' }
        Write-ErgomsMessage -Key 'label_expected_path' -Color DarkGray -Param @{ path = $pgDir }
        return
    }

    Write-ColorOutput '' White
    Write-ErgomsMessage -Key 'heading_status' -Color Cyan -Param @{ name = 'PostgreSQL' }

    $service = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if ($service) {
        $statusColor = switch ($service.Status) {
            'Running' { 'Green' }
            'Stopped' { 'Red' }
            default { 'Yellow' }
        }
        Write-ErgomsMessage -Key 'label_service_status' -Color $statusColor -Param @{ name = $script:PostgresServiceName; status = $service.Status }
    }
    else {
        Write-ErgomsMessage -Key 'service_not_registered' -Color Yellow
    }

    $listenPort = Get-PostgresListenPort -Root $Root
    $listenBind = Get-PostgresListenBind -Root $Root
    Write-ErgomsMessage -Key 'label_path_indent2' -Color Cyan -Param @{ path = $pgDir }
    Write-ErgomsMessage -Key 'label_listening_indent2' -Color Cyan -Param @{ addr = "${listenBind}:${listenPort}" }
    Write-PostgresYamlPortHint -Root $Root -ListenPort $listenPort

    if (Test-PostgresPing -Root $Root) {
        Write-ColorOutput '  Ping: OK' Green
    }
    else {
        Write-ErgomsMessage -Key 'ping_failed_server_down' -Color Yellow
    }
}

function Uninstall-Postgres {
    param(
        [string]$Root,
        [switch]$PurgeData
    )

    Initialize-PostgresServiceConfig -Root $Root

    Write-ErgomsMessage -Key 'heading_remove' -Color Cyan -Param @{ name = 'PostgreSQL' }
    Stop-PostgresProcess -Root $Root -Quiet

    $service = Get-Service -Name $script:PostgresServiceName -ErrorAction SilentlyContinue
    if ($service) {
        $nssmExe = Install-NSSM -Root $Root
        & $nssmExe stop $script:PostgresServiceName 2>$null
        & $nssmExe remove $script:PostgresServiceName confirm 2>$null
        Write-ErgomsMessage -Key 'pg_ok_service_removed' -Color Green
    }
    Sync-PostgresBackupSchedule -Root $Root -Uninstall

    $pgDir = Get-PostgresDir -Root $Root
    if ($PurgeData -and (Test-Path $pgDir)) {
        Remove-Item -Path $pgDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-ErgomsMessage -Key 'ok_removed_path' -Color Green -Param @{ path = $pgDir }
    }
    else {
        Write-ErgomsMessage -Key 'ok_stopped_binaries_kept' -Color Green -Param @{ name = 'PostgreSQL'; pkg = 'postgres'; purge_flag = '-Purge' }
    }
}
