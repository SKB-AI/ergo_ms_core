#Requires -RunAsAdministrator

<#

.SYNOPSIS

    Manages ergo_ms services on Windows using NSSM (Non-Sucking Service Manager)



.DESCRIPTION

    This script installs, starts, stops, and manages Windows services for ergo_ms.

    It uses NSSM to create Windows services from batch/powershell scripts.



.PARAMETER Command

    Command to execute: install, start, stop, restart, status, uninstall-services, install-cli, uninstall-cli



.PARAMETER Root

    Absolute path to project root (auto-detected if not provided)



.PARAMETER Purge

    Remove all configuration when uninstalling



.PARAMETER NoCli

    Skip CLI wrapper installation



.EXAMPLE

    .\ergo_ms.ps1 install

    .\ergo_ms.ps1 start

    .\ergo_ms.ps1 stop

    .\ergo_ms.ps1 status

    ergoms restart

#>



param(

    [Parameter(Position=0)]

    [string]$Command = 'help',



    [Parameter(ValueFromRemainingArguments=$true)]

    [string[]]$RemainingArgs = @(),



    [string]$Root = '',



    [switch]$Purge,

    [switch]$NoCli,

    [switch]$RecreateVenv,

    [switch]$WithPostgres

)



$ErrorActionPreference = "Stop"



# Lazy loading: загружаем только core и commands для быстрого выполнения обычных команд.

# Тяжёлые модули (nssm, services, setup, cli, help) загружаются только при необходимости.

$LibPath = Join-Path $PSScriptRoot "lib"

. (Join-Path $LibPath "core.ps1")

. (Join-Path $LibPath "nginx_env.ps1")

. (Join-Path $LibPath "commands.ps1")

. (Join-Path $LibPath "lifecycle.ps1")

. (Join-Path $LibPath "cli_log.ps1")



Initialize-ErgomsConsoleEncoding



$script:HeavyModulesLoaded = $false

function Load-HeavyModules {

    if ($script:HeavyModulesLoaded) { return }

    . (Join-Path $LibPath "nssm.ps1")

    . (Join-Path $LibPath "services.ps1")

    . (Join-Path $LibPath "setup.ps1")

    . (Join-Path $LibPath "cli.ps1")

    . (Join-Path $LibPath "help.ps1")

    . (Join-Path $LibPath "nginx.ps1")

    . (Join-Path $LibPath "redis.ps1")

    . (Join-Path $LibPath "meilisearch.ps1")

    . (Join-Path $LibPath "postgres.ps1")

    $script:HeavyModulesLoaded = $true

}



# Main execution

function Main {

    # Proxy commands that don't require admin

    $proxyCommands = @('poetry', 'api', 'media_api', 'npm')

    $isProxyCommand = $proxyCommands -contains $Command.ToLower()

    

    # Commands that require admin

    $adminCommands = @(

        'install', 'install-services', 'install-api-service', 'install-client-service', 

        'install-worker-service', 'install-beat-service', 'install-media-service', 

        'start', 'stop', 'restart', 'status', 

        'uninstall-services', 'install-cli', 'uninstall-cli', 'setup-full',

        'install-nginx', 'install-nginx-service', 'uninstall-nginx',

        'start-nginx', 'stop-nginx', 'restart-nginx', 'reload-nginx',

        'install-redis', 'install-redis-service', 'uninstall-redis',

        'start-redis', 'stop-redis', 'restart-redis',

        'install-postgres', 'install-postgres-service', 'uninstall-postgres',

        'start-postgres', 'stop-postgres', 'restart-postgres'

    )

    $requiresAdmin = $adminCommands -contains $Command.ToLower()

    

    # Commands that don't require admin

    $noAdminCommands = @('logs', 'help', 'clean', 'update-submodules', 'update-module-submodules', 'status-nginx', 'test-nginx', 'status-redis', 'test-redis', 'status-postgres', 'test-postgres', 'migrate-postgres-to-portable', 'install-python', 'install-python-runtime', 'install-nodejs', 'install-node')

    

    # Check if it's a custom command

    $projectRoot = $null

    $customCommands = @{}

    $isCustomCommand = $false

    

    if (-not $requiresAdmin -and -not $isProxyCommand -and $Command -ne 'help') {

        try {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $customCommands = Get-CustomCommands -ProjectRoot $projectRoot

            $isCustomCommand = $customCommands.ContainsKey($Command)

        }

        catch {

            # Ignore errors

        }

    }

    if (-not $projectRoot) {
        try {
            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root
        }
        catch {
        }
    }
    if ($projectRoot) {
        Attach-CliSessionLog -Root $projectRoot -Command $Command
    }

    

    # Check admin only for admin commands

    if ($requiresAdmin -and -not (Test-Administrator)) {

        Write-ErgomsMessage -Key 'admin_required' -Color Red -Stderr -Param @{ name = $Command }

        Write-ErgomsMessage -Key 'admin_powershell_hint' -Color Yellow -Stderr

        exit 1

    }



    # Handle built-in noAdminCommands before custom commands to avoid recursion

    # (clean/update-submodules are in commands.conf but must run as built-in when invoked via win: ergo_ms.ps1)

    if ($Command -in @('clean', 'update-submodules', 'update-module-submodules')) {

        . Load-HeavyModules

        $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

        if ($Command -eq 'clean') {

            Clear-ProjectDependencies -Root $projectRoot

        } elseif ($Command -eq 'update-submodules') {

            Update-Submodules -Root $projectRoot

        } else {

            Update-ModuleSubmodules -Root $projectRoot

        }

        return

    }

    if ($Command -in @('install-python', 'install-python-runtime', 'install-nodejs', 'install-node')) {

        $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

        Invoke-LifecycleRunner -Root $projectRoot -Recipe $Command.ToLower()

        return

    }



    # Handle nginx/redis noAdmin commands before custom commands

    if ($Command -in @('status-nginx', 'test-nginx', 'status-redis', 'test-redis', 'status-postgres', 'test-postgres')) {

        . Load-HeavyModules

        $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

        switch ($Command) {

            'status-nginx' { Show-NginxStatus -Root $projectRoot }

            'test-nginx'   { Test-NginxConfig -Root $projectRoot }

            'status-redis' { Show-RedisStatus -Root $projectRoot }

            'test-redis'   {

                if (Test-RedisPing -Root $projectRoot) {

                    Write-ColorOutput '[OK] PONG' Green

                }

                else {

                    Write-ErgomsMessage -Key 'redis_ping_failed_short' -Color Red -Stderr

                    exit 1

                }

            }

            'status-postgres' { Show-PostgresStatus -Root $projectRoot }

            'test-postgres' {

                if (Test-PostgresPing -Root $projectRoot) {

                    Write-ColorOutput '[OK] PONG' Green

                }

                else {

                    Write-ErgomsMessage -Key 'pg_ping_failed_short' -Color Red -Stderr

                    exit 1

                }

            }

        }

        return

    }



    # Handle custom commands (no admin required)

    if ($isCustomCommand) {

        Invoke-CustomCommand -CommandName $Command -CommandArgs $RemainingArgs -ProjectRoot $projectRoot

        return

    }



    # Handle proxy commands

    if ($isProxyCommand) {

        $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

        switch ($Command.ToLower()) {

            'poetry' {

                Invoke-PoetryCommand -CommandArgs $RemainingArgs -Root $projectRoot

                return

            }

            'api' {

                Invoke-ApiCommand -CommandArgs $RemainingArgs -Root $projectRoot

                return

            }

            'media_api' {

                Invoke-MediaApiCommand -CommandArgs $RemainingArgs -Root $projectRoot

                return

            }

            'npm' {

                Invoke-NpmCommand -CommandArgs $RemainingArgs -Root $projectRoot

                return

            }

        }

    }



    # Handle <module>:poetry commands (e.g., ergoms <module>:poetry add requests ">=2.28.0")

    if ($Command -match '^([a-zA-Z0-9_-]+):poetry$') {

        $moduleName = $Matches[1]

        $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

        Invoke-ModulePoetryCommand -ModuleName $moduleName -CommandArgs $RemainingArgs -Root $projectRoot

        return

    }



    # Service/admin/utility commands — загружаем тяжёлые модули

    # Dot-source для сохранения определений функций в текущей области видимости

    . Load-HeavyModules



    switch ($Command.ToLower()) {

        'install' {
            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root
            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-services'
        }

        'install-services' {
            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root
            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-services'
        }

        'install-api-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-api-service'

        }

        'install-client-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-client-service'

        }

        'install-worker-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-worker-service'

        }

        'install-beat-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-beat-service'

        }

        'install-media-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-media-service'

        }

        'deploy-api' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Write-ErgomsMessage -Key 'deploy_api_only' -Color Cyan -Param @{ root = $projectRoot }

            Invoke-CustomCommand -CommandName "deploy-api" -ProjectRoot $projectRoot -CommandArgs $RemainingArgs

            Write-Host ""; Write-ErgomsMessage -Key 'deploy_api_done' -Color Green

        }

        'deploy-client' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Write-ErgomsMessage -Key 'deploy_client_only' -Color Cyan -Param @{ root = $projectRoot }

            Invoke-CustomCommand -CommandName "deploy-client" -ProjectRoot $projectRoot -CommandArgs $RemainingArgs

            Write-Host ""; Write-ErgomsMessage -Key 'deploy_client_done' -Color Green

        }

        'deploy-api-dev' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Write-ErgomsMessage -Key 'deploy_api_dev' -Color Cyan -Param @{ root = $projectRoot }

            Invoke-CustomCommand -CommandName "deploy-api-dev" -ProjectRoot $projectRoot -CommandArgs $RemainingArgs

        }

        'deploy-client-dev' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Write-ErgomsMessage -Key 'deploy_client_dev' -Color Cyan -Param @{ root = $projectRoot }

            Invoke-CustomCommand -CommandName "deploy-client-dev" -ProjectRoot $projectRoot -CommandArgs $RemainingArgs

        }

        'deploy-all' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Write-ErgomsMessage -Key 'deploy_all' -Color Cyan -Param @{ root = $projectRoot }

            Invoke-CustomCommand -CommandName "deploy-all" -ProjectRoot $projectRoot -CommandArgs $RemainingArgs

            Write-Host ""; Write-ErgomsMessage -Key 'deploy_all_done' -Color Green

        }

        'start' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'start'

        }

        'stop' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'stop'

        }

        'restart' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'restart'

        }

        'status' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'status'

        }

        'uninstall-services' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $extra = @()

            if ($Purge) { $extra += '--purge' }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'uninstall-services' -ExtraArgs $extra

        }

        'install-cli' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Install-CliWrapper -ProjectRoot $projectRoot

        }

        'uninstall-cli' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Uninstall-CliWrapper -ProjectRoot $projectRoot

        }

        'logs' {

            if ($RemainingArgs.Count -eq 0) {

                Write-ErgomsMessage -Key 'service_name_required' -Color Red -Stderr

                $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

                $serviceNames = Get-ServiceNames -ProjectRoot $projectRoot

                Write-ErgomsMessage -Key 'available_services' -Color Yellow -Stderr -Param @{ items = ($serviceNames -join ', ') }

                Write-ErgomsMessage -Key 'logs_usage' -Color Cyan -Stderr

                exit 1

            }

            

            $serviceName = $RemainingArgs[0]

            $lines = 500

            

            if ($RemainingArgs.Count -gt 1) {

                $lines = [int]$RemainingArgs[1]

            }

            

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            if ($serviceName -in @('setup-full', 'setup', 'ergoms')) {
                Show-ServiceLogs -ServiceName $serviceName -Lines $lines -ProjectRoot $projectRoot
                return
            }

            $serviceNames = Get-ServiceNames -ProjectRoot $projectRoot

            $pythonExe = Join-Path $projectRoot 'virtual_env\python\Scripts\python.exe'
            $normalizeScript = Join-Path $projectRoot 'core\deployment\scripts\service_names.py'
            if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $normalizeScript)) {
                $normalized = & $pythonExe $normalizeScript normalize $serviceName 2>$null
                if (-not [string]::IsNullOrWhiteSpace($normalized)) {
                    $serviceName = $normalized.Trim()
                }
            }
            else {
                $serviceName = switch ($serviceName) {
                    'media_api' { 'ergo_ms_media_api' }
                    default { $serviceName }
                }
            }

            if ($serviceName -eq 'ergo_ms_client_dev' -and (Test-NginxEnabled -ProjectRoot $projectRoot)) {
                Write-ErgomsMessage -Key 'skip_client_dev_nginx' -Color Gray
                exit 0
            }

            $knownLog = $false
            $pathsScript = Join-Path $projectRoot 'core\deployment\scripts\logs_paths.py'
            if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $pathsScript)) {
                $knownRaw = & $pythonExe $pathsScript known $serviceName $projectRoot 2>$null
                if ("$knownRaw".Trim() -eq 'true') {
                    $knownLog = $true
                }
            }

            if (($serviceNames -notcontains $serviceName) -and -not $knownLog) {

                Write-ErgomsMessage -Key 'unknown_service' -Color Red -Stderr -Param @{ name = $serviceName }

                Write-ErgomsMessage -Key 'available_services' -Color Yellow -Stderr -Param @{ items = ($serviceNames -join ', ') }

                exit 1

            }

            

            Show-ServiceLogs -ServiceName $serviceName -Lines $lines -ProjectRoot $projectRoot

        }

        'setup-full' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $extra = @()

            if ($RecreateVenv) { $extra += '--recreate-venv' }

            if ($WithPostgres) { $extra += '--with-postgres' }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'setup-full' -ExtraArgs $extra

        }

        'clean' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Clear-ProjectDependencies -Root $projectRoot

        }

        'update-submodules' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'update-submodules'

        }

        'update-module-submodules' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'update-module-submodules'

        }

        'install-nginx' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $serverName = if ($RemainingArgs.Count -ge 1) { $RemainingArgs[0] } else { '' }

            $listenPort = if ($RemainingArgs.Count -ge 2) { $RemainingArgs[1] } else { '' }

            $extra = @()

            if ($serverName) { $extra += @('--server-name', $serverName) }

            if ($listenPort) { $extra += @('--listen-port', $listenPort) }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-nginx' -ExtraArgs $extra

        }

        'install-nginx-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $serverName = if ($RemainingArgs.Count -ge 1) { $RemainingArgs[0] } else { '' }

            $listenPort = if ($RemainingArgs.Count -ge 2) { $RemainingArgs[1] } else { '' }

            $extra = @()

            if ($serverName) { $extra += @('--server-name', $serverName) }

            if ($listenPort) { $extra += @('--listen-port', $listenPort) }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-nginx-service' -ExtraArgs $extra

        }

        'uninstall-nginx' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $extra = @()

            if ($Purge) { $extra += '--purge' }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'uninstall-nginx' -ExtraArgs $extra

        }

        'start-nginx' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'start-nginx'

        }

        'stop-nginx' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'stop-nginx'

        }

        'restart-nginx' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'restart-nginx'

        }

        'reload-nginx' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'reload-nginx'

        }

        'install-redis' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $portArgs = @($RemainingArgs | Where-Object { $_ -ne '--configure' })

            $listenPort = if ($portArgs.Count -ge 1) { $portArgs[0] } else { '' }

            $extra = @()

            if ($listenPort) { $extra += @('--listen-port', $listenPort) }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-redis' -ExtraArgs $extra

        }

        'install-redis-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $portArgs = @($RemainingArgs | Where-Object { $_ -ne '--configure' })

            $listenPort = if ($portArgs.Count -ge 1) { $portArgs[0] } else { '' }

            $extra = @()

            if ($listenPort) { $extra += @('--listen-port', $listenPort) }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-redis-service' -ExtraArgs $extra

        }

        'uninstall-redis' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $extra = @()

            if ($Purge) { $extra += '--purge' }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'uninstall-redis' -ExtraArgs $extra

        }

        'start-redis' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'start-redis'

        }

        'stop-redis' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'stop-redis'

        }

        'restart-redis' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'restart-redis'

        }

        'install-postgres' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $portArgs = @($RemainingArgs | Where-Object { $_ -notin @('--configure', '--no-skip-system') })

            $listenPort = if ($portArgs.Count -ge 1) { $portArgs[0] } else { '' }

            $extra = @()

            if ($listenPort) { $extra += @('--listen-port', $listenPort) }

            if ($RemainingArgs -contains '--no-skip-system' -or $WithPostgres) {

                $extra += '--with-postgres'

            }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'install-postgres' -ExtraArgs $extra

        }

        'install-postgres-service' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Load-HeavyModules

            Install-PostgresService -Root $projectRoot

        }

        'uninstall-postgres' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $extra = @()

            if ($Purge) { $extra += '--purge' }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'uninstall-postgres' -ExtraArgs $extra

        }

        'start-postgres' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'start-postgres'

        }

        'stop-postgres' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'stop-postgres'

        }

        'restart-postgres' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'restart-postgres'

        }

        'migrate-postgres-to-portable' {

            $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

            $extra = @()

            $hasSourcePassword = $false

            for ($i = 0; $i -lt $RemainingArgs.Count; $i++) {

                $arg = $RemainingArgs[$i]

                if ($arg -eq '--source-port' -and ($i + 1) -lt $RemainingArgs.Count) {

                    $extra += @('--source-port', $RemainingArgs[$i + 1])

                    $i++

                }

                elseif ($arg -eq '--source-host' -and ($i + 1) -lt $RemainingArgs.Count) {

                    $extra += @('--source-host', $RemainingArgs[$i + 1])

                    $i++

                }

                elseif ($arg -eq '--source-user' -and ($i + 1) -lt $RemainingArgs.Count) {

                    $extra += @('--source-user', $RemainingArgs[$i + 1])

                    $i++

                }

                elseif ($arg -eq '--source-password' -and ($i + 1) -lt $RemainingArgs.Count) {

                    $extra += @('--source-password', $RemainingArgs[$i + 1])

                    $hasSourcePassword = $true

                    $i++

                }

                elseif ($arg -in @('--force', '--dry-run')) {

                    $extra += $arg

                }

                else {

                    Write-ErgomsMessage -Key 'error_unknown_arg' -Color Red -Stderr -Param @{ arg = $arg }

                    exit 1

                }

            }

            if (-not $hasSourcePassword) {

                Write-ErgomsMessage -Key 'error_need_source_password' -Color Red -Stderr

                exit 1

            }

            Invoke-LifecycleRunner -Root $projectRoot -Recipe 'migrate-postgres-to-portable' -ExtraArgs $extra

        }

        'help' {

            try {

                $projectRoot = Get-ProjectRoot -ProvidedRoot $Root

                Show-Help -ProjectRoot $projectRoot -HelpArgs $RemainingArgs

            }

            catch {

                Show-Help -HelpArgs $RemainingArgs

            }

        }

        default {

            Write-ErgomsMessage -Key 'unknown_command' -Color Red -Stderr -Param @{ name = $Command }

            Write-ErgomsMessage -Key 'help_hint' -Color Yellow -Stderr

            exit 1

        }

    }

}



Main

