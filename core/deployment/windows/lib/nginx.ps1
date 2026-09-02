. "$PSScriptRoot/nginx_common.ps1"

function Render-NginxTemplate {
    param(
        [string]$TemplatePath,
        [string]$Root,
        [string]$ServerName = 'localhost',
        [string]$ListenHost = '0.0.0.0',
        [string]$ListenPort = '80',
        [string]$SslCert = '',
        [string]$SslKey = '',
        [bool]$UseHttps = $false
    )

    $pythonExe = Join-Path $Root "virtual_env\python\Scripts\python.exe"
    $renderScript = Join-Path $Root "core\deployment\scripts\render_nginx_config.py"
    if ((Test-Path $pythonExe) -and (Test-Path $renderScript)) {
        $useHttpsArg = if ($UseHttps) { 'true' } else { 'false' }
        $arguments = @(
            $renderScript,
            '--template', $TemplatePath,
            '--root', $Root,
            '--server-name', $ServerName,
            '--listen-host', $ListenHost,
            '--listen-port', $ListenPort,
            '--use-https', $useHttpsArg
        )
        if ($SslCert) {
            $arguments += @('--ssl-cert', $SslCert)
        }
        if ($SslKey) {
            $arguments += @('--ssl-key', $SslKey)
        }

        $output = & $pythonExe @arguments 2>&1
        if ($LASTEXITCODE -eq 0 -and $output) {
            if ($output -is [System.Array]) {
                return ($output -join "`n")
            }
            return [string]$output
        }
        if ($LASTEXITCODE -ne 0 -and $output) {
            Write-ErgomsMessage -Key 'warning_render_nginx_fallback' -Color Yellow
            $output | ForEach-Object { Write-ColorOutput $_ Yellow }
        }
    }

    $snippetsDir = Join-Path $Root "core/deployment/nginx/snippets"
    $rootForward = $Root -replace '\\', '/'
    $snippetsForward = $snippetsDir -replace '\\', '/'

    $content = Get-Content -Path $TemplatePath -Raw -Encoding UTF8
    $maintenanceSnippetPath = Join-Path $Root "core\deployment\nginx\snippets\maintenance.conf"
    $maintenanceSnippet = ''
    if (Test-Path $maintenanceSnippetPath) {
        $maintenanceSnippet = (Get-Content -Path $maintenanceSnippetPath -Raw -Encoding UTF8) -replace '\$\{ERGO_ROOT\}', $rootForward
    }
    $content = $content -replace '\$\{ERGO_ROOT\}', $rootForward
    $content = $content -replace '\$\{ERGO_SERVER_NAME\}', $ServerName
    $content = $content -replace '\$\{ERGO_LISTEN_HOST\}', $ListenHost
    $content = $content -replace '\$\{ERGO_LISTEN_PORT\}', $ListenPort
    $content = $content -replace '\$\{ERGO_NGINX_SNIPPETS\}', $snippetsForward
    $content = $content -replace '\$\{ERGO_SSL_CERT\}', ($SslCert -replace '\\', '/')
    $content = $content -replace '\$\{ERGO_SSL_KEY\}', ($SslKey -replace '\\', '/')
    $content = $content -replace '\$\{ERGO_HOST_POLICY_BLOCKS\}', ''
    $content = $content -replace '\$\{ERGO_HTTP_CANONICAL_REDIRECT\}', 'https://$host$request_uri'
    $content = $content -replace '\$\{ERGO_MAINTENANCE_SNIPPET\}', $maintenanceSnippet
    $content = $content -replace '\$\{ERGO_JUPYTER_UPSTREAM\}', ''
    $content = $content -replace '\$\{ERGO_JUPYTER_LOCATION\}', ''

    return $content
}

function Install-NginxConfig {
    param(
        [string]$Root,
        [string]$ServerName = 'localhost',
        [string]$ListenHost = '0.0.0.0',
        [string]$ListenPort = '80',
        [hashtable]$EnvVars = @{}
    )

    $nginxDir = Get-NginxDir -Root $Root
    $nginxExe = Install-NginxBinary -Root $Root

    $templatePath = Get-NginxTemplatePath -Root $Root -EnvVars $EnvVars -ListenPort $ListenPort
    if (-not (Test-Path $templatePath)) {
        Write-ErgomsMessage -Key 'error_template_not_found' -Color Red -Stderr -Param @{ path = $templatePath }
        throw "Template not found"
    }

    $useHttps = Test-NginxUseHttps -EnvVars $EnvVars -ListenPort $ListenPort
    $sslCert = if ($EnvVars['ERGO_SSL_CERT']) { $EnvVars['ERGO_SSL_CERT'] } else { '' }
    $sslKey = if ($EnvVars['ERGO_SSL_KEY']) { $EnvVars['ERGO_SSL_KEY'] } else { '' }
    if ($useHttps) {
        Warn-NginxInsecureCerts -CertPath $sslCert -KeyPath $sslKey
    }

    $distPath = Join-Path $Root "core\client\dist"
    if (-not (Test-Path (Join-Path $distPath "index.html"))) {
        Write-ErgomsMessage -Key 'error_index_html_not_found' -Color Red -Stderr -Param @{ path = $distPath }
        Write-ErgomsMessage -Key 'nginx_need_client_build' -Color Yellow
        Write-ColorOutput "    ergoms client-build" Yellow
        Write-ErgomsMessage -Key 'hint_then_install_nginx' -Color Yellow
        throw "Client build not found"
    }

    $rendered = Render-NginxTemplate -TemplatePath $templatePath -Root $Root `
        -ServerName $ServerName -ListenHost $ListenHost -ListenPort $ListenPort `
        -SslCert $sslCert -SslKey $sslKey -UseHttps:$useHttps

    $confDir = Join-Path $nginxDir "conf"
    $confPath = Join-Path $confDir "${script:NginxConfName}.conf"
    New-Item -ItemType Directory -Path $confDir -Force | Out-Null

    [System.IO.File]::WriteAllText($confPath, $rendered, [System.Text.UTF8Encoding]::new($false))
    Write-ErgomsMessage -Key 'ok_config_written' -Color Green -Param @{ path = $confPath }

    $mainConf = Join-Path $confDir "nginx.conf"
    Write-NginxMainConfig -Root $Root -MainConfPath $mainConf -IncludeConfPath $confPath -NginxDir $nginxDir

    Write-ErgomsMessage -Key 'arrow_checking_nginx_config' -Color Cyan
    $testResult = Invoke-NginxCli -NginxExe $nginxExe -Arguments @('-t', '-c', $mainConf) -WorkingDirectory $nginxDir
    if ($testResult.ExitCode -ne 0) {
        Write-ErgomsMessage -Key 'error_nginx_t_failed' -Color Red -Stderr
        Write-ColorOutput ($testResult.Output -join "`n") Red
        throw "Nginx config test failed"
    }
    Write-ErgomsMessage -Key 'ok_config_valid' -Color Green

    return @{
        NginxExe = $nginxExe
        MainConf = $mainConf
        SiteConf = $confPath
        ServerName = $ServerName
        ListenPort = $ListenPort
    }
}

function Write-NginxMainConfig {
    param(
        [string]$Root,
        [string]$MainConfPath,
        [string]$IncludeConfPath,
        [string]$NginxDir
    )

    $includeForward = $IncludeConfPath -replace '\\', '/'
    $runtimeLogsDir = (Join-Path $NginxDir 'logs') -replace '\\', '/'
    $centralLogsDir = (Get-NginxCentralLogsDir -Root $Root) -replace '\\', '/'
    $tempDir = (Join-Path $NginxDir 'temp') -replace '\\', '/'

    $errorLogPath = (Invoke-NginxLogEnv -Root $Root -Command 'path' -Key 'NGINX_ERROR') -replace '\\', '/'
    if (-not $errorLogPath) {
        $errorLogPath = "$centralLogsDir/nginx-error.log"
    }

    $nginxErrorLevel = Invoke-NginxLogEnv -Root $Root -Command 'nginx-error-level'
    if (-not $nginxErrorLevel) {
        $nginxErrorLevel = 'warn'
    }

    $accessEnabled = Invoke-NginxLogEnv -Root $Root -Command 'nginx-access-enabled'
    if ($accessEnabled -eq 'false') {
        $accessLogLine = 'access_log off;'
    }
    else {
        $accessLogPath = (Invoke-NginxLogEnv -Root $Root -Command 'path' -Key 'NGINX_ACCESS') -replace '\\', '/'
        if (-not $accessLogPath) {
            $accessLogPath = "$centralLogsDir/nginx-access.log"
        }
        $accessLogLine = "access_log $accessLogPath;"
    }

    New-Item -ItemType Directory -Path (Join-Path $NginxDir 'logs') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $NginxDir 'temp') -Force | Out-Null
    New-Item -ItemType Directory -Path $centralLogsDir -Force | Out-Null

    $mainContent = @"
worker_processes auto;

error_log $errorLogPath $nginxErrorLevel;
pid       $runtimeLogsDir/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    $accessLogLine

    sendfile        on;
    keepalive_timeout 65;

    client_body_temp_path $tempDir/client_body;
    proxy_temp_path       $tempDir/proxy;
    fastcgi_temp_path     $tempDir/fastcgi;
    uwsgi_temp_path       $tempDir/uwsgi;
    scgi_temp_path        $tempDir/scgi;

    include $includeForward;
}
"@

    [System.IO.File]::WriteAllText($MainConfPath, $mainContent, [System.Text.UTF8Encoding]::new($false))
}

function Install-Nginx {
    param(
        [string]$Root,
        [string]$ServerName = '',
        [string]$ListenPort = '',
        [switch]$AsService
    )

    $ensureScript = Join-Path $Root "core\deployment\scripts\ensure_nginx_env.py"
    $pythonExe = Join-Path $Root "virtual_env\python\Scripts\python.exe"
    if ((Test-Path $ensureScript) -and (Test-Path $pythonExe)) {
        & $pythonExe $ensureScript 2>$null | Out-Host
    }

    $envVars = Read-NginxEnv -Root $Root
    if (-not $ServerName) {
        if ($envVars['NGINX_PUBLIC_HOST']) {
            $ServerName = $envVars['NGINX_PUBLIC_HOST']
        } elseif ($envVars['NGINX_SERVER_NAME']) {
            $ServerName = $envVars['NGINX_SERVER_NAME']
        } else {
            $ServerName = 'localhost'
        }
    }
    $ListenHost = if ($envVars['NGINX_LISTEN_HOST']) { $envVars['NGINX_LISTEN_HOST'] } else { '0.0.0.0' }
    if (-not $ListenPort) { $ListenPort = if ($envVars['NGINX_LISTEN_PORT']) { $envVars['NGINX_LISTEN_PORT'] } else { '80' } }

    Write-ColorOutput "" White
    Write-ErgomsMessage -Key 'heading_install_only' -Color Cyan -Param @{ name = 'Nginx' }
    Write-ColorOutput "" White

    $config = Install-NginxConfig -Root $Root -ServerName $ServerName -ListenHost $ListenHost `
        -ListenPort $ListenPort -EnvVars $envVars

    if ($AsService) {
        Install-NginxService -Root $Root
    }
    else {
        Start-NginxProcess -Root $Root
    }

    $nginxDir = Get-NginxDir -Root $Root
    $useHttps = Test-NginxUseHttps -EnvVars $envVars -ListenPort $ListenPort
    Write-ColorOutput "" White
    Write-ErgomsMessage -Key 'ok_installed_and_running' -Color Green -Param @{ name = 'Nginx' }
        if ($useHttps) {
        Write-ErgomsMessage -Key 'label_listening_https' -Color Cyan -Param @{ host = $ServerName }
    } else {
        Write-ErgomsMessage -Key 'label_listening_http_bind' -Color Cyan -Param @{ host = $ServerName; port = $ListenPort; bind = $ListenHost }
    }
    Write-ErgomsMessage -Key 'label_path' -Color Cyan -Param @{ path = $nginxDir }
    Write-ErgomsMessage -Key 'label_config' -Color Cyan -Param @{ path = $config.SiteConf }
    Write-ErgomsMessage -Key 'label_logs' -Color Cyan -Param @{ path = (Get-NginxCentralLogsDir -Root $Root) }
}

function Install-NginxService {
    param([string]$Root)
    Sync-NginxServiceName -Root $Root

    if (-not (Test-NginxInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Nginx'; cmd = 'ergoms install-nginx' }
        return
    }

    $nginxDir = Get-NginxDir -Root $Root
    $nssmExe = Install-NSSM -Root $Root
    $nginxExe = Get-NginxExe -Root $Root
    $mainConf = Join-Path $nginxDir "conf\nginx.conf"

    $existingService = Get-Service -Name $script:NginxServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-ErgomsMessage -Key 'service_exists_reinstall' -Color Yellow -Param @{ name = $script:NginxServiceName }
        if ($existingService.Status -eq 'Running') {
            & $nssmExe stop $script:NginxServiceName 2>$null
            Start-Sleep -Seconds 2
        }
        & $nssmExe remove $script:NginxServiceName confirm 2>$null
        Start-Sleep -Seconds 1
    }

    Write-ErgomsMessage -Key 'arrow_install_as_windows_service' -Color Cyan -Param @{ name = 'nginx' }
    & $nssmExe install $script:NginxServiceName $nginxExe
    & $nssmExe set $script:NginxServiceName AppParameters "-c `"$($mainConf -replace '\\', '/')`""
    & $nssmExe set $script:NginxServiceName AppDirectory $nginxDir
    & $nssmExe set $script:NginxServiceName DisplayName "Ergo MS - Nginx"
    & $nssmExe set $script:NginxServiceName Description "Ergo MS Nginx reverse proxy"

    $logsDir = Join-Path $nginxDir "logs"
    & $nssmExe set $script:NginxServiceName AppStdout (Join-Path $logsDir "service_stdout.log")
    & $nssmExe set $script:NginxServiceName AppStderr (Join-Path $logsDir "service_stderr.log")
    & $nssmExe set $script:NginxServiceName Start SERVICE_AUTO_START
    & $nssmExe set $script:NginxServiceName AppExit Default Restart
    & $nssmExe set $script:NginxServiceName AppRestartDelay 5000

    Start-Service -Name $script:NginxServiceName
    Write-ErgomsMessage -Key 'ok_windows_service_installed_running' -Color Green -Param @{ name = 'nginx' }
}

function Test-ProcessIsNginx {
    param($Process)
    if (-not $Process) {
        return $false
    }
    return $Process.ProcessName -eq 'nginx'
}

function Remove-NginxStalePidFile {
    param([string]$Root)

    if (-not $Root -or -not (Test-NginxInstalled -Root $Root)) {
        return
    }

    $pidFile = Join-Path (Get-NginxDir -Root $Root) 'logs\nginx.pid'
    if (-not (Test-Path $pidFile)) {
        return
    }

    $pidText = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($pidText -match '^\d+$') {
        $proc = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        # PID из файла мог достаться другому процессу после прошлого nginx
        if (-not (Test-ProcessIsNginx $proc)) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
        return
    }

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

function Wait-NginxProcessStopped {
    param(
        [string]$Root = '',
        [int]$TimeoutSec = 10
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Remove-NginxStalePidFile -Root $Root
        if (-not (Test-NginxProcessRunning -Root $Root)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }

    Remove-NginxStalePidFile -Root $Root
    return -not (Test-NginxProcessRunning -Root $Root)
}

function Stop-ErgoNginxProcessesForce {
    $procs = Get-Process -Name 'nginx' -ErrorAction SilentlyContinue
    if ($procs) {
        foreach ($proc in $procs) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not (Get-Process -Name 'nginx' -ErrorAction SilentlyContinue)) {
        return
    }

    $taskkill = Get-Command taskkill.exe -ErrorAction SilentlyContinue
    if (-not $taskkill) {
        return
    }

    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # 128 — процесса уже нет; не печатаем «nginx.exe not found» в setup-full
        & $taskkill.Source /F /IM nginx.exe 2>$null | Out-Null
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
}

function Test-NginxProcessRunning {
    param([string]$Root = '')

    Remove-NginxStalePidFile -Root $Root

    $service = Get-Service -Name $script:NginxServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        return $true
    }
    if ($null -ne (Get-Process -Name 'nginx' -ErrorAction SilentlyContinue)) {
        return $true
    }
    if ($Root -and (Test-NginxInstalled -Root $Root)) {
        $pidFile = Join-Path (Get-NginxDir -Root $Root) 'logs\nginx.pid'
        if (Test-Path $pidFile) {
            $pidText = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($pidText -match '^\d+$') {
                $proc = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
                if (Test-ProcessIsNginx $proc) {
                    return $true
                }
            }
        }
    }
    return $false
}

function Stop-NginxProcess {
    param(
        [string]$Root = '',
        [switch]$Quiet
    )

    Remove-NginxStalePidFile -Root $Root

    if (-not (Test-NginxProcessRunning -Root $Root)) {
        if (-not $Quiet) {
            Write-ErgomsMessage -Key 'skip_was_not_running' -Color Gray -Param @{ name = 'Nginx' }
        }
        return
    }

    $service = Get-Service -Name $script:NginxServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        Write-ErgomsMessage -Key 'arrow_stopping_service' -Color Cyan -Param @{ name = 'nginx' }
        Stop-Service -Name $script:NginxServiceName -Force
        if (-not (Wait-NginxProcessStopped -Root $Root -TimeoutSec 15)) {
            Stop-ErgoNginxProcessesForce
            if (-not (Wait-NginxProcessStopped -Root $Root -TimeoutSec 5)) {
                Write-ErgomsMessage -Key 'error_stop_service_failed' -Color Red -Stderr -Param @{ name = 'nginx' }
                exit 1
            }
        }
        Write-ErgomsMessage -Key 'ok_service_stopped' -Color Green -Param @{ name = 'nginx' }
        return
    }

    if ($Root) {
        $nginxDir = Get-NginxDir -Root $Root
        $nginxExe = Get-NginxExe -Root $Root
        if (Test-Path $nginxExe) {
            $mainConf = (Join-Path $nginxDir 'conf\nginx.conf') -replace '\\', '/'
            Write-ErgomsMessage -Key 'arrow_stopping_process' -Color Cyan -Param @{ name = 'nginx' }
            Invoke-NginxCli -NginxExe $nginxExe -Arguments @('-s', 'quit', '-c', $mainConf) -WorkingDirectory $nginxDir | Out-Null
            if (Wait-NginxProcessStopped -Root $Root -TimeoutSec 8) {
                if (-not $Quiet) {
                    Write-ErgomsMessage -Key 'ok_stopped' -Color Green -Param @{ name = 'Nginx' }
                }
                return
            }
        }
    }

    Stop-ErgoNginxProcessesForce
    Remove-NginxStalePidFile -Root $Root

    if (-not (Wait-NginxProcessStopped -Root $Root -TimeoutSec 5)) {
        Write-ErgomsMessage -Key 'error_stop_failed' -Color Red -Stderr -Param @{ name = 'nginx' }
        exit 1
    }
    if (-not $Quiet) {
        Write-ErgomsMessage -Key 'ok_stopped' -Color Green -Param @{ name = 'Nginx' }
    }
}

function Start-NginxProcess {
    param([string]$Root)

    if (-not (Test-NginxInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Nginx'; cmd = 'ergoms install-nginx' }
        return
    }

    Stop-NginxProcess -Root $Root -Quiet

    $nginxDir = Get-NginxDir -Root $Root
    $nginxExe = Get-NginxExe -Root $Root
    $mainConf = (Join-Path $nginxDir "conf\nginx.conf") -replace '\\', '/'
    $pidFile = Join-Path $nginxDir "logs\nginx.pid"
    if (Test-Path $pidFile) {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }

    Write-ErgomsMessage -Key 'arrow_starting' -Color Cyan -Param @{ name = 'nginx' }
    Push-Location $nginxDir
    try {
        Start-Process -FilePath $nginxExe -ArgumentList "-c", $mainConf -WindowStyle Hidden -WorkingDirectory $nginxDir
    }
    finally {
        Pop-Location
    }

    Start-Sleep -Seconds 2

    $procs = @(Get-Process -Name "nginx" -ErrorAction SilentlyContinue)
    if ($procs.Count -gt 0) {
        Write-ErgomsMessage -Key 'ok_nginx_started_detail' -Color Green -Param @{ count = $procs.Count; pid = $procs[0].Id }
    }
    else {
        Write-ErgomsMessage -Key 'error_start_failed_check_logs' -Color Red -Stderr -Param @{ name = 'Nginx'; path = (Get-NginxErrorLogPath -Root $Root) }
    }
}

function Restart-NginxProcess {
    param([string]$Root)

    if (-not (Test-NginxInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Nginx'; cmd = 'ergoms install-nginx' }
        return
    }

    $service = Get-Service -Name $script:NginxServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Write-ErgomsMessage -Key 'arrow_restarting_service' -Color Cyan -Param @{ name = 'nginx' }
        Restart-Service -Name $script:NginxServiceName -Force
        Write-ErgomsMessage -Key 'ok_service_restarted' -Color Green -Param @{ name = 'nginx' }
        return
    }

    Stop-NginxProcess -Root $Root
    Start-NginxProcess -Root $Root
}

function Invoke-NginxReload {
    param([string]$Root)

    if (-not (Test-NginxInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_not_installed_run' -Color Red -Stderr -Param @{ name = 'Nginx'; cmd = 'ergoms install-nginx' }
        return
    }

    $nginxDir = Get-NginxDir -Root $Root
    $nginxExe = Get-NginxExe -Root $Root
    $mainConf = Join-Path $nginxDir 'conf\nginx.conf'
    $siteConf = Join-Path $nginxDir "conf\$($script:NginxConfName).conf"
    if (Test-Path $siteConf) {
        Write-NginxMainConfig -Root $Root -MainConfPath $mainConf -IncludeConfPath $siteConf -NginxDir $nginxDir
    }

    $mainConfForward = $mainConf -replace '\\', '/'

    Write-ErgomsMessage -Key 'arrow_checking_config' -Color Cyan
    $testResult = Invoke-NginxCli -NginxExe $nginxExe -Arguments @('-t', '-c', $mainConfForward) -WorkingDirectory $nginxDir
    if ($testResult.ExitCode -ne 0) {
        Write-ErgomsMessage -Key 'error_config_check_failed' -Color Red -Stderr
        Write-ColorOutput ($testResult.Output -join "`n") Red
        return
    }

    Write-ErgomsMessage -Key 'arrow_reloading' -Color Cyan -Param @{ name = 'nginx' }
    Invoke-NginxCli -NginxExe $nginxExe -Arguments @('-s', 'reload', '-c', $mainConfForward) -WorkingDirectory $nginxDir | Out-Null

    Write-ErgomsMessage -Key 'ok_reloaded' -Color Green -Param @{ name = 'Nginx' }
}

function Show-NginxStatus {
    param([string]$Root)

    $nginxDir = Get-NginxDir -Root $Root
    $installed = Test-NginxInstalled -Root $Root

    if (-not $installed) {
        Write-ErgomsMessage -Key 'component_not_installed' -Color DarkGray -Param @{ name = 'Nginx' }
        Write-ErgomsMessage -Key 'label_expected_path' -Color DarkGray -Param @{ path = $nginxDir }
        return
    }

    Write-ColorOutput "" White
    Write-ErgomsMessage -Key 'heading_status' -Color Cyan -Param @{ name = 'Nginx' }

    $service = Get-Service -Name $script:NginxServiceName -ErrorAction SilentlyContinue
    if ($service) {
        $statusColor = switch ($service.Status) {
            'Running' { 'Green' }
            'Stopped' { 'Red' }
            default { 'Yellow' }
        }
        Write-ErgomsMessage -Key 'label_service_status' -Color $statusColor -Param @{ name = $script:NginxServiceName; status = $service.Status }
    }
    else {
        $procs = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
        if ($procs) {
            Write-ErgomsMessage -Key 'status_running_pid_process' -Color Green -Param @{ pid = $procs[0].Id }
        }
        else {
            Write-ErgomsMessage -Key 'status_process_not_running' -Color Red
        }
    }

    if ($installed) {
        Write-ErgomsMessage -Key 'label_path_indent2' -Color Cyan -Param @{ path = $nginxDir }
        $confPath = Join-Path $nginxDir "conf\${script:NginxConfName}.conf"
        if (Test-Path $confPath) {
            Write-ErgomsMessage -Key 'config_installed_at' -Color Cyan -Param @{ path = $confPath }
        }
        Write-ErgomsMessage -Key 'label_logs_indent2' -Color Cyan -Param @{ path = (Get-NginxCentralLogsDir -Root $Root) }
    }

    Write-ColorOutput "" White
}

function Test-NginxConfig {
    param([string]$Root)

    if (-not (Test-NginxInstalled -Root $Root)) {
        Write-ErgomsMessage -Key 'error_component_not_installed' -Color Red -Stderr -Param @{ name = 'Nginx' }
        return
    }

    $nginxDir = Get-NginxDir -Root $Root
    $nginxExe = Get-NginxExe -Root $Root
    $mainConf = (Join-Path $nginxDir "conf\nginx.conf") -replace '\\', '/'

    $testResult = Invoke-NginxCli -NginxExe $nginxExe -Arguments @('-t', '-c', $mainConf) -WorkingDirectory $nginxDir
    if ($testResult.ExitCode -ne 0) {
        Write-ColorOutput ($testResult.Output -join "`n") Red
    }
}

function Uninstall-Nginx {
    param(
        [string]$Root,
        [switch]$PurgeData
    )
    Sync-NginxServiceName -Root $Root

    Write-ColorOutput "" White
    Write-ErgomsMessage -Key 'heading_remove' -Color Cyan -Param @{ name = 'Nginx' }
    Write-ColorOutput "" White

    $service = Get-Service -Name $script:NginxServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Write-ErgomsMessage -Key 'arrow_remove_nginx_service' -Color Yellow
        if ($service.Status -eq 'Running') {
            Stop-Service -Name $script:NginxServiceName -Force
            Start-Sleep -Seconds 2
        }

        $nssmDir = Get-NssmDir -Root $Root
        $nssmExe = Join-Path $nssmDir "nssm.exe"
        if (Test-Path $nssmExe) {
            & $nssmExe remove $script:NginxServiceName confirm 2>&1 | Out-Null
        }
        else {
            sc.exe delete $script:NginxServiceName 2>$null
        }
        Write-ErgomsMessage -Key 'ok_service_removed_generic' -Color Green
    }
    else {
        Stop-NginxProcess -Root $Root -Quiet
    }

    $nginxDir = Get-NginxDir -Root $Root
    if ($PurgeData -and (Test-Path $nginxDir)) {
        Remove-Item $nginxDir -Recurse -Force
        Write-ErgomsMessage -Key 'ok_removed_path' -Color Green -Param @{ path = $nginxDir }
    }
    elseif (Test-Path $nginxDir) {
        $confPath = Join-Path $nginxDir "conf\${script:NginxConfName}.conf"
        if (Test-Path $confPath) {
            Remove-Item $confPath -Force
            Write-ErgomsMessage -Key 'ok_config_removed' -Color Green -Param @{ path = $confPath }
        }
    }

    Write-ErgomsMessage -Key 'ok_removed' -Color Green -Param @{ name = 'Nginx' }
}
