# Lifecycle runner invocation for Windows ergo_ms

function Ensure-PortableRuntimesForSetup {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$PythonOnly,
        [switch]$NodeOnly,
        [switch]$RespectEnv
    )

    . (Join-Path $PSScriptRoot 'portable_env.ps1')
    . (Join-Path $PSScriptRoot 'portable_python.ps1')
    . (Join-Path $PSScriptRoot 'portable_nodejs.ps1')

    if (-not $NodeOnly) {
        if ($RespectEnv -and -not (Test-PortablePythonEnabled -Root $Root)) {
            Write-ColorOutput (Format-ErgoConsole -Level skip -Message 'PORTABLE_PYTHON_ENABLED=false — portable Python не устанавливается') Gray
        } else {
            Install-PortablePython -Root $Root | Out-Null
        }
    }
    if (-not $PythonOnly) {
        if ($RespectEnv -and -not (Test-PortableNodejsEnabled -Root $Root)) {
            Write-ColorOutput (Format-ErgoConsole -Level skip -Message 'PORTABLE_NODEJS_ENABLED=false — portable Node.js не устанавливается') Gray
        } else {
            Install-PortableNodejs -Root $Root | Out-Null
        }
    }
}

function Get-LifecyclePythonExe {
    param([Parameter(Mandatory = $true)][string]$Root)

    $venvPy = Join-Path $Root 'virtual_env\python\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        return $venvPy
    }

    $portablePy = Join-Path $Root 'virtual_env\packages\python\python.exe'
    if (Test-Path -LiteralPath $portablePy) {
        return $portablePy
    }

    return $null
}

function Invoke-LifecycleRunner {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Recipe,
        [string[]]$ExtraArgs = @()
    )

    $runner = Join-Path $Root 'core\deployment\lifecycle\runner.py'
    if (-not (Test-Path $runner)) {
        Write-ColorOutput "[ERROR] lifecycle runner не найден: $runner" Red
        exit 1
    }

    switch ($Recipe) {
        'setup-full' {
            Ensure-PortableRuntimesForSetup -Root $Root -RespectEnv
        }
        { $_ -in @('install-python', 'install-python-runtime') } {
            Ensure-PortableRuntimesForSetup -Root $Root -PythonOnly
        }
        { $_ -in @('install-nodejs', 'install-node') } {
            Ensure-PortableRuntimesForSetup -Root $Root -NodeOnly
        }
    }

    $argv = @($runner, $Recipe, '--project-root', $Root) + $ExtraArgs
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
