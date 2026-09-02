# Внутренний dispatch для lifecycle (services, nginx, redis, postgres, tls, cli).
param(
    [Parameter(Mandatory = $true)][string]$Category,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Extra
)

$ErrorActionPreference = 'Stop'
$LibPath = Join-Path (Split-Path -Parent $PSScriptRoot) '..' | Join-Path -ChildPath 'windows' | Join-Path -ChildPath 'lib'
$LibPath = (Resolve-Path (Join-Path $PSScriptRoot '..\..\windows\lib')).Path

. (Join-Path $LibPath 'core.ps1')
Initialize-ErgomsConsoleEncoding

function Invoke-InfraBackend {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][string]$Operation
    )
    . (Join-Path $LibPath 'lifecycle.ps1')
    $backend = Join-Path $Root "core\deployment\lifecycle\services\backends\${Service}_backend.py"
    if (-not (Test-Path -LiteralPath $backend)) {
        Write-ColorOutput "[ERROR] Backend не найден: $backend" Red
        exit 1
    }
    $argv = @($backend, $Operation, '--root', $Root)
    $pyExe = Get-LifecyclePythonExe -Root $Root
    if ($pyExe) {
        & $pyExe @argv
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 @argv
    }
    else {
        & python @argv
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

switch ($Category) {
    'service' {
        . (Join-Path $LibPath 'nssm.ps1')
        . (Join-Path $LibPath 'services.ps1')
        switch ($Operation) {
            'install-all' { Install-AllServices -Root $Root; Start-AllServices -ProjectRoot $Root }
            'install-api' { Install-SingleService -ServiceName 'ergo_ms_api_dev' -Root $Root; Start-Service -Name 'ergo_ms_api_dev' }
            'install-client' { Install-SingleService -ServiceName 'ergo_ms_client_dev' -Root $Root; Start-Service -Name 'ergo_ms_client_dev' }
            'install-media' { Install-SingleService -ServiceName 'ergo_ms_media_api' -Root $Root; Start-Service -Name 'ergo_ms_media_api' }
            'install-beat' { Install-SingleService -ServiceName 'ergo_ms_celery_beat' -Root $Root; Start-Service -Name 'ergo_ms_celery_beat' }
            'install-workers' { Install-WorkerServices -Root $Root; Start-WorkerServices -ProjectRoot $Root }
            'start-all' { Start-AllServices -ProjectRoot $Root }
            'start-api' { Start-Service -Name 'ergo_ms_api_dev' -ErrorAction SilentlyContinue; net start ergo_ms_api_dev 2>$null }
            'start-client' { Start-Service -Name 'ergo_ms_client_dev' -ErrorAction SilentlyContinue; net start ergo_ms_client_dev 2>$null }
            'start-media' { Start-Service -Name 'ergo_ms_media_api' -ErrorAction SilentlyContinue; net start ergo_ms_media_api 2>$null }
            'start-beat' { Start-Service -Name 'ergo_ms_celery_beat' -ErrorAction SilentlyContinue; net start ergo_ms_celery_beat 2>$null }
            'start-workers' { Start-WorkerServices -ProjectRoot $Root }
            'stop-all' { Stop-AllServices -ProjectRoot $Root }
            'stop-api' { net stop ergo_ms_api_dev 2>$null }
            'stop-client' { net stop ergo_ms_client_dev 2>$null }
            'stop-media' { net stop ergo_ms_media_api 2>$null }
            'stop-beat' { net stop ergo_ms_celery_beat 2>$null }
            'stop-workers' { Get-Service -Name 'ergo_ms_celery_worker*' -ErrorAction SilentlyContinue | ForEach-Object { net stop $_.Name 2>$null } }
            'restart-all' { Restart-AllServices -ProjectRoot $Root }
            'restart-api' { net stop ergo_ms_api_dev 2>$null; net start ergo_ms_api_dev 2>$null }
            'restart-client' { net stop ergo_ms_client_dev 2>$null; net start ergo_ms_client_dev 2>$null }
            'restart-media' { net stop ergo_ms_media_api 2>$null; net start ergo_ms_media_api 2>$null }
            'restart-beat' { net stop ergo_ms_celery_beat 2>$null; net start ergo_ms_celery_beat 2>$null }
            'restart-workers' { Stop-WorkerServices -ProjectRoot $Root; Start-WorkerServices -ProjectRoot $Root }
            'status-all' { Show-ServicesStatus -ProjectRoot $Root }
            'status-api' { sc query ergo_ms_api_dev }
            'status-client' { sc query ergo_ms_client_dev }
            'status-media' { sc query ergo_ms_media_api }
            'status-beat' { sc query ergo_ms_celery_beat }
            'status-workers' { Get-Service -Name 'ergo_ms_celery_worker*' -ErrorAction SilentlyContinue | ForEach-Object { sc query $_.Name } }
            'uninstall-all' {
                $purge = $Extra -contains '--purge'
                Uninstall-AllServices -PurgeData:$purge -ProjectRoot $Root
            }
            default { throw "Неизвестная операция service: $Operation" }
        }
    }
    'nginx' {
        . (Join-Path $LibPath 'nssm.ps1')
        . (Join-Path $LibPath 'nginx_env.ps1')
        . (Join-Path $LibPath 'nginx.ps1')
        $serverName = if ($Extra.Count -ge 1) { $Extra[0] } else { '' }
        $listenPort = if ($Extra.Count -ge 2) { $Extra[1] } else { '' }
        switch ($Operation) {
            'install' { Install-Nginx -Root $Root -ServerName $serverName -ListenPort $listenPort }
            'install-service' { Install-Nginx -Root $Root -ServerName $serverName -ListenPort $listenPort -AsService }
            'uninstall' {
                $purge = $Extra -contains '--purge'
                Uninstall-Nginx -Root $Root -PurgeData:$purge
            }
            'start' { Start-NginxProcess -Root $Root }
            'stop' { Stop-NginxProcess -Root $Root }
            'restart' { Restart-NginxProcess -Root $Root }
            'reload' { Invoke-InfraBackend -Root $Root -Service 'nginx' -Operation 'reload' }
            'status' { Invoke-InfraBackend -Root $Root -Service 'nginx' -Operation 'status' }
            'test' { Invoke-InfraBackend -Root $Root -Service 'nginx' -Operation 'test' }
            default { throw "Неизвестная операция nginx: $Operation" }
        }
    }
    'redis' {
        . (Join-Path $LibPath 'nssm.ps1')
        . (Join-Path $LibPath 'redis.ps1')
        $port = if ($Extra.Count -ge 1 -and $Extra[0] -match '^\d+$') { $Extra[0] } else { '' }
        switch ($Operation) {
            'install' { Install-Redis -Root $Root -ListenPort $port }
            'install-service' { Install-Redis -Root $Root -ListenPort $port -AsService }
            'uninstall' {
                $purge = $Extra -contains '--purge'
                Uninstall-Redis -Root $Root -PurgeData:$purge
            }
            'start' { Start-RedisProcess -Root $Root }
            'stop' { Stop-RedisProcess -Root $Root }
            'restart' { Restart-RedisProcess -Root $Root }
            'status' { Invoke-InfraBackend -Root $Root -Service 'redis' -Operation 'status' }
            'test' { Invoke-InfraBackend -Root $Root -Service 'redis' -Operation 'test' }
            default { throw "Неизвестная операция redis: $Operation" }
        }
    }

    'postgres' {
        . (Join-Path $LibPath 'nssm.ps1')
        . (Join-Path $LibPath 'postgres.ps1')
        $port = ''
        $noSkip = $false
        foreach ($arg in $Extra) {
            if ($arg -eq '--no-skip-system') { $noSkip = $true }
            elseif ($arg -match '^\d+$') { $port = $arg }
        }
        switch ($Operation) {
            'install' {
                if ($noSkip) {
                    Install-Postgres -Root $Root -ListenPort $port -NoSkipSystem
                }
                else {
                    Install-Postgres -Root $Root -ListenPort $port
                }
            }
            'install-service' { Install-PostgresService -Root $Root }
            'uninstall' {
                $purge = $Extra -contains '--purge'
                Uninstall-Postgres -Root $Root -PurgeData:$purge
            }
            'start' { Start-PostgresProcess -Root $Root }
            'stop' { Stop-PostgresProcess -Root $Root }
            'restart' { Restart-PostgresProcess -Root $Root }
            'status' { Show-PostgresStatus -Root $Root }
            'test' {
                if (-not (Test-PostgresPing -Root $Root)) {
                    Write-ColorOutput '[ERROR] PostgreSQL ping не удался' Red
                    exit 1
                }
                Write-ColorOutput '[OK] PONG' Green
            }
            'migrate-to-portable' {
                $migrateArgs = @()
                if ($Extra) { $migrateArgs = @($Extra) }
                Migrate-PostgresToPortable -Root $Root -ExtraArgs $migrateArgs
            }
            default { throw "Неизвестная операция postgres: $Operation" }
        }
    }
    'meilisearch' {
        . (Join-Path $LibPath 'nssm.ps1')
        . (Join-Path $LibPath 'meilisearch.ps1')
        switch ($Operation) {
            'install' { Install-Meilisearch -Root $Root }
            'install-service' { Install-Meilisearch -Root $Root -AsService }
            'uninstall' {
                $purge = $Extra -contains '--purge'
                Uninstall-Meilisearch -Root $Root -PurgeData:$purge
            }
            'start' { Start-MeilisearchProcess -Root $Root }
            'stop' { Stop-MeilisearchProcess -Root $Root }
            'restart' { Restart-MeilisearchProcess -Root $Root }
            'status' { Invoke-InfraBackend -Root $Root -Service 'meilisearch' -Operation 'status' }
            'test' { Invoke-InfraBackend -Root $Root -Service 'meilisearch' -Operation 'test' }
            default { throw "Неизвестная операция meilisearch: $Operation" }
        }
    }
    'tls' {
        if ($Operation -eq 'status') {
            Write-ColorOutput '[WARNING] TLS на Windows не поддерживается; используйте Linux или nginx вручную' Yellow
            exit 0
        }
        Write-ColorOutput '[ERROR] TLS на Windows не поддерживается' Red
        exit 1
    }
    'cli' {
        . (Join-Path $LibPath 'cli.ps1')
        switch ($Operation) {
            'install' { Install-CliWrapper -ProjectRoot $Root }
            'uninstall' { Uninstall-CliWrapper -ProjectRoot $Root }
            default { throw "Неизвестная операция cli: $Operation" }
        }
    }
    'runtime' {
        . (Join-Path $LibPath 'portable_python.ps1')
        . (Join-Path $LibPath 'portable_nodejs.ps1')
        $force = $Extra -contains '--force'
        switch ($Operation) {
            'install-python' { Install-PortablePython -Root $Root -Force:$force | Out-Null }
            'install-nodejs' { Install-PortableNodejs -Root $Root -Force:$force | Out-Null }
            default { throw "Неизвестная операция runtime: $Operation" }
        }
    }
    default { throw "Неизвестная категория: $Category" }
}
