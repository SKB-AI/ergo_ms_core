$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = (Resolve-Path "$ScriptDir\..\..\..\..\..").ProviderPath
Set-Location $RootDir

. "$ScriptDir\lib.ps1"

# Живые службы ОС больше не чистят рабочий virtual_env.
# Изолированный прогон с тестовым префиксом: ergoms system-test --suite os-services
Step "test.ps1: ergoms system-test --suite os-services"
$python = Join-Path $RootDir "virtual_env\python\Scripts\python.exe"
$script = Join-Path $RootDir "core\deployment\scripts\run_system_test.py"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Нет venv python: $python"
    exit 1
}
& $python $script --suite os-services --launch os-services
exit $LASTEXITCODE
