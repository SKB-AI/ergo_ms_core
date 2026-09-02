# Effective search: ERGO_SEARCH_ENABLED (по умолчанию true, как в ergo_modes).

function Test-SearchEnabled {
    param([string]$ProjectRoot)

    if (-not $ProjectRoot) {
        try { $ProjectRoot = Get-ProjectRoot } catch { return $true }
    }

    $candidates = @(
        (Join-Path $ProjectRoot '.env'),
        (Join-Path $ProjectRoot 'env\search.env')
    )

    $explicit = $null
    foreach ($envFile in $candidates) {
        if (-not (Test-Path $envFile)) { continue }
        foreach ($line in Get-Content -Path $envFile -Encoding UTF8) {
            if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) { continue }
            if ($line -match '^ERGO_SEARCH_ENABLED=(.*)$') {
                $value = $Matches[1].Trim().Trim('"').Trim("'").ToLower()
                $explicit = $value -in @('1', 'true', 'yes', 'on')
            }
        }
    }

    if ($null -ne $explicit) {
        return [bool]$explicit
    }
    return $true
}
