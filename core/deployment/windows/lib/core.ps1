# Core utilities for ErgoMS deployment

# Базовые утилиты для развертывания ErgoMS

. (Join-Path $PSScriptRoot 'portable_env.ps1')
. (Join-Path $PSScriptRoot 'nginx_env.ps1')
. (Join-Path $PSScriptRoot 'redis_env.ps1')
. (Join-Path $PSScriptRoot 'search_env.ps1')

function Get-ErgoServicePrefix {
    param([string]$ProjectRoot)
    if ($env:ERGO_SERVICE_PREFIX -and $env:ERGO_SERVICE_PREFIX.Trim()) {
        return $env:ERGO_SERVICE_PREFIX.Trim()
    }
    if ($ProjectRoot) {
        $fromFile = Get-ErgoEnvValue -Root $ProjectRoot -Name 'ERGO_SERVICE_PREFIX'
        if ($fromFile) { return $fromFile }
    }
    return 'ergo_ms'
}

function Get-ErgoServiceName {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [string]$ProjectRoot
    )
    $prefix = Get-ErgoServicePrefix -ProjectRoot $ProjectRoot
    return "${prefix}_${Role}"
}

function Test-ErgoServiceRole {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $base = $Name -replace '\.service$', ''
    return $base.EndsWith("_$Role")
}

$script:BaseServices = @(
    (Get-ErgoServiceName -Role 'api_dev'),
    (Get-ErgoServiceName -Role 'client_dev'),
    (Get-ErgoServiceName -Role 'media_api'),
    (Get-ErgoServiceName -Role 'celery_beat')
)




# Метки консольного вывода (см. core/deployment/console_tags.py)
$script:ErgoTagOk = '[OK]'
$script:ErgoTagError = '[ERROR]'
$script:ErgoTagWarning = '[WARNING]'
$script:ErgoTagSkip = '[SKIP]'
$script:ErgoTagInfo = '[INFO]'

function Format-ErgoConsole {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('ok', 'error', 'warning', 'skip', 'info')]
        [string]$Level,
        [string]$Message = ''
    )

    $tag = switch ($Level) {
        'ok' { $script:ErgoTagOk }
        'error' { $script:ErgoTagError }
        'warning' { $script:ErgoTagWarning }
        'skip' { $script:ErgoTagSkip }
        'info' { $script:ErgoTagInfo }
    }

    if ([string]::IsNullOrWhiteSpace($Message)) {
        return $tag
    }
    return "$tag $Message"
}

# Кэш для списка служб

$script:CachedServiceNames = $null

$script:CachedProjectRoot = $null



function Write-ColorOutput {

    param([string]$Message, [string]$Color = 'White')

    Write-Host $Message -ForegroundColor $Color

}



function Write-ErgomsMessage {

    param(

        [Parameter(Mandatory = $true)]

        [string]$Key,

        [string]$Color = 'White',

        [switch]$Stderr,

        [hashtable]$Param = @{}

    )



    $root = $script:ErgomsProjectRoot

    if (-not $root) {

        try { $root = Get-ProjectRoot } catch { $root = $null }

    }



    $pythonExe = if ($root) { Join-Path $root 'virtual_env\python\Scripts\python.exe' } else { $null }

    $scriptPath = if ($root) { Join-Path $root 'core\deployment\scripts\ergoms_console.py' } else { $null }



    if ($pythonExe -and (Test-Path $pythonExe) -and $scriptPath -and (Test-Path $scriptPath)) {

        $args = @($scriptPath, '--key', $Key, '--color', $Color)

        if ($Stderr) { $args += '--stderr' }

        foreach ($entry in $Param.GetEnumerator()) {

            $args += @('--param', "$($entry.Key)=$($entry.Value)")

        }

        $prevEa = $ErrorActionPreference

        $ErrorActionPreference = 'Continue'

        try {

            if ($Stderr) {

                & $pythonExe @args 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor $Color }

            }

            else {

                & $pythonExe @args | ForEach-Object { Write-Host $_ -ForegroundColor $Color }

            }

        }

        finally {

            $ErrorActionPreference = $prevEa

        }

        return

    }



    Write-ColorOutput "[$Key]" $Color

}



function Write-ErgomsText {

    param(

        [Parameter(Mandatory = $true)]

        [string]$Text,

        [string]$Color = 'White',

        [switch]$Stderr

    )



    $root = $script:ErgomsProjectRoot

    if (-not $root) {

        try { $root = Get-ProjectRoot } catch { $root = $null }

    }



    $pythonExe = if ($root) { Join-Path $root 'virtual_env\python\Scripts\python.exe' } else { $null }

    $scriptPath = if ($root) { Join-Path $root 'core\deployment\scripts\ergoms_console.py' } else { $null }



    if ($pythonExe -and (Test-Path $pythonExe) -and $scriptPath -and (Test-Path $scriptPath)) {

        $args = @($scriptPath, '--text', $Text, '--color', $Color)

        if ($Stderr) { $args += '--stderr' }

        $prevEa = $ErrorActionPreference

        $ErrorActionPreference = 'Continue'

        try {

            if ($Stderr) {

                & $pythonExe @args 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor $Color }

            }

            else {

                & $pythonExe @args | ForEach-Object { Write-Host $_ -ForegroundColor $Color }

            }

        }

        finally {

            $ErrorActionPreference = $prevEa

        }

        return

    }



    Write-ColorOutput $Text $Color

}



function Initialize-ErgomsConsoleEncoding {

    if ($Host.Name -ne 'ConsoleHost') { return }

    try {

        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

        [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)

        $script:OutputEncoding = [System.Text.UTF8Encoding]::new($false)

    }

    catch {

        # Ignore: non-interactive hosts may not support console encoding changes

    }

}



function Test-Administrator {

    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()

    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

}



function Get-ProjectRoot {

    param([string]$ProvidedRoot)



    if ($ProvidedRoot) {

        if (Test-Path $ProvidedRoot) {

            return (Resolve-Path $ProvidedRoot).Path

        }

        throw "Provided root path does not exist: $ProvidedRoot"

    }



    # Auto-detect from script location

    $scriptDir = Split-Path -Parent $PSCommandPath

    $projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)



    # Try git root

    try {

        Push-Location $scriptDir

        $gitRoot = git rev-parse --show-toplevel 2>$null

        if ($gitRoot) {

            Pop-Location

            return (Resolve-Path $gitRoot).Path

        }

    }

    catch {

        # Ignore git errors

    }

    finally {

        Pop-Location

    }



    return $projectRoot

}



function Get-ProjectLogsDir {

    param([string]$ProjectRoot)

    return Join-Path $ProjectRoot "logs"

}



function Get-ProjectWrappersDir {

    param([string]$ProjectRoot)

    return Join-Path $ProjectRoot "core\deployment\wrappers"

}



function Test-ProjectStructure {

    param([string]$Root)



    $apiPath = Join-Path $Root "core\api"

    $clientPath = Join-Path $Root "core\client"



    if (-not (Test-Path $apiPath)) {

        Write-ErgomsMessage -Key 'invalid_project_root' -Color Red -Stderr -Param @{ path = $apiPath }

        Write-ErgomsMessage -Key 'project_root_setup_hint' -Color Yellow -Stderr

        throw "Invalid project root: $apiPath not found"

    }

    if (-not (Test-Path $clientPath)) {

        Write-ErgomsMessage -Key 'invalid_project_root' -Color Red -Stderr -Param @{ path = $clientPath }

        Write-ErgomsMessage -Key 'project_root_setup_hint' -Color Yellow -Stderr

        throw "Invalid project root: $clientPath not found"

    }



    Write-ErgomsMessage -Key 'project_structure_ok' -Color Green

}



# Парсинг YAML файла для получения имён воркеров

# Простой парсер без внешних зависимостей (PowerShell-yaml не установлен по умолчанию)

function Get-CeleryWorkersFromYaml {

    param([string]$YamlFile)

    

    if (-not (Test-Path $YamlFile)) {

        return @()

    }

    

    $workers = @()

    $inWorkers = $false

    

    $lines = Get-Content $YamlFile -Encoding UTF8

    

    foreach ($line in $lines) {

        # Пропускаем комментарии и пустые строки

        if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) {

            continue

        }

        

        # Проверяем начало секции workers:

        if ($line -match '^workers:\s*$') {

            $inWorkers = $true

            continue

        }

        

        # Если мы в секции workers

        if ($inWorkers) {

            # Проверяем, не началась ли новая секция верхнего уровня

            if ($line -match '^[a-z_]+:\s*$' -and $line -notmatch '^\s') {

                $inWorkers = $false

                continue

            }

            

            # Ищем имена воркеров (строки с отступом в 2 пробела и двоеточием)

            if ($line -match '^\s{2}([a-z_]+):\s*$') {

                $workerName = $Matches[1]

                $workers += $workerName

            }

        }

    }

    

    return $workers

}



# Получение списка воркеров из celery_workers.yaml

function Get-CeleryWorkers {

    param([string]$ProjectRoot)

    

    if (-not $ProjectRoot) {

        try {

            $ProjectRoot = Get-ProjectRoot

        }

        catch {

            return @()

        }

    }

    

    $workersConfig = Join-Path $ProjectRoot "celery_workers.yaml"

    return Get-CeleryWorkersFromYaml -YamlFile $workersConfig

}



# Генерация списка служб на основе конфигурации воркеров

function Test-PostgresPortableEnabled {

    param([string]$ProjectRoot)

    $direct = Join-Path $ProjectRoot 'virtual_env\packages\postgres\bin\postgres.exe'

    $nested = Join-Path $ProjectRoot 'virtual_env\packages\postgres\pgsql\bin\postgres.exe'

    if ((Test-Path $direct) -or (Test-Path $nested)) { return $true }

    $svcName = 'ergo_ms_postgres'
    $fromEnv = Get-ErgoEnvValue -Root $ProjectRoot -Name 'POSTGRES_SERVICE_WINDOWS'
    if ($fromEnv) { $svcName = $fromEnv }

    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue

    return $null -ne $svc

}




function Test-HostProfileWants {
    param(
        [string]$ProjectRoot,
        [string]$ServiceId
    )
    $python = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'
    $script = Join-Path $ProjectRoot 'core\deployment\lifecycle\host_profile.py'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $script)) {
        return $true
    }
    & $python $script --root $ProjectRoot --wants $ServiceId 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-HostProfileCoreUnits {
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'
    $script = Join-Path $ProjectRoot 'core\deployment\lifecycle\host_profile.py'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $script)) {
        return @(
            (Get-ErgoServiceName -Role 'api_dev' -ProjectRoot $ProjectRoot),
            (Get-ErgoServiceName -Role 'client_dev' -ProjectRoot $ProjectRoot),
            (Get-ErgoServiceName -Role 'media_api' -ProjectRoot $ProjectRoot),
            (Get-ErgoServiceName -Role 'celery_beat' -ProjectRoot $ProjectRoot)
        )
    }
    try {
        $out = & $python $script --root $ProjectRoot --core-units 2>$null
        if (-not $out) { return @() }
        return @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    } catch {
        return @(
            (Get-ErgoServiceName -Role 'api_dev' -ProjectRoot $ProjectRoot),
            (Get-ErgoServiceName -Role 'client_dev' -ProjectRoot $ProjectRoot),
            (Get-ErgoServiceName -Role 'media_api' -ProjectRoot $ProjectRoot),
            (Get-ErgoServiceName -Role 'celery_beat' -ProjectRoot $ProjectRoot)
        )
    }
}

function Get-ModuleHostServiceUnits {
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'
    $script = Join-Path $ProjectRoot 'core\deployment\scripts\host_lifecycle_loader.py'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $script)) {
        return @()
    }
    try {
        $out = & $python $script --root $ProjectRoot --units 2>$null
        if (-not $out) { return @() }
        return @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    } catch {
        return @()
    }
}

function Get-ModuleHostStopCommands {
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'
    $script = Join-Path $ProjectRoot 'core\deployment\scripts\host_lifecycle_loader.py'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $script)) {
        return @()
    }
    try {
        $out = & $python $script --root $ProjectRoot --stop-commands 2>$null
        if (-not $out) { return @() }
        return @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    } catch {
        return @()
    }
}

function Get-ModuleHostStopPairs {
    param([string]$ProjectRoot)
    $python = Join-Path $ProjectRoot 'virtual_env\python\Scripts\python.exe'
    $script = Join-Path $ProjectRoot 'core\deployment\scripts\host_lifecycle_loader.py'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $script)) {
        return @()
    }
    try {
        $out = & $python $script --root $ProjectRoot --stop-commands-paired 2>$null
        if (-not $out) { return @() }
        return @(
            $out | ForEach-Object {
                $line = "$_"
                if (-not $line) { return }
                $parts = $line -split "`t", 2
                $cmd = "$($parts[0])".Trim()
                if (-not $cmd) { return }
                $units = @()
                if ($parts.Count -gt 1 -and "$($parts[1])".Trim()) {
                    $units = @("$($parts[1])".Trim() -split '\s+' | Where-Object { $_ })
                }
                [pscustomobject]@{ Command = $cmd; Units = $units }
            }
        )
    } catch {
        return @()
    }
}

function Test-DedicatedInfraServiceName {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$ProjectRoot
    )
    if (
        (Test-ErgoServiceRole -Name $Name -Role 'redis') -or
        (Test-ErgoServiceRole -Name $Name -Role 'meilisearch') -or
        (Test-ErgoServiceRole -Name $Name -Role 'nginx')
    ) {
        return $true
    }
    $postgresName = Get-ErgoServiceName -Role 'postgres' -ProjectRoot $ProjectRoot
    if ($ProjectRoot) {
        $fromEnv = Get-ErgoEnvValue -Root $ProjectRoot -Name 'POSTGRES_SERVICE_WINDOWS'
        if ($fromEnv) { $postgresName = $fromEnv }
    }
    return ($Name -eq $postgresName)
}

function Get-ServiceNames {
    param([string]$ProjectRoot)

    $nginxEnabled = Test-NginxEnabled -ProjectRoot $ProjectRoot
    $redisEnabled = Test-RedisEnabled -ProjectRoot $ProjectRoot
    $searchEnabled = Test-SearchEnabled -ProjectRoot $ProjectRoot
    $postgresEnabled = Test-PostgresPortableEnabled -ProjectRoot $ProjectRoot
    $coreUnits = @(Get-HostProfileCoreUnits -ProjectRoot $ProjectRoot)
    $coreKey = ($coreUnits -join ',')
    $yamlWorkers = [bool](Test-HostProfileWants -ProjectRoot $ProjectRoot -ServiceId 'yaml_workers')
    $prefix = Get-ErgoServicePrefix -ProjectRoot $ProjectRoot

    if (
        $script:CachedServiceNames -and
        $script:CachedProjectRoot -eq $ProjectRoot -and
        $script:CachedServicePrefix -eq $prefix -and
        $script:CachedNginxEnabled -eq $nginxEnabled -and
        $script:CachedRedisEnabled -eq $redisEnabled -and
        $script:CachedSearchEnabled -eq $searchEnabled -and
        $script:CachedPostgresEnabled -eq $postgresEnabled -and
        $script:CachedHostCoreUnits -eq $coreKey -and
        $script:CachedYamlWorkers -eq $yamlWorkers
    ) {
        return $script:CachedServiceNames
    }

    $services = @() + $coreUnits
    $clientDev = Get-ErgoServiceName -Role 'client_dev' -ProjectRoot $ProjectRoot
    $nginxName = Get-ErgoServiceName -Role 'nginx' -ProjectRoot $ProjectRoot

    if ($postgresEnabled) {
        $postgresServiceName = Get-ErgoServiceName -Role 'postgres' -ProjectRoot $ProjectRoot
        $fromEnv = Get-ErgoEnvValue -Root $ProjectRoot -Name 'POSTGRES_SERVICE_WINDOWS'
        if ($fromEnv) { $postgresServiceName = $fromEnv }
        $services = @($postgresServiceName) + $services
    }

    if ($redisEnabled) {
        $services = @((Get-ErgoServiceName -Role 'redis' -ProjectRoot $ProjectRoot)) + $services
    }

    if ($searchEnabled) {
        $services = @((Get-ErgoServiceName -Role 'meilisearch' -ProjectRoot $ProjectRoot)) + $services
    }

    if ($nginxEnabled) {
        $services = $services | Where-Object { $_ -ne $clientDev }
        if ($services -notcontains $nginxName) {
            $services += $nginxName
        }
    }

    if (Test-HostProfileWants -ProjectRoot $ProjectRoot -ServiceId 'yaml_workers') {
        $workers = Get-CeleryWorkers -ProjectRoot $ProjectRoot
        $workerBase = Get-ErgoServiceName -Role 'celery_worker' -ProjectRoot $ProjectRoot
        if ($workers.Count -gt 0) {
            foreach ($worker in $workers) {
                $services += "${workerBase}_$worker"
            }
        }
        else {
            $services += $workerBase
        }
    }

    foreach ($unit in @(Get-ModuleHostServiceUnits -ProjectRoot $ProjectRoot)) {
        if ($unit -and ($services -notcontains $unit)) {
            $services += $unit
        }
    }

    $script:CachedServiceNames = @($services)
    $script:CachedProjectRoot = $ProjectRoot
    $script:CachedServicePrefix = $prefix
    $script:CachedNginxEnabled = $nginxEnabled
    $script:CachedRedisEnabled = $redisEnabled
    $script:CachedSearchEnabled = $searchEnabled
    $script:CachedPostgresEnabled = $postgresEnabled
    $script:CachedHostCoreUnits = $coreKey
    $script:CachedYamlWorkers = $yamlWorkers

    return $script:CachedServiceNames
}

function Get-WorkerServiceNames {

    param([string]$ProjectRoot)

    

    $services = @()

    

    if (-not (Test-HostProfileWants -ProjectRoot $ProjectRoot -ServiceId 'yaml_workers')) {
        return @()
    }

    $workers = Get-CeleryWorkers -ProjectRoot $ProjectRoot

    

    if ($workers.Count -gt 0) {

        $workerBase = Get-ErgoServiceName -Role 'celery_worker' -ProjectRoot $ProjectRoot
        foreach ($worker in $workers) {

            $services += "${workerBase}_$worker"

        }

    }

    else {

        $services += (Get-ErgoServiceName -Role 'celery_worker' -ProjectRoot $ProjectRoot)

    }

    

    return $services

}



# Сброс кэша списка служб

function Reset-ServiceNamesCache {
    $script:CachedServiceNames = $null
    $script:CachedProjectRoot = $null
    $script:CachedServicePrefix = $null
    $script:CachedNginxEnabled = $null
    $script:CachedRedisEnabled = $null
    $script:CachedSearchEnabled = $null
    $script:CachedPostgresEnabled = $null
    $script:CachedHostCoreUnits = $null
    $script:CachedYamlWorkers = $null
}

# Export-ModuleMember -Function *  # Удалено, так как это не модуль

