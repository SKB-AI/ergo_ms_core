# Clean project dependencies
# Очистка зависимостей проекта

function Clear-ProjectShellEnvironment {
    param(
        [string]$VenvPath
    )

    if (-not (Test-Path -LiteralPath $VenvPath)) {
        if ($env:VIRTUAL_ENV) {
            Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
        }
        return
    }

    $venvNorm = [System.IO.Path]::GetFullPath($VenvPath).TrimEnd('\')

    if ($env:VIRTUAL_ENV) {
        $activeNorm = [System.IO.Path]::GetFullPath($env:VIRTUAL_ENV).TrimEnd('\')
        if ($activeNorm -eq $venvNorm) {
            Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
            Write-ErgomsMessage -Key 'clean_venv_cleared' -Color Gray
        }
    }

    $scriptsPath = Join-Path $venvNorm "Scripts"
    if ($env:PATH) {
        $parts = $env:PATH -split ';' | Where-Object {
            $_ -and ([System.IO.Path]::GetFullPath($_).TrimEnd('\') -ne $scriptsPath)
        }
        $env:PATH = ($parts -join ';')
    }
}

function Stop-BlockingProcessesForClean {
    param(
        [string]$Root
    )

    Write-ErgomsMessage -Key 'clean_stopping_blockers' -Color Gray

    # Службы текущего корня — {prefix}_*; ergo-* только у префикса по умолчанию
    $prefix = Get-ErgoServicePrefix -ProjectRoot $Root
    $wildcard = "${prefix}_*"
    $services = @(
        Get-Service -Name $wildcard -ErrorAction SilentlyContinue
    )
    if ($prefix -eq 'ergo_ms') {
        $services += @(Get-Service -Name 'ergo-*' -ErrorAction SilentlyContinue)
    }
    $services = $services | Where-Object { $_ -and $_.Status -ne 'Stopped' } | Sort-Object -Property Name -Unique

    foreach ($svc in $services) {
        try {
            Stop-Service -Name $svc.Name -Force -ErrorAction Stop
            Write-ErgomsMessage -Key 'clean_service_stopped' -Color Gray -Param @{ name = $svc.Name }
        }
        catch {
            Write-ErgomsMessage -Key 'clean_warn_stop_service_admin' -Color Yellow -Param @{ name = $svc.Name }
        }
    }

    $rootLower = $Root.ToLowerInvariant()
    $venvLower = (Join-Path $Root "virtual_env\python").ToLowerInvariant()
    $packagesLower = (Join-Path $Root "virtual_env\packages").ToLowerInvariant()
    $packagesLowerFwd = $packagesLower.Replace('\', '/')
    $pythonExe = (Join-Path $Root "virtual_env\python\Scripts\python.exe").ToLowerInvariant()

    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $exe = $_.ExecutablePath
        $cmd = $_.CommandLine
        if (-not $exe -and -not $cmd) { return $false }
        $exeLower = if ($exe) { $exe.ToLowerInvariant() } else { "" }
        $cmdLower = if ($cmd) { $cmd.ToLowerInvariant() } else { "" }
        $cmdNorm = $cmdLower.Replace('/', '\')

        # portable nginx / redis / postgres / nssm — иначе clean не удалит virtual_env/packages
        if ($exeLower.StartsWith($packagesLower) -or $cmdNorm.Contains($packagesLower) -or $cmdLower.Contains($packagesLowerFwd)) {
            return $true
        }
        if ($exeLower.StartsWith($venvLower) -or $cmdLower.Contains($venvLower)) { return $true }
        if ($exeLower -eq $pythonExe) { return $true }
        if ($exeLower.EndsWith("\python.exe") -and $cmdLower.Contains($rootLower)) { return $true }
        if ($exeLower.EndsWith("\pythonw.exe") -and ($exeLower.StartsWith($rootLower) -or $cmdLower.Contains($rootLower))) { return $true }
        if ($exeLower.EndsWith("\pip.exe") -and $exeLower.StartsWith($venvLower)) { return $true }
        if ($exeLower.EndsWith("\esbuild.exe") -and ($exeLower.StartsWith($rootLower) -or $cmdLower.Contains($rootLower))) { return $true }
        if ($exeLower.EndsWith("\node.exe") -and $cmdLower.Contains($rootLower)) { return $true }

        return $false
    }

    $stopped = 0
    foreach ($p in $procs) {
        if ($p.ProcessId -eq $PID) { continue }
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-ErgomsMessage -Key 'clean_process_stopped' -Color Gray -Param @{ name = $p.Name; pid = $p.ProcessId }
            $stopped++
        }
        catch {
            Write-ErgomsMessage -Key 'clean_warn_stop_pid' -Color Yellow -Param @{ pid = $p.ProcessId; name = $p.Name }
        }
    }

    if ($stopped -gt 0 -or $services.Count -gt 0) {
        Start-Sleep -Milliseconds 800
    }
}

function Test-RobocopySuccess {
    param([int]$ExitCode)
    return ($ExitCode -ge 0 -and $ExitCode -le 7)
}

function Invoke-RobocopyMirrorEmpty {
    param(
        [string]$TargetPath,
        [string[]]$ExcludeFiles = @(),
        [string]$ProjectRoot = ''
    )

    if (-not (Test-Path -LiteralPath $TargetPath -PathType Container)) {
        return $false
    }

    if (-not $ProjectRoot) {
        $cursor = (Resolve-Path -LiteralPath $TargetPath).Path
        while ($cursor) {
            if (Test-Path -LiteralPath (Join-Path $cursor 'core\deployment')) {
                $ProjectRoot = $cursor
                break
            }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { break }
            $cursor = $parent
        }
    }

    if (-not $ProjectRoot) {
        return $false
    }

    $cacheTmp = Join-Path $ProjectRoot "virtual_env\cache\tmp"
    New-Item -ItemType Directory -Path $cacheTmp -Force | Out-Null
    $emptyDir = Join-Path $cacheTmp ("ergo_clean_empty_" + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $emptyDir -Force | Out-Null
        $argumentList = @(
            "`"$emptyDir`"",
            "`"$TargetPath`"",
            "/mir", "/mt:16", "/r:1", "/w:1",
            "/nfl", "/ndl", "/njh", "/njs", "/nc", "/ns", "/np"
        )
        foreach ($name in $ExcludeFiles) {
            $argumentList += @("/xf", "`"$name`"")
        }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "robocopy.exe"
        $psi.Arguments = ($argumentList -join ' ')
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        $null = $proc.StandardOutput.ReadToEnd()
        $null = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        return (Test-RobocopySuccess $proc.ExitCode)
    }
    finally {
        Remove-Item -LiteralPath $emptyDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Remove-PathWithCmdRmdir {
    param([string]$Path)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "cmd.exe"
    $psi.Arguments = "/c rd /s /q `"$Path`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $null = $proc.StandardOutput.ReadToEnd()
    $null = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return -not (Test-Path -LiteralPath $Path)
}

function Remove-PathRobust {
    param(
        [string]$Path,
        [int]$MaxRetries = 3,
        [int]$RetryDelayMs = 500
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }

    $isDirectory = Test-Path -LiteralPath $Path -PathType Container

    if ($isDirectory) {
        if ((Invoke-RobocopyMirrorEmpty -TargetPath $Path) -and (Remove-PathWithCmdRmdir -Path $Path)) {
            return $true
        }
        if (-not (Test-Path -LiteralPath $Path)) {
            return $true
        }
        if (Remove-PathWithCmdRmdir -Path $Path) {
            return $true
        }
    }

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return $true
        }
        catch {
            if ($attempt -lt $MaxRetries) {
                Start-Sleep -Milliseconds $RetryDelayMs
            }
        }
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }

    if ($isDirectory -and (Invoke-RobocopyMirrorEmpty -TargetPath $Path)) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $Path)) {
            return $true
        }
        return (Remove-PathWithCmdRmdir -Path $Path)
    }

    return -not (Test-Path -LiteralPath $Path)
}

function Test-CleanTargetHasWork {
    param(
        [string]$Path,
        [bool]$FullRemove
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }

    if ($FullRemove) {
        return $true
    }

    $items = @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '.gitkeep' })
    return ($items.Count -gt 0)
}

function New-CleanTrashStaging {
    param(
        [string]$Root
    )

    # Каталог в корне проекта — тот же том, что и цели (мгновенный Move-Item)
    $staging = Join-Path $Root (".ergo_clean_trash_" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    return $staging
}

function Move-PathToCleanTrash {
    param(
        [string]$Path,
        [string]$StagingRoot
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    $leaf = Split-Path -Leaf $Path
    $dest = Join-Path $StagingRoot $leaf
    if (Test-Path -LiteralPath $dest) {
        $dest = Join-Path $StagingRoot ($leaf + "_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    }

    try {
        Move-Item -LiteralPath $Path -Destination $dest -Force -ErrorAction Stop
        return $dest
    }
    catch {
        return $null
    }
}

function Start-BackgroundTrashRemoval {
    param(
        [string]$StagingRoot
    )

    if (-not $StagingRoot -or -not (Test-Path -LiteralPath $StagingRoot)) {
        return
    }

    # Фоновый процесс: не блокируем ergoms clean
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "cmd.exe"
    $psi.Arguments = "/c rd /s /q `"$StagingRoot`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    [void][System.Diagnostics.Process]::Start($psi)
}

function New-EmptyGitkeepFile {
    param(
        [string]$Path
    )

    # Пустой файл без перевода строки (Set-Content пишет CRLF и ломает git status)
    [System.IO.File]::WriteAllBytes($Path, [byte[]]@())
}

function Restore-CleanDirectorySkeleton {
    param(
        [string]$Path,
        [bool]$WithGitkeep
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }

    if ($WithGitkeep) {
        $gitkeep = Join-Path $Path '.gitkeep'
        if (-not (Test-Path -LiteralPath $gitkeep)) {
            New-EmptyGitkeepFile -Path $gitkeep
        }
    }
}

function Restore-GitkeepFromTrash {
    param(
        [string]$TargetDir,
        [string]$TrashDir
    )

    $dest = Join-Path $TargetDir '.gitkeep'
    $saved = Join-Path $TrashDir '.gitkeep'
    if (Test-Path -LiteralPath $saved) {
        Move-Item -LiteralPath $saved -Destination $dest -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $dest)) {
        New-EmptyGitkeepFile -Path $dest
    }
}

function Remove-DirectoryContents {
    param(
        [string]$Path,
        [string]$Label,
        [string]$Root,
        [string]$StagingRoot
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-ErgomsMessage -Key 'clean_skip_not_found' -Color Gray -Param @{ label = $Label }
        return @{ Ok = $true; Async = $false; Count = 0 }
    }

    $items = @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '.gitkeep' })

    if ($items.Count -eq 0) {
        Write-ErgomsMessage -Key 'clean_skip_already_empty' -Color Gray -Param @{ label = $Label }
        return @{ Ok = $true; Async = $false; Count = 0 }
    }

    $removedCount = $items.Count
    $moved = Move-PathToCleanTrash -Path $Path -StagingRoot $StagingRoot
    if ($moved) {
        # Каталог уехал целиком (включая .gitkeep) — возвращаем исходный .gitkeep
        Restore-CleanDirectorySkeleton -Path $Path -WithGitkeep $false
        Restore-GitkeepFromTrash -TargetDir $Path -TrashDir $moved
        Write-ErgomsMessage -Key 'clean_ok_removed_count_bg' -Color Green -Param @{ count = $removedCount; label = $Label }
        return @{ Ok = $true; Async = $true; Count = $removedCount }
    }

    # Запасной путь: синхронная очистка на месте
    $cleared = Invoke-RobocopyMirrorEmpty -TargetPath $Path -ExcludeFiles @('.gitkeep')
    $failedItems = @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '.gitkeep' } |
        ForEach-Object { $_.Name })

    if (-not $cleared -or $failedItems.Count -gt 0) {
        Stop-BlockingProcessesForClean -Root $Root
        $retryFailed = @()
        foreach ($name in $failedItems) {
            $fullName = Join-Path $Path $name
            if (-not (Test-Path -LiteralPath $fullName)) { continue }
            if (-not (Remove-PathRobust -Path $fullName -MaxRetries 5 -RetryDelayMs 400)) {
                $retryFailed += $name
            }
        }
        $failedItems = $retryFailed
    }

    if ($failedItems.Count -gt 0) {
        Write-ErgomsMessage -Key 'clean_error_clear_failed' -Color Red -Stderr -Param @{ label = $Label; items = ($failedItems -join ', ') }
        Write-ErgomsMessage -Key 'clean_hint_close_venv' -Color Yellow
        return @{ Ok = $false; Async = $false; Count = 0 }
    }

    Write-ErgomsMessage -Key 'clean_ok_removed_count' -Color Green -Param @{ count = $removedCount; label = $Label }
    return @{ Ok = $true; Async = $false; Count = $removedCount }
}

function Remove-FullPathFast {
    param(
        [string]$Path,
        [string]$Label,
        [string]$Root,
        [string]$StagingRoot
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-ErgomsMessage -Key 'clean_skip_not_found' -Color Gray -Param @{ label = $Label }
        return @{ Ok = $true; Async = $false }
    }

    $moved = Move-PathToCleanTrash -Path $Path -StagingRoot $StagingRoot
    if ($moved) {
        Write-ErgomsMessage -Key 'clean_ok_label_removed_bg' -Color Green -Param @{ label = $Label }
        return @{ Ok = $true; Async = $true }
    }

    if (Remove-PathRobust -Path $Path) {
        Write-ErgomsMessage -Key 'clean_ok_label_removed' -Color Green -Param @{ label = $Label }
        return @{ Ok = $true; Async = $false }
    }

    Stop-BlockingProcessesForClean -Root $Root
    if (Remove-PathRobust -Path $Path -MaxRetries 5 -RetryDelayMs 400) {
        Write-ErgomsMessage -Key 'clean_ok_label_removed' -Color Green -Param @{ label = $Label }
        return @{ Ok = $true; Async = $false }
    }

    Write-ErgomsMessage -Key 'clean_error_remove_failed' -Color Red -Stderr -Param @{ label = $Label }
    Write-ErgomsMessage -Key 'clean_hint_close_terminals' -Color Yellow
    return @{ Ok = $false; Async = $false }
}

function Clear-ProjectDependencies {
    param(
        [string]$Root
    )

    $cleanTargets = @(
        @{Path = "virtual_env\npm\node_modules"; Label = "virtual_env/npm/node_modules"; FullRemove = $true},
        @{Path = "node_modules";               Label = "node_modules (legacy)";       FullRemove = $true},
        @{Path = "virtual_env\python";          Label = "virtual_env/python";          FullRemove = $false},
        @{Path = "virtual_env\static_api";      Label = "virtual_env/static_api";      FullRemove = $false},
        @{Path = "virtual_env\celery";          Label = "virtual_env/celery";          FullRemove = $false},
        @{Path = "virtual_env\nodejs";          Label = "virtual_env/nodejs";          FullRemove = $false},
        @{Path = "virtual_env\packages";        Label = "virtual_env/packages";        FullRemove = $false},
        @{Path = "virtual_env\resources";       Label = "virtual_env/resources";       FullRemove = $false},
        @{Path = "virtual_env\trained_models";  Label = "virtual_env/trained_models";  FullRemove = $false},
        @{Path = "virtual_env\cache";           Label = "virtual_env/cache";           FullRemove = $false},
        # Legacy sibling path (до переноса в virtual_env/cache/docker-cache)
        @{Path = "virtual_env\docker-cache";    Label = "virtual_env/docker-cache (legacy)"; FullRemove = $true}
    )

    Write-Host ""; Write-ErgomsMessage -Key 'clean_heading' -Color Cyan
    Write-ColorOutput ""
    Write-ErgomsMessage -Key 'clean_will_remove' -Color Yellow
    foreach ($target in $cleanTargets) {
        Write-ColorOutput "  - $($target.Label)" Gray
    }
    Write-ColorOutput ""
    Write-ErgomsMessage -Key 'clean_media_kept' -Color Green
    Write-ColorOutput ""

    Write-ErgomsMessage -Key 'clean_confirm' -Color White; $confirmation = Read-Host
    if ($confirmation -notmatch '^[yY]$') {
        Write-ErgomsMessage -Key 'clean_cancelled' -Color Yellow
        return
    }

    $workTargets = @()
    foreach ($target in $cleanTargets) {
        $fullPath = Join-Path $Root $target.Path
        if (Test-CleanTargetHasWork -Path $fullPath -FullRemove:$target.FullRemove) {
            $workTargets += $target
        }
    }

    if ($workTargets.Count -eq 0) {
        Write-Host ""; Write-ErgomsMessage -Key 'clean_skip_nothing' -Color Gray
        Write-Host ""; Write-ErgomsMessage -Key 'clean_done_heading' -Color Green
        Write-ColorOutput ""
        return
    }

    Stop-BlockingProcessesForClean -Root $Root
    Clear-ProjectShellEnvironment -VenvPath (Join-Path $Root "virtual_env\python")

    $staging = New-CleanTrashStaging -Root $Root
    $anyAsync = $false
    $total = $cleanTargets.Count

    for ($i = 0; $i -lt $total; $i++) {
        $target = $cleanTargets[$i]
        $step = $i + 1
        $fullPath = Join-Path $Root $target.Path
        Write-Host ""; Write-ErgomsMessage -Key 'clean_step' -Color Yellow -Param @{ step = $step; total = $total; label = $target.Label }

        if (-not (Test-CleanTargetHasWork -Path $fullPath -FullRemove:$target.FullRemove)) {
            if ($target.FullRemove -and -not (Test-Path -LiteralPath $fullPath)) {
                Write-ErgomsMessage -Key 'clean_skip_not_found' -Color Gray -Param @{ label = $target.Label }
            }
            elseif (-not $target.FullRemove) {
                if (-not (Test-Path -LiteralPath $fullPath)) {
                    Write-ErgomsMessage -Key 'clean_skip_not_found' -Color Gray -Param @{ label = $target.Label }
                }
                else {
                    Write-ErgomsMessage -Key 'clean_skip_already_empty' -Color Gray -Param @{ label = $target.Label }
                }
            }
            else {
                Write-ErgomsMessage -Key 'clean_skip_already_empty' -Color Gray -Param @{ label = $target.Label }
            }
            continue
        }

        if ($target.FullRemove) {
            $result = Remove-FullPathFast -Path $fullPath -Label $target.Label -Root $Root -StagingRoot $staging
        }
        else {
            $result = Remove-DirectoryContents -Path $fullPath -Label $target.Label -Root $Root -StagingRoot $staging
        }

        if ($result.Async) {
            $anyAsync = $true
        }
    }

    if ($anyAsync) {
        Start-BackgroundTrashRemoval -StagingRoot $staging
        Write-Host ""; Write-ErgomsMessage -Key 'clean_info_async' -Color Gray
    }
    else {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""; Write-ErgomsMessage -Key 'clean_done_heading' -Color Green
    Write-ColorOutput ""
    Write-ErgomsMessage -Key 'clean_reinstall_hint' -Color Cyan
    Write-ColorOutput "  ergoms setup" Yellow
    Write-ColorOutput ""
}