# Nginx management for Windows
# Установка, настройка и управление nginx на Windows

$script:NginxVersion = '1.27.4'
$script:NginxZipUrl = "https://nginx.org/download/nginx-$script:NginxVersion.zip"
$script:NginxServiceName = Get-ErgoServiceName -Role 'nginx'

function Sync-NginxServiceName {
    param([string]$Root)
    $script:NginxServiceName = Get-ErgoServiceName -Role 'nginx' -ProjectRoot $Root
}
$script:NginxConfName = 'ergo_ms'

function Invoke-NginxCli {
    param(
        [string]$NginxExe,
        [string[]]$Arguments,
        [string]$WorkingDirectory = ''
    )

    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = @()
    $exitCode = 0

    try {
        if ($WorkingDirectory) {
            Push-Location $WorkingDirectory
        }
        $output = @(& $NginxExe @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        if ($WorkingDirectory) {
            Pop-Location
        }
        $ErrorActionPreference = $prevEa
    }

    return @{
        Output = $output
        ExitCode = $exitCode
    }
}

function Get-NginxPackagesRelativePath {
    return Join-Path "virtual_env" (Join-Path "packages" "nginx")
}

function Get-NginxDir {
    param([string]$Root)
    return Join-Path $Root (Get-NginxPackagesRelativePath)
}

function Get-NginxExe {
    param([string]$Root)
    return Join-Path (Get-NginxDir -Root $Root) "nginx.exe"
}

function Test-NginxInstalled {
    param([string]$Root)
    return (Test-Path (Get-NginxExe -Root $Root))
}

function Read-NginxEnv {
    param([string]$Root)

    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return @{} }

    $result = @{}
    $lines = Get-Content -Path $envFile -Encoding UTF8

    foreach ($line in $lines) {
        if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -match '^(NGINX_[A-Z_]+|ERGO_SSL_CERT|ERGO_SSL_KEY)=(.*)$') {
            $result[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $result
}

function Invoke-NginxLogEnv {
    param(
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [string]$Key = ''
    )

    $pythonExe = Join-Path $Root 'virtual_env\python\Scripts\python.exe'
    $scriptPath = Join-Path $Root 'core\deployment\scripts\log_env.py'
    if (-not (Test-Path $pythonExe) -or -not (Test-Path $scriptPath)) {
        return ''
    }

    $args = @($scriptPath, $Command)
    if ($Key) {
        $args += $Key
    }
    $args += $Root
    return (& $pythonExe @args 2>$null)
}

function Get-NginxCentralLogsDir {
    param([string]$Root)

    $dir = Invoke-NginxLogEnv -Root $Root -Command 'logs-dir'
    if ($dir) {
        return $dir
    }
    return Join-Path $Root 'logs'
}

function Get-NginxErrorLogPath {
    param([string]$Root)

    $path = Invoke-NginxLogEnv -Root $Root -Command 'path' -Key 'NGINX_ERROR'
    if ($path) {
        return $path
    }
    return Join-Path (Get-NginxCentralLogsDir -Root $Root) 'nginx-error.log'
}

function Install-NginxBinary {
    param([string]$Root)

    $nginxDir = Get-NginxDir -Root $Root
    $nginxExe = Get-NginxExe -Root $Root

    if (Test-Path $nginxExe) {
        Write-ErgomsMessage -Key 'nginx_already_installed' -Color Green -Param @{ path = $nginxDir }
        return $nginxExe
    }

    Write-ErgomsMessage -Key 'nginx_downloading' -Color Yellow -Param @{ version = $script:NginxVersion }

    . (Join-Path $PSScriptRoot 'portable_archive.ps1')
    $cacheTmp = Join-Path $Root "virtual_env\cache\tmp"
    $downloads = Join-Path $Root "virtual_env\cache\downloads"
    New-Item -ItemType Directory -Path $cacheTmp -Force | Out-Null
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    $cacheZip = Join-Path $downloads "nginx-$($script:NginxVersion).zip"
    $tempZip = Join-Path $cacheTmp "nginx.zip"
    $tempExtract = Join-Path $cacheTmp "nginx_extract"

    try {
        if (-not (Test-CachedRuntimeArchive -Path $cacheZip)) {
            Save-RuntimeArchiveDownload -Url $script:NginxZipUrl -DestPath $cacheZip -Root $Root
        }
        Copy-Item -LiteralPath $cacheZip -Destination $tempZip -Force

        if (Test-Path $tempExtract) {
            Remove-Item $tempExtract -Recurse -Force
        }
        Expand-Archive -Path $tempZip -DestinationPath $tempExtract

        $extractedDir = Get-ChildItem -Path $tempExtract -Directory | Select-Object -First 1
        if (-not $extractedDir) {
            throw "Could not find extracted nginx directory"
        }

        if (Test-Path $nginxDir) {
            Remove-Item $nginxDir -Recurse -Force
        }

        New-Item -ItemType Directory -Path (Split-Path $nginxDir -Parent) -Force | Out-Null
        Move-Item -Path $extractedDir.FullName -Destination $nginxDir -Force

        Write-ErgomsMessage -Key 'nginx_installed_to' -Color Green -Param @{ path = $nginxDir }
    }
    finally {
        if (Test-Path $tempZip) { Remove-Item $tempZip -Force }
        if (Test-Path $tempExtract) { Remove-Item $tempExtract -Recurse -Force }
    }

    return $nginxExe
}

function Test-NginxTruthy {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $normalized = $Value.Trim().ToLowerInvariant()
    return $normalized -in @('1', 'true', 'yes')
}

function Test-NginxUseHttps {
    param([hashtable]$EnvVars, [string]$ListenPort = '80')
    if (Test-NginxTruthy $EnvVars['NGINX_USE_HTTPS']) { return $true }
    return $ListenPort -eq '443'
}

function Get-NginxTemplatePath {
    param(
        [string]$Root,
        [hashtable]$EnvVars,
        [string]$ListenPort = '80'
    )

    $nginxDir = Join-Path $Root "core\deployment\nginx"
    if ((Test-NginxUseHttps -EnvVars $EnvVars -ListenPort $ListenPort) -and
        (Test-Path (Join-Path $nginxDir "ergo_ms.conf.template"))) {
        return Join-Path $nginxDir "ergo_ms.conf.template"
    }
    return Join-Path $nginxDir "ergo_ms_http.conf.template"
}

function Warn-NginxInsecureCerts {
    param(
        [string]$CertPath,
        [string]$KeyPath
    )

    if ([string]::IsNullOrWhiteSpace($CertPath) -or [string]::IsNullOrWhiteSpace($KeyPath)) {
        Write-ErgomsMessage -Key 'nginx_ssl_vars_missing' -Color Yellow
        return
    }
    if ($CertPath -like '*snakeoil*' -or $KeyPath -like '*snakeoil*') {
        Write-ErgomsMessage -Key 'nginx_ssl_self_signed' -Color Yellow
    }
    if (-not (Test-Path $CertPath)) {
        Write-ErgomsMessage -Key 'nginx_ssl_cert_missing' -Color Yellow -Param @{ path = $CertPath }
    }
    if (-not (Test-Path $KeyPath)) {
        Write-ErgomsMessage -Key 'nginx_ssl_key_missing' -Color Yellow -Param @{ path = $KeyPath }
    }
}
