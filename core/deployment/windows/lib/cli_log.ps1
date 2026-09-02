# CLI session log: setup-full.log or ergoms.log (see cli_session_log.py).
# ASCII-only so Windows PowerShell 5.1 can parse this file without BOM.

function Get-CliLogPython {
    param([Parameter(Mandatory = $true)][string]$Root)

    $venvPy = Join-Path $Root 'virtual_env\python\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        return $venvPy
    }
    $portablePy = Join-Path $Root 'virtual_env\packages\python\python.exe'
    if (Test-Path -LiteralPath $portablePy) {
        return $portablePy
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Attach-CliSessionLog {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Command
    )

    if ($env:ERGO_CLI_LOG_ATTACHED -eq '1') {
        return
    }
    if (-not $Root -or -not $Command) {
        return
    }

    $script = Join-Path $Root 'core\deployment\scripts\cli_session_log.py'
    if (-not (Test-Path -LiteralPath $script)) {
        return
    }

    $py = Get-CliLogPython -Root $Root
    if (-not $py) {
        return
    }

    # PS 5.1 + ErrorActionPreference Stop: native stderr is a terminating error.
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $logPath = & $py $script prepare $Command $Root 2>$null
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($logPath)) {
        return
    }

    $logPath = $logPath.Trim()
    $logDir = Split-Path -Parent $logPath
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    $env:ERGO_CLI_LOG_ATTACHED = '1'
    try {
        Start-Transcript -Path $logPath -Append | Out-Null
    }
    catch {
        # Host already transcribing or path not writable: keep console, skip file.
    }
}
