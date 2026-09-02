# NSSM management for Windows services
# Управление NSSM для служб Windows (бинарь в virtual_env/packages/nssm)

$script:NssmUrl = 'https://nssm.cc/release/nssm-2.24.zip'

function Get-NssmDir {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )
    return Join-Path $Root "virtual_env\packages\nssm"
}

function Install-NSSM {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $nssmDir = Get-NssmDir -Root $Root
    $nssmExe = Join-Path $nssmDir "nssm.exe"

    if (Test-Path -LiteralPath $nssmExe) {
        Write-ErgomsMessage -Key 'nssm_already_installed' -Color Green -Param @{ path = $nssmExe }
        return $nssmExe
    }

    Write-ErgomsMessage -Key 'nssm_downloading' -Color Yellow
    . (Join-Path $PSScriptRoot 'portable_archive.ps1')
    $cacheTmp = Join-Path $Root "virtual_env\cache\tmp"
    $downloads = Join-Path $Root "virtual_env\cache\downloads"
    New-Item -ItemType Directory -Path $cacheTmp -Force | Out-Null
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    $cacheZip = Join-Path $downloads "nssm-2.24.zip"
    $tempZip = Join-Path $cacheTmp "nssm.zip"
    $tempExtract = Join-Path $cacheTmp "nssm_extract"

    try {
        if (-not (Test-CachedRuntimeArchive -Path $cacheZip)) {
            Save-RuntimeArchiveDownload -Url $script:NssmUrl -DestPath $cacheZip -Root $Root
        }
        Copy-Item -LiteralPath $cacheZip -Destination $tempZip -Force

        if (Test-Path -LiteralPath $tempExtract) {
            Remove-Item -LiteralPath $tempExtract -Recurse -Force
        }
        Expand-Archive -Path $tempZip -DestinationPath $tempExtract

        $nssmSource = Get-ChildItem -Path $tempExtract -Filter "nssm.exe" -Recurse |
            Where-Object { $_.FullName -like "*win64*" } |
            Select-Object -First 1

        if (-not $nssmSource) {
            throw "Could not find nssm.exe in downloaded archive"
        }

        New-Item -ItemType Directory -Path $nssmDir -Force | Out-Null
        Copy-Item $nssmSource.FullName -Destination $nssmExe -Force

        Write-ErgomsMessage -Key 'nssm_installed' -Color Green -Param @{ path = $nssmExe }
    }
    finally {
        if (Test-Path -LiteralPath $tempZip) { Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $tempExtract) { Remove-Item -LiteralPath $tempExtract -Recurse -Force -ErrorAction SilentlyContinue }
    }

    return $nssmExe
}

# Создание wrapper'а для базовых служб (API, Client, Beat)
function New-BaseServiceWrapper {
    param(
        [string]$ServiceName,
        [string]$Root
    )

    $corePath = Join-Path $Root "core"
    $venvActivate = Join-Path $Root "virtual_env\python\Scripts\activate.bat"
    $wrapperDir = Get-ProjectWrappersDir -ProjectRoot $Root

    New-Item -ItemType Directory -Path $wrapperDir -Force | Out-Null

    switch -Regex ($ServiceName) {
        '_api_dev$' {
            $wrapperPath = Join-Path $wrapperDir "start_api.bat"
            $pythonExe = Join-Path $Root "virtual_env\python\Scripts\python.exe"
            $scriptPath = Join-Path $Root "core\api\scripts\start_api.py"
            $content = @(
                '@echo off',
                'chcp 65001 >nul',
                'set PYTHONIOENCODING=utf-8',
                'set PYTHONUTF8=1',
                "cd /d `"$Root`"",
                "call `"$pythonExe`" `"$scriptPath`""
            ) -join "`r`n"
        }
        '_client_dev$' {
            $wrapperPath = Join-Path $wrapperDir "start_client.bat"
            $pythonExe = Join-Path $Root "virtual_env\python\Scripts\python.exe"
            $scriptPath = Join-Path $Root "core\deployment\scripts\start_client_if_dev.py"
            $content = @(
                '@echo off',
                'chcp 65001 >nul',
                'set NO_COLOR=1',
                'set FORCE_COLOR=0',
                'set npm_config_color=false',
                "cd /d `"$Root`"",
                "call `"$pythonExe`" `"$scriptPath`""
            ) -join "`r`n"
        }
        '_celery_beat$' {
            $wrapperPath = Join-Path $wrapperDir "start_celery_beat.bat"
            $scriptPath = Join-Path $corePath "api\scripts\start_celery_beat.py"
            $content = @(
                '@echo off',
                'chcp 65001 >nul',
                'set PYTHONIOENCODING=utf-8',
                'set PYTHONUTF8=1',
                "cd /d `"$corePath`"",
                "call `"$venvActivate`"",
                "python `"$scriptPath`""
            ) -join "`r`n"
        }
        '_media_api$' {
            $wrapperPath = Join-Path $wrapperDir "start_media_api.bat"
            $scriptPath = Join-Path $corePath "api\scripts\start_media_api.py"
            $content = @(
                '@echo off',
                'chcp 65001 >nul',
                'set PYTHONIOENCODING=utf-8',
                'set PYTHONUTF8=1',
                "cd /d `"$Root`"",
                "call `"$venvActivate`"",
                "python `"$scriptPath`""
            ) -join "`r`n"
        }
        default {
            throw "Unknown base service: $ServiceName"
        }
    }

    Set-Content -Path $wrapperPath -Value $content -Encoding ASCII
    return $wrapperPath
}

function New-WorkerServiceWrapper {
    param(
        [string]$WorkerName,
        [string]$Root
    )

    $corePath = Join-Path $Root "core"
    $venvActivate = Join-Path $Root "virtual_env\python\Scripts\activate.bat"
    $wrapperDir = Get-ProjectWrappersDir -ProjectRoot $Root

    New-Item -ItemType Directory -Path $wrapperDir -Force | Out-Null

    $scriptPath = Join-Path $corePath "api\scripts\start_celery_worker.py"
    $wrapperPath = Join-Path $wrapperDir "start_celery_worker_${WorkerName}.bat"
    $content = @(
        '@echo off',
        'chcp 65001 >nul',
        'set PYTHONIOENCODING=utf-8',
        'set PYTHONUTF8=1',
        "cd /d `"$corePath`"",
        "call `"$venvActivate`"",
        "python `"$scriptPath`" --worker=$WorkerName"
    ) -join "`r`n"

    Set-Content -Path $wrapperPath -Value $content -Encoding ASCII
    return $wrapperPath
}

function New-DefaultWorkerServiceWrapper {
    param(
        [string]$Root
    )

    $corePath = Join-Path $Root "core"
    $venvActivate = Join-Path $Root "virtual_env\python\Scripts\activate.bat"
    $wrapperDir = Get-ProjectWrappersDir -ProjectRoot $Root

    New-Item -ItemType Directory -Path $wrapperDir -Force | Out-Null

    $scriptPath = Join-Path $corePath "api\scripts\start_celery_worker.py"
    $wrapperPath = Join-Path $wrapperDir "start_celery_worker.bat"
    $content = @(
        '@echo off',
        'chcp 65001 >nul',
        'set PYTHONIOENCODING=utf-8',
        'set PYTHONUTF8=1',
        "cd /d `"$corePath`"",
        "call `"$venvActivate`"",
        "python `"$scriptPath`""
    ) -join "`r`n"

    Set-Content -Path $wrapperPath -Value $content -Encoding ASCII
    return $wrapperPath
}

function New-ServiceWrapper {
    param(
        [string]$ServiceName,
        [string]$Root
    )

    if ($ServiceName -match '_celery_worker_(.+)$') {
        $workerName = $Matches[1]
        return New-WorkerServiceWrapper -WorkerName $workerName -Root $Root
    }

    if ($ServiceName -match '_celery_worker$') {
        return New-DefaultWorkerServiceWrapper -Root $Root
    }

    return New-BaseServiceWrapper -ServiceName $ServiceName -Root $Root
}
