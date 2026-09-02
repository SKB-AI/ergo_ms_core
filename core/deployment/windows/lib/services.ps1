# Service management functions

# Функции управления службами



function Install-Service {

    param(

        [string]$ServiceName,

        [string]$Root,

        [string]$NssmExe

    )

    

    # Redis / Meilisearch / Postgres / nginx ставят Ensure*OsServiceStep, не bat-wrapper.
    if (Test-DedicatedInfraServiceName -Name $ServiceName -ProjectRoot $Root) {
        return
    }

    # Check if service already exists

    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    if ($existingService) {

        Write-ErgomsMessage -Key 'service_exists_reinstall' -Color Yellow -Param @{ name = $ServiceName }

        

        # Stop service only if it's running

        if ($existingService.Status -eq 'Running') {

            Write-ErgomsMessage -Key 'svc_stopping' -Color Gray

            & $NssmExe stop $ServiceName 2>$null

            Start-Sleep -Seconds 2

        }

        

        # Remove service

        Write-ErgomsMessage -Key 'svc_removing' -Color Gray

        & $NssmExe remove $ServiceName confirm 2>$null

        Start-Sleep -Seconds 2

    }



    $corePath = Join-Path $Root "core"

    $pythonExe = Join-Path $Root "virtual_env\python\Scripts\python.exe"

    $displayName = "Ergo MS - $ServiceName"



    Write-ErgomsMessage -Key 'svc_installing' -Color Cyan -Param @{ name = $ServiceName }



    $useDirectPython = $false

    $appPath = $null

    $appParams = $null



    if (Test-ErgoServiceRole -Name $ServiceName -Role 'celery_beat') {

        # Используем общий скрипт запуска Beat, чтобы логика кэшей и логирование

        # совпадали с ergoms start-beat / start_celery_beat.py

        $useDirectPython = $true

        $appPath = $pythonExe

        $appParams = "api\scripts\start_celery_beat.py"

    }

    elseif (Test-ErgoServiceRole -Name $ServiceName -Role 'client_dev') {

        $useDirectPython = $true

        $appPath = $pythonExe

        $appParams = "core\deployment\scripts\start_client_if_dev.py"

    }

    elseif ($ServiceName -match '_celery_worker_(.+)$') {

        $useDirectPython = $true

        $workerName = $Matches[1]

        $appPath = $pythonExe

        $appParams = "api\scripts\start_celery_worker.py --worker=$workerName"

    }



    if ($useDirectPython -and (Test-Path $pythonExe)) {

        & $NssmExe install $ServiceName $appPath

        & $NssmExe set $ServiceName AppParameters $appParams

    }

    else {

        $wrapperPath = New-ServiceWrapper -ServiceName $ServiceName -Root $Root

        & $NssmExe install $ServiceName $wrapperPath

    }

    & $NssmExe set $ServiceName DisplayName $displayName

    & $NssmExe set $ServiceName Description "Ergo Management System - $ServiceName"

    if (Test-ErgoServiceRole -Name $ServiceName -Role 'client_dev') {

        & $NssmExe set $ServiceName AppDirectory $Root

    }

    else {

        & $NssmExe set $ServiceName AppDirectory (Join-Path $Root "core")

    }

    $logsDir = Get-ProjectLogsDir -ProjectRoot $Root

    # Redirect both stdout and stderr to the same file (single log per service)

    $singleLog = Join-Path $logsDir "${ServiceName}.log"

    & $NssmExe set $ServiceName AppStdout $singleLog

    & $NssmExe set $ServiceName AppStderr $singleLog

    # Ensure UTF-8 and unbuffered output (fixes delayed/blocked logs in Celery)

    & $NssmExe set $ServiceName AppEnvironmentExtra "PYTHONIOENCODING=UTF-8" "PYTHONUTF8=1" "PYTHONUNBUFFERED=1"

    

    # Set service to auto-start

    & $NssmExe set $ServiceName Start SERVICE_AUTO_START



    # Set restart policy

    & $NssmExe set $ServiceName AppExit Default Restart

    & $NssmExe set $ServiceName AppRestartDelay 5000



    Write-ErgomsMessage -Key 'svc_installed_ok' -Color Green -Param @{ name = $ServiceName }

}



function Disable-ClientServiceIfNginx {

    param([string]$ProjectRoot)



    if (-not (Test-NginxEnabled -ProjectRoot $ProjectRoot)) {

        return

    }



    $clientName = Get-ErgoServiceName -Role 'client_dev' -ProjectRoot $ProjectRoot

    $service = Get-Service -Name $clientName -ErrorAction SilentlyContinue

    if ($service -and $service.Status -ne 'Stopped') {

        Stop-Service -Name $clientName -Force -ErrorAction SilentlyContinue

    }



    Write-NginxSkipClientMessage -ProjectRoot $ProjectRoot

}



function Remove-StaleHostProfileServices {
    param(
        [string]$Root,
        [string[]]$KeepNames,
        [string]$NssmExe
    )
    $keep = @{}
    foreach ($name in @($KeepNames)) {
        if ($name) { $keep[$name] = $true }
    }
    $candidates = @(
        (Get-ErgoServiceName -Role 'api_dev' -ProjectRoot $Root),
        (Get-ErgoServiceName -Role 'client_dev' -ProjectRoot $Root),
        (Get-ErgoServiceName -Role 'media_api' -ProjectRoot $Root),
        (Get-ErgoServiceName -Role 'celery_beat' -ProjectRoot $Root)
    )
    $workerBase = Get-ErgoServiceName -Role 'celery_worker' -ProjectRoot $Root
    foreach ($worker in @(Get-CeleryWorkers -ProjectRoot $Root)) {
        $candidates += "${workerBase}_$worker"
    }
    $candidates += $workerBase
    foreach ($serviceName in $candidates) {
        if ($keep.ContainsKey($serviceName)) { continue }
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if (-not $service) { continue }
        Write-ErgomsMessage -Key 'svc_removing_named' -Color Gray -Param @{ name = $serviceName }
        try {
            if ($service.Status -ne 'Stopped') {
                Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
                Wait-ServiceStopped -ServiceName $serviceName -TimeoutSeconds 20 | Out-Null
            }
            if ($NssmExe -and (Test-Path $NssmExe)) {
                & $NssmExe remove $serviceName confirm 2>&1 | Out-Null
            }
            if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
                sc.exe delete $serviceName 2>$null
            }
            Write-ErgomsMessage -Key 'svc_removed_ok' -Color Green -Param @{ name = $serviceName }
        } catch {
            Write-ErgomsMessage -Key 'svc_remove_failed' -Color Red -Stderr -Param @{ name = $serviceName; error = $_.Exception.Message }
        }
    }
}

function Install-AllServices {

    param([string]$Root)



    Test-ProjectStructure -Root $Root

    

    # Create logs directory

    $logsDir = Get-ProjectLogsDir -ProjectRoot $Root

    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null



    # Install NSSM

    $nssmExe = Install-NSSM -Root $Root



    if (Test-NginxEnabled -ProjectRoot $Root) {

        Disable-ClientServiceIfNginx -ProjectRoot $Root

    }



    # Get service names dynamically based on config

    $serviceNames = Get-ServiceNames -ProjectRoot $Root

    Remove-StaleHostProfileServices -Root $Root -KeepNames $serviceNames -NssmExe $nssmExe

    

    Write-ErgomsMessage -Key 'svc_installing_list' -Color Cyan -Param @{ items = ($serviceNames -join ', ') }

    

    foreach ($serviceName in $serviceNames) {

        Install-Service -ServiceName $serviceName -Root $Root -NssmExe $nssmExe

    }



    Write-Host ""; Write-ErgomsMessage -Key 'svc_all_installed_ok' -Color Green

    Write-ErgomsMessage -Key 'svc_logs_dir' -Color Cyan -Param @{ path = $logsDir }

}



function Install-SingleService {

    param(

        [string]$ServiceName,

        [string]$Root

    )



    if ((Test-ErgoServiceRole -Name $ServiceName -Role 'client_dev') -and (Test-NginxEnabled -ProjectRoot $Root)) {

        Disable-ClientServiceIfNginx -ProjectRoot $Root

        return

    }



    Test-ProjectStructure -Root $Root

    

    # Create logs directory

    $logsDir = Get-ProjectLogsDir -ProjectRoot $Root

    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null



    # Install NSSM

    $nssmExe = Install-NSSM -Root $Root



    # Install single service

    Install-Service -ServiceName $ServiceName -Root $Root -NssmExe $nssmExe



    Write-Host ""; Write-ErgomsMessage -Key 'svc_installed_success' -Color Green -Param @{ name = $ServiceName }

    Write-ErgomsMessage -Key 'svc_logs_dir' -Color Cyan -Param @{ path = $logsDir }

}



# Установка всех воркеров из конфигурации

function Install-WorkerServices {

    param([string]$Root)



    Test-ProjectStructure -Root $Root

    

    # Create logs directory

    $logsDir = Get-ProjectLogsDir -ProjectRoot $Root

    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null



    # Install NSSM

    $nssmExe = Install-NSSM -Root $Root



    # Get worker service names

    $workerServices = Get-WorkerServiceNames -ProjectRoot $Root

    

    Write-ErgomsMessage -Key 'svc_installing_workers' -Color Cyan -Param @{ items = ($workerServices -join ', ') }

    

    foreach ($serviceName in $workerServices) {

        Install-Service -ServiceName $serviceName -Root $Root -NssmExe $nssmExe

    }



    Write-Host ""; Write-ErgomsMessage -Key 'svc_workers_installed_ok' -Color Green

}



function Test-OsServiceInstalled {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $null -ne (Get-Service -Name $Name -ErrorAction SilentlyContinue)
}


function Start-AllServices {
    param([string]$ProjectRoot)

    $serviceNames = @(Get-ServiceNames -ProjectRoot $ProjectRoot)
    $planned = New-Object System.Collections.Generic.List[string]
    if (Test-RedisEnabled -ProjectRoot $ProjectRoot) { [void]$planned.Add('Redis') }
    if (Test-SearchEnabled -ProjectRoot $ProjectRoot) { [void]$planned.Add('Meilisearch') }
    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        if (-not (Test-OsServiceInstalled -Name $serviceName)) { continue }
        [void]$planned.Add($serviceName)
    }
    Write-ErgomsMessage -Key 'svc_starting_all' -Color Cyan -Param @{
        count = $planned.Count
        items = ($planned -join ', ')
    }

    $started = 0
    $already = 0
    $missing = 0
    $failed = 0

    if (Test-RedisEnabled -ProjectRoot $ProjectRoot) {
        if (Get-Command Start-RedisProcess -ErrorAction SilentlyContinue) {
            try {
                $redisSvc = Get-Service -Name 'ergo_ms_redis' -ErrorAction SilentlyContinue
                if ($redisSvc -and $redisSvc.Status -eq 'Running') {
                    Start-RedisProcess -Root $ProjectRoot
                    $already++
                }
                else {
                    Start-RedisProcess -Root $ProjectRoot
                    $started++
                }
            } catch {
                Write-ErgomsMessage -Key 'svc_start_failed' -Color Red -Stderr -Param @{ name = 'ergo_ms_redis'; error = $_.Exception.Message }
                $failed++
            }
        }
    }

    if (Test-SearchEnabled -ProjectRoot $ProjectRoot) {
        if (Get-Command Start-MeilisearchProcess -ErrorAction SilentlyContinue) {
            try {
                $meiliSvc = Get-Service -Name 'ergo_ms_meilisearch' -ErrorAction SilentlyContinue
                if ($meiliSvc -and $meiliSvc.Status -eq 'Running') {
                    Start-MeilisearchProcess -Root $ProjectRoot
                    $already++
                }
                else {
                    Start-MeilisearchProcess -Root $ProjectRoot
                    $started++
                }
            } catch {
                Write-ErgomsMessage -Key 'svc_start_failed' -Color Red -Stderr -Param @{ name = 'ergo_ms_meilisearch'; error = $_.Exception.Message }
                $failed++
            }
        }
    }

    $pending = New-Object System.Collections.Generic.List[object]
    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if (-not $service) {
            continue
        }
        if ($service.Status -eq 'Running') {
            Write-ErgomsMessage -Key 'ok_service_already_running' -Color Green -Param @{ name = $serviceName }
            $already++
            continue
        }
        try {
            $service.Start()
            [void]$pending.Add($service)
        }
        catch {
            Write-ErgomsMessage -Key 'svc_start_failed' -Color Red -Stderr -Param @{ name = $serviceName; error = $_.Exception.Message }
            $failed++
        }
    }
    foreach ($service in $pending) {
        try {
            $service.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(45))
            Write-ErgomsMessage -Key 'svc_started_ok' -Color Green -Param @{ name = $service.Name }
            $started++
        }
        catch {
            Write-ErgomsMessage -Key 'svc_start_failed' -Color Red -Stderr -Param @{ name = $service.Name; error = $_.Exception.Message }
            $failed++
        }
    }

    Write-ErgomsMessage -Key 'svc_start_summary' -Color Green -Param @{
        started = $started
        already = $already
        missing = $missing
        failed = $failed
    }
}


function Stop-AllServices {
    param([string]$ProjectRoot)

    $serviceNames = @(Get-ServiceNames -ProjectRoot $ProjectRoot)
    $planned = New-Object System.Collections.Generic.List[string]
    if (Test-RedisEnabled -ProjectRoot $ProjectRoot) { [void]$planned.Add('Redis') }
    if (Test-SearchEnabled -ProjectRoot $ProjectRoot) { [void]$planned.Add('Meilisearch') }
    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        if (-not (Test-OsServiceInstalled -Name $serviceName)) { continue }
        [void]$planned.Add($serviceName)
    }
    Write-ErgomsMessage -Key 'svc_stopping_all' -Color Cyan -Param @{
        count = $planned.Count
        items = ($planned -join ', ')
    }

    $stopped = 0
    $skipped = 0
    $missing = 0
    $failed = 0
    # Снимок Running: на Linux unit'ы с Requires гаснут каскадом; на Windows — та же логика учёта.
    $wasActive = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        $svcSnap = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($svcSnap -and $svcSnap.Status -ne 'Stopped') {
            [void]$wasActive.Add($serviceName)
        }
    }

    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        try {
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if (-not $service) {
                continue
            }
            elseif ($service.Status -ne 'Stopped') {
                Stop-Service -Name $serviceName -Force
                Write-ErgomsMessage -Key 'svc_stopped_ok' -Color Green -Param @{ name = $serviceName }
                $stopped++
            }
            elseif ($wasActive.Contains($serviceName)) {
                Write-ErgomsMessage -Key 'svc_stopped_cascade' -Color Green -Param @{ name = $serviceName }
                $stopped++
            }
            else {
                $skipped++
            }
        }
        catch {
            Write-ErgomsMessage -Key 'svc_stop_failed' -Color Red -Stderr -Param @{ name = $serviceName; error = $_.Exception.Message }
            $failed++
        }
    }

    if (Get-Command ergoms -ErrorAction SilentlyContinue) {
        foreach ($pair in @(Get-ModuleHostStopPairs -ProjectRoot $ProjectRoot)) {
            if (-not $pair) { continue }
            $pairCmd = "$($pair.Command)".Trim()
            if (-not $pairCmd) { continue }
            $needStop = $true
            $pairUnits = @($pair.Units)
            if ($pairUnits.Count -gt 0) {
                $needStop = $false
                foreach ($unitName in $pairUnits) {
                    $svc = Get-Service -Name $unitName -ErrorAction SilentlyContinue
                    if (-not $svc) {
                        $needStop = $true
                        break
                    }
                    if ($svc.Status -ne 'Stopped') {
                        $needStop = $true
                        break
                    }
                }
            }
            if (-not $needStop) { continue }
            try {
                Push-Location $ProjectRoot
                try { ergoms $pairCmd } finally { Pop-Location }
            } catch {
                Write-ErgomsMessage -Key 'svc_stop_failed' -Color Red -Stderr -Param @{ name = $pairCmd; error = $_.Exception.Message }
            }
        }
    }

    if (Test-SearchEnabled -ProjectRoot $ProjectRoot) {
        if (Get-Command Stop-MeilisearchProcess -ErrorAction SilentlyContinue) {
            try {
                $meiliSvc = Get-Service -Name 'ergo_ms_meilisearch' -ErrorAction SilentlyContinue
                $wasRunning = $meiliSvc -and $meiliSvc.Status -ne 'Stopped'
                if ($wasRunning) {
                    Stop-MeilisearchProcess -Root $ProjectRoot -Quiet
                    $stopped++
                }
                else {
                    $skipped++
                }
            } catch {
                Write-ErgomsMessage -Key 'svc_stop_failed' -Color Red -Stderr -Param @{ name = 'ergo_ms_meilisearch'; error = $_.Exception.Message }
                $failed++
            }
        }
    }

    if (Test-RedisEnabled -ProjectRoot $ProjectRoot) {
        if (Get-Command Stop-RedisProcess -ErrorAction SilentlyContinue) {
            try {
                $redisSvc = Get-Service -Name 'ergo_ms_redis' -ErrorAction SilentlyContinue
                $wasRunning = $redisSvc -and $redisSvc.Status -ne 'Stopped'
                if ($wasRunning) {
                    Stop-RedisProcess -Root $ProjectRoot -Quiet
                    $stopped++
                }
                else {
                    $skipped++
                }
            } catch {
                Write-ErgomsMessage -Key 'svc_stop_failed' -Color Red -Stderr -Param @{ name = 'ergo_ms_redis'; error = $_.Exception.Message }
                $failed++
            }
        }
    }

    Write-ErgomsMessage -Key 'svc_stop_summary' -Color Green -Param @{
        stopped = $stopped
        skipped = $skipped
        missing = $missing
        failed = $failed
    }
}


function Restart-AllServices {
    param([string]$ProjectRoot)

    $serviceNames = @(Get-ServiceNames -ProjectRoot $ProjectRoot)
    $planned = New-Object System.Collections.Generic.List[string]
    if (Test-RedisEnabled -ProjectRoot $ProjectRoot) { [void]$planned.Add('Redis') }
    if (Test-SearchEnabled -ProjectRoot $ProjectRoot) { [void]$planned.Add('Meilisearch') }
    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        if (-not (Test-OsServiceInstalled -Name $serviceName)) { continue }
        [void]$planned.Add($serviceName)
    }
    Write-ErgomsMessage -Key 'svc_restarting_all' -Color Cyan -Param @{
        count = $planned.Count
        items = ($planned -join ', ')
    }

    $restarted = 0
    $missing = 0
    $failed = 0

    if (Test-RedisEnabled -ProjectRoot $ProjectRoot) {
        if (Get-Command Restart-RedisProcess -ErrorAction SilentlyContinue) {
            try {
                Restart-RedisProcess -Root $ProjectRoot
                $restarted++
            } catch {
                Write-ErgomsMessage -Key 'svc_restart_failed' -Color Red -Stderr -Param @{ name = 'ergo_ms_redis'; error = $_.Exception.Message }
                $failed++
            }
        }
    }

    if (Test-SearchEnabled -ProjectRoot $ProjectRoot) {
        if (Get-Command Restart-MeilisearchProcess -ErrorAction SilentlyContinue) {
            try {
                Restart-MeilisearchProcess -Root $ProjectRoot
                $restarted++
            } catch {
                Write-ErgomsMessage -Key 'svc_restart_failed' -Color Red -Stderr -Param @{ name = 'ergo_ms_meilisearch'; error = $_.Exception.Message }
                $failed++
            }
        }
    }

    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_redis' -or $serviceName -eq 'ergo_ms_meilisearch') { continue }
        try {
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($service) {
                Restart-Service -Name $serviceName -Force
                Write-ErgomsMessage -Key 'svc_restarted_ok' -Color Green -Param @{ name = $serviceName }
                $restarted++
            }
        }
        catch {
            Write-ErgomsMessage -Key 'svc_restart_failed' -Color Red -Stderr -Param @{ name = $serviceName; error = $_.Exception.Message }
            $failed++
        }
    }

    Write-ErgomsMessage -Key 'svc_restart_summary' -Color Green -Param @{
        restarted = $restarted
        missing = $missing
        failed = $failed
    }
}


function Start-WorkerServices {

    param([string]$ProjectRoot)

    

    Write-ErgomsMessage -Key 'svc_starting_workers' -Color Cyan

    $workerServices = Get-WorkerServiceNames -ProjectRoot $ProjectRoot

    foreach ($serviceName in $workerServices) {

        try {

            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

            if ($service) {

                Start-Service -Name $serviceName

                Write-ErgomsMessage -Key 'svc_started_ok' -Color Green -Param @{ name = $serviceName }

            }

            else {

                Write-ErgomsMessage -Key 'svc_not_installed_dash' -Color Gray -Param @{ name = $serviceName }

            }

        }

        catch {

            Write-ErgomsMessage -Key 'svc_start_failed' -Color Red -Stderr -Param @{ name = $serviceName; error = $_.Exception.Message }

        }

    }

}



function Show-ServicesStatus {
    param([string]$ProjectRoot)

    Write-Host ""; Write-ErgomsMessage -Key 'svc_status_heading' -Color Cyan
    Write-ColorOutput ""

    $serviceNames = Get-ServiceNames -ProjectRoot $ProjectRoot
    foreach ($serviceName in $serviceNames) {
        if ($serviceName -eq 'ergo_ms_meilisearch') {
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($service) {
                $statusColor = switch ($service.Status) {
                    'Running' { 'Green' }
                    'Stopped' { 'Red' }
                    default { 'Yellow' }
                }
                Write-Host "  $serviceName : " -NoNewline
                Write-ColorOutput "$($service.Status)" $statusColor
            }
            else {
                Write-Host "  $serviceName : " -NoNewline
                Write-ColorOutput 'NotInstalled' Gray
            }
            continue
        }
        if ($serviceName -eq 'ergo_ms_redis') {
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($service) {
                $statusColor = switch ($service.Status) {
                    'Running' { 'Green' }
                    'Stopped' { 'Red' }
                    default { 'Yellow' }
                }
                Write-Host "  $serviceName : " -NoNewline
                Write-ColorOutput "$($service.Status)" $statusColor
            }
            elseif (Get-Command Show-RedisStatus -ErrorAction SilentlyContinue) {
                Show-RedisStatus -Root $ProjectRoot
            }
            else {
                Write-Host "  $serviceName : " -NoNewline
                Write-ErgomsMessage -Key 'svc_status_not_installed' -Color DarkGray
            }
            continue
        }

        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($service) {
            $statusColor = switch ($service.Status) {
                'Running' { 'Green' }
                'Stopped' { 'Red' }
                default { 'Yellow' }
            }
            Write-Host "  $serviceName : " -NoNewline
            Write-ColorOutput "$($service.Status)" $statusColor
        }
        else {
            Write-Host "  $serviceName : " -NoNewline
            Write-ErgomsMessage -Key 'svc_status_not_installed' -Color DarkGray
        }
    }

    Write-ColorOutput ""
    Write-ErgomsMessage -Key 'label_logs_short' -Color Cyan -Param @{ path = 'logs\' }
}

function Show-ServiceLogs {

    param(

        [string]$ServiceName,

        [int]$Lines = 500,

        [string]$ProjectRoot = ""

    )

    

    # Ensure console outputs UTF-8 so special symbols display correctly

    try {

        $OutputEncoding = [System.Text.Encoding]::UTF8

        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    } catch {}



    if (-not $ProjectRoot) {

        $ProjectRoot = Get-ProjectRoot

    }

    

    # Portable / default DB logs live outside logs/; follow via start-db-dev.

    if (
        (Test-ErgoServiceRole -Name $ServiceName -Role 'postgres') -or
        (Test-ErgoServiceRole -Name $ServiceName -Role 'db') -or
        $ServiceName -eq 'ergo-postgres' -or
        (Test-ErgoServiceRole -Name $ServiceName -Role 'sqlite') -or
        (Test-ErgoServiceRole -Name $ServiceName -Role 'mysql') -or
        (Test-ErgoServiceRole -Name $ServiceName -Role 'mssql')
    ) {

        $py = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'

        $script = Join-Path $ProjectRoot 'core\deployment\scripts\start_db_logs_dev.py'

        if (-not (Test-Path $py)) {

            Write-ErgomsMessage -Key 'svc_log_file_not_found' -Color Red -Stderr -Param @{ path = $script }

            exit 1

        }

        & $py $script

        exit $LASTEXITCODE

    }

    if (Test-ErgoServiceRole -Name $ServiceName -Role 'nginx') {

        $py = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'

        $script = Join-Path $ProjectRoot 'core\deployment\scripts\start_nginx_logs_dev.py'

        if (-not (Test-Path $py)) {

            Write-ErgomsMessage -Key 'svc_log_file_not_found' -Color Red -Stderr -Param @{ path = $script }

            exit 1

        }

        & $py -u $script $Lines

        exit $LASTEXITCODE

    }

    

    $logsDir = Get-ProjectLogsDir -ProjectRoot $ProjectRoot
    $logPath = $null
    $pythonExe = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'
    $pathsScript = Join-Path $ProjectRoot 'core\deployment\scripts\logs_paths.py'
    if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $pathsScript)) {
        $resolved = & $pythonExe $pathsScript service $ServiceName $ProjectRoot 2>$null
        if ($resolved) {
            $first = @($resolved | Where-Object { $_ -and $_.Trim() }) | Select-Object -First 1
            if ($first) {
                $logPath = $first.Trim()
            }
        }
    }
    if (-not $logPath) {
        $logPath = Join-Path $logsDir "${ServiceName}.log"
    }

    

    if (-not (Test-Path $logPath)) {

        Write-ErgomsMessage -Key 'svc_log_file_not_found' -Color Red -Stderr -Param @{ path = $logPath }

        Write-ErgomsMessage -Key 'svc_logs_written_as_windows_service' -Color Gray

        Write-ErgomsMessage -Key 'svc_logs_vscode_terminal' -Color Gray

        exit 1

    }

    

    $fileInfo = Get-Item $logPath

    $isEmpty = $fileInfo.Length -eq 0

    Write-ErgomsMessage -Key 'svc_tail_log' -Color Cyan -Param @{ lines = $Lines; name = $ServiceName }

    Write-ErgomsMessage -Key 'svc_log_file_label' -Color Gray -Param @{ path = $logPath }

    if ($isEmpty) {

        Write-ErgomsMessage -Key 'svc_log_file_empty' -Color Yellow

        Write-ErgomsMessage -Key 'svc_log_hint_windows_service' -Color Gray

        Write-ErgomsMessage -Key 'svc_logs_vscode_start_all' -Color Gray

        Write-ColorOutput ""

        Write-ErgomsMessage -Key 'svc_tail_follow_wait' -Color Gray

    }

    Write-ColorOutput ""

    

    # Read log as UTF-8 to display special symbols correctly in Windows PowerShell

    Get-Content -Path $logPath -Tail $Lines -Wait -Encoding UTF8

}



function Wait-ServiceStopped {

    param(

        [string]$ServiceName,

        [int]$TimeoutSeconds = 30

    )

    

    $startTime = Get-Date

    $timeout = (Get-Date).AddSeconds($TimeoutSeconds)

    

    while ((Get-Date) -lt $timeout) {

        $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

        if (-not $service) {

            # Service doesn't exist anymore, consider it stopped

            return $true

        }

        

        $status = $service.Status

        if ($status -eq 'Stopped') {

            return $true

        }

        

        # Wait a bit before checking again

        Start-Sleep -Milliseconds 500

    }

    

    # Timeout reached

    return $false

}



function Uninstall-AllServices {

    param(

        [bool]$PurgeData,

        [string]$ProjectRoot

    )



    Write-ErgomsMessage -Key 'svc_removing_all' -Color Yellow

    

    $nssmDir = Get-NssmDir -Root $ProjectRoot

    $nssmExe = Join-Path $nssmDir "nssm.exe"

    

    $serviceNames = Get-ServiceNames -ProjectRoot $ProjectRoot

    foreach ($serviceName in $serviceNames) {

        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

        if ($service) {

            try {

                # Handle service stopping - check status and wait if needed

                $currentStatus = $service.Status

                if ($currentStatus -eq 'Running' -or $currentStatus -eq 'StartPending') {

                    Write-ErgomsMessage -Key 'svc_stopping_named' -Color Gray -Param @{ name = $serviceName }

                    # Use Stop-Service which handles StopPending state better than nssm

                    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue

                }

                elseif ($currentStatus -eq 'StopPending') {

                    Write-ErgomsMessage -Key 'svc_stopping_wait' -Color Gray -Param @{ name = $serviceName }

                }

                

                # Wait for service to fully stop (if not already stopped)

                if ($currentStatus -ne 'Stopped') {

                    $stopped = Wait-ServiceStopped -ServiceName $serviceName -TimeoutSeconds 30

                    if (-not $stopped) {

                        Write-ErgomsMessage -Key 'svc_force_stop_timeout' -Color Yellow -Param @{ name = $serviceName }

                        Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue

                        Start-Sleep -Seconds 2

                    }

                }

                

                # Remove service

                Write-ErgomsMessage -Key 'svc_removing_named' -Color Gray -Param @{ name = $serviceName }

                if (Test-Path $nssmExe) {

                    & $nssmExe remove $serviceName confirm 2>&1 | Out-Null

                    if ($LASTEXITCODE -ne 0) {

                        Write-ErgomsMessage -Key 'svc_nssm_remove_fallback' -Color Yellow

                        sc.exe delete $serviceName 2>$null

                    }

                }

                else {

                    sc.exe delete $serviceName 2>$null

                }

                

                Write-ErgomsMessage -Key 'svc_removed_ok' -Color Green -Param @{ name = $serviceName }

            }

            catch {

                Write-ErgomsMessage -Key 'svc_remove_failed' -Color Red -Stderr -Param @{ name = $serviceName; error = $_.Exception.Message }

            }

        }

        else {

            Write-ErgomsMessage -Key 'svc_not_found_dash' -Color Gray -Param @{ name = $serviceName }

        }

    }

    # Старые имена ergo-* — только у рабочего префикса, иначе снимем чужие тесты
    $prefix = Get-ErgoServicePrefix -ProjectRoot $ProjectRoot
    $legacy = @()
    if ($prefix -eq 'ergo_ms') {
        $legacy = Get-Service -Name 'ergo-*' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike 'ergo_ms_*' }
    }
    foreach ($svc in $legacy) {
        try {
            if ($svc.Status -ne 'Stopped') {
                Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
                Wait-ServiceStopped -ServiceName $svc.Name -TimeoutSeconds 15 | Out-Null
            }
            Write-ErgomsMessage -Key 'svc_removing_legacy' -Color Gray -Param @{ name = $svc.Name }
            if (Test-Path $nssmExe) {
                & $nssmExe remove $svc.Name confirm 2>&1 | Out-Null
            }
            if (Get-Service -Name $svc.Name -ErrorAction SilentlyContinue) {
                sc.exe delete $svc.Name 2>$null
            }
            Write-ErgomsMessage -Key 'svc_legacy_removed_ok' -Color Green -Param @{ name = $svc.Name }
        }
        catch {
            Write-ErgomsMessage -Key 'svc_legacy_remove_failed' -Color Yellow -Param @{ name = $svc.Name; error = $_.Exception.Message }
        }
    }

    if ($PurgeData) {

        Write-ErgomsMessage -Key 'svc_removing_config_data' -Color Yellow

        $dataDir = "$env:ProgramData\ergo_ms"

        if (Test-Path $dataDir) {

            Remove-Item $dataDir -Recurse -Force

            Write-ErgomsMessage -Key 'ok_removed_path' -Color Green -Param @{ path = $dataDir }

        }

        

        # Also remove project logs and wrappers if they exist

        if ($ProjectRoot) {

            $projectLogsDir = Get-ProjectLogsDir -ProjectRoot $ProjectRoot

            if (Test-Path $projectLogsDir) {

                Remove-Item $projectLogsDir -Recurse -Force

                Write-ErgomsMessage -Key 'svc_project_logs_removed' -Color Green -Param @{ path = $projectLogsDir }

            }

        }

    }



    Write-ErgomsMessage -Key 'svc_remove_done' -Color Green

}



# Export-ModuleMember -Function *  # Удалено, так как это не модуль

