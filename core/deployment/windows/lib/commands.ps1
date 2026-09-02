# Custom commands management
# Управление пользовательскими командами

function Get-CustomCommands {
    param([string]$ProjectRoot)
    
    $commands = @{}
    
    # Load core commands
    $configPath = Join-Path $ProjectRoot "core\deployment\commands.conf"
    if (Test-Path $configPath) {
        Get-Content $configPath | ForEach-Object {
            $line = $_.Trim()
            # Skip comments and empty lines
            if ($line -and -not $line.StartsWith('#') -and -not $line.StartsWith('=')) {
                if ($line -match '^([a-zA-Z0-9_-]+)=(.+)$') {
                    $cmdName = $matches[1].Trim()
                    $cmdValue = $matches[2].Trim()
                    $commands[$cmdName] = $cmdValue
                }
            }
        }
    }
    
    # Load module commands
    $modulesPath = Join-Path $ProjectRoot "modules"
    if (Test-Path $modulesPath) {
        Get-ChildItem -Path $modulesPath -Directory | ForEach-Object {
            $moduleName = $_.Name
            $moduleConfigPath = Join-Path $_.FullName "ergoms.conf"
            
            if (Test-Path $moduleConfigPath) {
                Get-Content $moduleConfigPath | ForEach-Object {
                    $line = $_.Trim()
                    # Skip comments and empty lines
                    if ($line -and -not $line.StartsWith('#') -and -not $line.StartsWith('=')) {
                        if ($line -match '^([a-zA-Z0-9_-]+)=(.+)$') {
                            $cmdName = $matches[1].Trim()
                            $cmdValue = $matches[2].Trim()
                            
                            # Add module prefix to command name
                            $prefixedName = "$moduleName`:$cmdName"
                            $commands[$prefixedName] = $cmdValue
                        }
                    }
                }
            }
        }
    }
    
    return $commands
}

function Test-ShouldRunOnThisPlatform {
    param([string]$CommandString)
    if ($CommandString -match '^linux:') {
        return $false
    }
    return $true
}

function Split-CompositeCommand {
    param([string]$CommandDef)
    $parts = New-Object System.Collections.Generic.List[string]
    $buf = New-Object System.Text.StringBuilder
    $quote = [char]0
    $escaped = $false
    $chars = $CommandDef.ToCharArray()
    $i = 0
    while ($i -lt $chars.Length) {
        $ch = $chars[$i]
        if ($quote -ne [char]0) {
            [void]$buf.Append($ch)
            if ($escaped) {
                $escaped = $false
            } elseif ($ch -eq [char]'\') {
                $escaped = $true
            } elseif ($ch -eq $quote) {
                $quote = [char]0
            }
            $i++
            continue
        }
        if ($ch -eq '"' -or $ch -eq "'") {
            $quote = $ch
            [void]$buf.Append($ch)
            $i++
            continue
        }
        if ($ch -eq '&' -and ($i + 1) -lt $chars.Length -and $chars[$i + 1] -eq '&') {
            $part = $buf.ToString().Trim()
            if ($part) {
                $parts.Add($part)
            }
            [void]$buf.Clear()
            $i += 2
            continue
        }
        [void]$buf.Append($ch)
        $i++
    }
    $tail = $buf.ToString().Trim()
    if ($tail) {
        $parts.Add($tail)
    }
    return , $parts.ToArray()
}

function Add-PortableNodejsToPath {
    param([Parameter(Mandatory = $true)][string]$Root)
    $nodeDir = Join-Path $Root 'virtual_env\packages\nodejs'
    if (Test-Path -LiteralPath $nodeDir) {
        $env:PATH = "$nodeDir;$env:PATH"
    }
    return $nodeDir
}

function Invoke-CustomCommand {
    param(
        [string]$CommandName,
        [string[]]$CommandArgs,
        [string]$ProjectRoot
    )
    
    $customCommands = Get-CustomCommands -ProjectRoot $ProjectRoot
    
    if (-not $customCommands.ContainsKey($CommandName)) {
        Write-ErgomsMessage -Key 'unknown_command' -Color Red -Stderr -Param @{ name = $CommandName }
        Write-ErgomsMessage -Key 'cmd_available_custom' -Color Yellow -Param @{ items = ($customCommands.Keys -join ', ') }
        Write-ErgomsMessage -Key 'help_hint' -Color Cyan
        exit 1
    }
    
    $commandDef = $customCommands[$CommandName]
    
    # Check if it's a composite command (contains &&)
    if ($commandDef -match '&&') {
        $subCommands = Split-CompositeCommand -CommandDef $commandDef
        Write-ErgomsMessage -Key 'cmd_running_composite' -Color Cyan -Param @{ name = $CommandName }
        
        foreach ($subCmd in $subCommands) {
            if (-not (Test-ShouldRunOnThisPlatform -CommandString $subCmd)) {
                continue
            }
            Write-ColorOutput "   -> $subCmd" Yellow
            Execute-CommandString -CommandString $subCmd -ProjectRoot $ProjectRoot -UserArgs $CommandArgs
            if ($LASTEXITCODE -ne 0) {
                Write-ErgomsMessage -Key 'command_failed' -Color Red -Stderr -Param @{ name = $subCmd }
                exit $LASTEXITCODE
            }
        }
    }
    else {
        Execute-CommandString -CommandString $commandDef -ProjectRoot $ProjectRoot -UserArgs $CommandArgs
    }
}

function Execute-CommandString {
    param(
        [string]$CommandString,
        [string]$ProjectRoot,
        [string[]]$UserArgs
    )
    
    # Mark as internal so wrappers in init_terminal.ps1 pass through
    $env:ERGOMS_INTERNAL = '1'
    
    # Parse command type (poetry:, api:, media_api:, npm:, shell:, win:, linux:, lifecycle:)
    if ($CommandString -match '^(poetry|api|media_api|npm|shell|win|linux|lifecycle):(.+)$') {
        $cmdType = $matches[1]
        $cmdArgs = $matches[2].Trim()
        
        # Skip linux commands on Windows
        if ($cmdType -eq 'linux') {
            return
        }
        
        # Split command arguments and add user arguments
        $allArgs = ($cmdArgs -split '\s+') + $UserArgs
        
        switch ($cmdType) {
            'poetry' {
                Push-Location $ProjectRoot
                try {
                    & poetry @allArgs
                }
                finally {
                    Pop-Location
                }
            }
            'api' {
                $venvPath = Join-Path $ProjectRoot "virtual_env\python"
                $pythonExe = Join-Path $venvPath "Scripts\python.exe"
                if (-not (Test-Path $pythonExe)) {
                    Write-ErgomsMessage -Key 'venv_not_found' -Color Red -Stderr
                    exit 1
                }
                Push-Location (Join-Path $ProjectRoot "core\api")
                try {
                    $env:VIRTUAL_ENV = $venvPath
                    $env:PATH = "$venvPath\Scripts;$env:PATH"
                    $env:PYTHONPATH = $ProjectRoot
                    $env:PYTHONIOENCODING = "utf-8"
                    $env:PYTHONUNBUFFERED = "1"
                    $pipCache = Join-Path $ProjectRoot "virtual_env\cache\pip"
                    $poetryCache = Join-Path $ProjectRoot "virtual_env\cache\poetry"
                    $npmCache = Join-Path $ProjectRoot "virtual_env\cache\npm"
                    New-Item -ItemType Directory -Path $pipCache -Force | Out-Null
                    New-Item -ItemType Directory -Path $poetryCache -Force | Out-Null
                    New-Item -ItemType Directory -Path $npmCache -Force | Out-Null
                    $env:PIP_CACHE_DIR = $pipCache
                    $env:POETRY_CACHE_DIR = $poetryCache
                    $env:npm_config_cache = $npmCache
                    $env:NPM_CONFIG_CACHE = $npmCache
                    & $pythonExe -m commands @allArgs
                }
                finally {
                    Pop-Location
                }
            }
            'media_api' {
                $venvPath = Join-Path $ProjectRoot "virtual_env\python"
                $pythonExe = Join-Path $venvPath "Scripts\python.exe"
                if (-not (Test-Path $pythonExe)) {
                    Write-ErgomsMessage -Key 'venv_not_found' -Color Red -Stderr
                    exit 1
                }
                Push-Location $ProjectRoot
                try {
                    $env:VIRTUAL_ENV = $venvPath
                    $env:PATH = "$venvPath\Scripts;$env:PATH"
                    $env:PYTHONPATH = (Join-Path $ProjectRoot "core\media_api\src") + ";" + $ProjectRoot
                    & $pythonExe -m media_server.manage @allArgs
                }
                finally {
                    Pop-Location
                }
            }
            'npm' {
                $npmRoot = Join-Path $ProjectRoot "virtual_env\npm"
                Push-Location $npmRoot
                try {
                    if (-not (Test-Path "package.json")) {
                        Write-ErgomsMessage -Key 'cmd_error_package_json' -Color Red -Stderr
                        Write-ErgomsMessage -Key 'cmd_cwd_label' -Color Gray -Param @{ path = (Get-Location) }
                        exit 1
                    }
                    $npmCache = Join-Path $ProjectRoot "virtual_env\cache\npm"
                    New-Item -ItemType Directory -Path $npmCache -Force | Out-Null
                    $env:npm_config_cache = $npmCache
                    $env:NPM_CONFIG_CACHE = $npmCache
                    $nodeDir = Add-PortableNodejsToPath -Root $ProjectRoot
                    $npmBin = Join-Path $nodeDir "npm.cmd"
                    if (-not (Test-Path -LiteralPath $npmBin)) {
                        $npmBin = "npm"
                    }
                    if ($npmBin -eq "npm") {
                        $npmCmdLine = "npm " + ($allArgs -join ' ')
                    } else {
                        $npmCmdLine = "`"$npmBin`" " + ($allArgs -join ' ')
                    }
                    & cmd /c $npmCmdLine
                }
                finally {
                    Pop-Location
                }
            }
            'shell' {
                Push-Location $ProjectRoot
                try {
                    Add-PortableNodejsToPath -Root $ProjectRoot | Out-Null
                    $fullCommand = $cmdArgs
                    if ($UserArgs.Count -gt 0) {
                        $fullCommand += " " + ($UserArgs -join ' ')
                    }
                    & cmd /c $fullCommand
                    if ($LASTEXITCODE -ne 0) {
                        exit $LASTEXITCODE
                    }
                }
                finally {
                    Pop-Location
                }
            }
            'win' {
                Push-Location $ProjectRoot
                try {
                    Add-PortableNodejsToPath -Root $ProjectRoot | Out-Null
                    $fullCommand = $cmdArgs
                    if ($UserArgs.Count -gt 0) {
                        $fullCommand += " " + ($UserArgs -join ' ')
                    }
                    & cmd /c $fullCommand
                    if ($LASTEXITCODE -ne 0) {
                        exit $LASTEXITCODE
                    }
                }
                finally {
                    Pop-Location
                }
            }
            'lifecycle' {
                . (Join-Path $PSScriptRoot 'lifecycle.ps1')
                $recipe = $cmdArgs
                if ($UserArgs.Count -gt 0) {
                    Invoke-LifecycleRunner -Root $ProjectRoot -Recipe $recipe -ExtraArgs $UserArgs
                }
                else {
                    Invoke-LifecycleRunner -Root $ProjectRoot -Recipe $recipe
                }
            }
        }
    }
    else {
        # Execute as shell command (backward compatibility)
        Push-Location $ProjectRoot
        try {
            $allArgs = ($CommandString -split '\s+') + $UserArgs
            $mainCmd = $allArgs[0]
            $cmdArgs = $allArgs[1..($allArgs.Length-1)]
            & $mainCmd $cmdArgs
        }
        finally {
            Pop-Location
        }
    }
}

function Invoke-ModulePoetryCommand {
    param([string]$ModuleName, [string[]]$CommandArgs, [string]$Root)

    $env:ERGOMS_INTERNAL = '1'

    if ($CommandArgs.Count -eq 0) {
        Write-ErgomsMessage -Key 'poetry_usage_heading' -Color Yellow
        Write-ErgomsMessage -Key 'poetry_usage_add' -Color Yellow -Param @{ module = $ModuleName }
        Write-ErgomsMessage -Key 'poetry_usage_add_constraint' -Color Yellow -Param @{ module = $ModuleName }
        Write-ErgomsMessage -Key 'poetry_usage_remove' -Color Yellow -Param @{ module = $ModuleName }
        Write-ErgomsMessage -Key 'poetry_usage_list' -Color Yellow -Param @{ module = $ModuleName }
        return
    }

    $subCmd = $CommandArgs[0].ToLower()
    $restArgs = if ($CommandArgs.Count -gt 1) { $CommandArgs[1..($CommandArgs.Count - 1)] } else { @() }

    switch ($subCmd) {
        'add' {
            if ($restArgs.Count -eq 0) {
                Write-ErgomsMessage -Key 'poetry_error_need_package_add' -Color Red -Stderr -Param @{ module = $ModuleName }
                return
            }
            Invoke-ApiCommand -CommandArgs (@('module-add', $ModuleName) + $restArgs) -Root $Root
        }
        'remove' {
            if ($restArgs.Count -eq 0) {
                Write-ErgomsMessage -Key 'poetry_error_need_package_remove' -Color Red -Stderr -Param @{ module = $ModuleName }
                return
            }
            Invoke-ApiCommand -CommandArgs (@('module-remove', $ModuleName) + $restArgs) -Root $Root
        }
        { $_ -in 'list', 'show' } {
            Invoke-ApiCommand -CommandArgs @('module-list', $ModuleName) -Root $Root
        }
        default {
            Write-ErgomsMessage -Key 'poetry_error_unknown_subcmd' -Color Red -Stderr -Param @{ cmd = $subCmd }
            Write-ErgomsMessage -Key 'poetry_available_subcmds' -Color Yellow
        }
    }
}

function Invoke-PoetryCommand {
    param([string[]]$CommandArgs, [string]$Root)
    
    $env:ERGOMS_INTERNAL = '1'

    # Перехватываем poetry install/update: ядро + зависимости модулей.
    # Остальные poetry-подкоманды выполняются напрямую.
    if ($CommandArgs.Count -gt 0 -and $CommandArgs[0] -eq 'install') {
        $extraArgs = if ($CommandArgs.Count -gt 1) { $CommandArgs[1..($CommandArgs.Count - 1)] } else { @() }
        Invoke-ApiCommand -CommandArgs (@('install') + $extraArgs) -Root $Root
        return
    }

    if ($CommandArgs.Count -gt 0 -and $CommandArgs[0] -eq 'update') {
        $extraArgs = if ($CommandArgs.Count -gt 1) { $CommandArgs[1..($CommandArgs.Count - 1)] } else { @() }
        Invoke-ApiCommand -CommandArgs (@('update') + $extraArgs) -Root $Root
        return
    }

    if ($CommandArgs.Count -gt 0 -and $CommandArgs[0] -eq 'list') {
        Invoke-ApiCommand -CommandArgs @('module-list') -Root $Root
        return
    }
    
    $venvPath = Join-Path $Root "virtual_env\python"
    if (Test-Path $venvPath) {
        $env:VIRTUAL_ENV = $venvPath
        $env:PATH = "$venvPath\Scripts;$env:PATH"
    }
    
    Push-Location $Root
    try {
        & poetry $CommandArgs
    }
    finally {
        Pop-Location
    }
}

function Invoke-ApiCommand {
    param([string[]]$CommandArgs, [string]$Root)
    
    $env:ERGOMS_INTERNAL = '1'
    
    $venvPath = Join-Path $Root "virtual_env\python"
    
    $pythonExe = Join-Path $venvPath "Scripts\python.exe"
    if (-not (Test-Path $pythonExe)) {
        Write-ErgomsMessage -Key 'venv_not_found_at' -Color Red -Stderr -Param @{ path = $venvPath }
        Write-ErgomsMessage -Key 'venv_setup_hint' -Color Yellow -Stderr
        exit 1
    }
    
    Push-Location (Join-Path $Root "core\api")
    try {
        $env:VIRTUAL_ENV = $venvPath
        $env:PATH = "$venvPath\Scripts;$env:PATH"
        $env:PYTHONPATH = $Root
        $env:PYTHONIOENCODING = "utf-8"
        $env:PYTHONUNBUFFERED = "1"
        & $pythonExe -m commands $CommandArgs
    }
    finally {
        Pop-Location
    }
}

function Invoke-MediaApiCommand {
    param([string[]]$CommandArgs, [string]$Root)
    
    $env:ERGOMS_INTERNAL = '1'
    
    $venvPath = Join-Path $Root "virtual_env\python"
    $pythonExe = Join-Path $venvPath "Scripts\python.exe"
    if (-not (Test-Path $pythonExe)) {
        Write-ErgomsMessage -Key 'venv_not_found_at' -Color Red -Stderr -Param @{ path = $venvPath }
        Write-ErgomsMessage -Key 'venv_setup_hint' -Color Yellow -Stderr
        exit 1
    }
    
    Push-Location $Root
    try {
        $env:VIRTUAL_ENV = $venvPath
        $env:PATH = "$venvPath\Scripts;$env:PATH"
        $env:PYTHONPATH = (Join-Path $Root "core\media_api\src") + ";" + $Root
        & $pythonExe -m media_server.manage $CommandArgs
    }
    finally {
        Pop-Location
    }
}

function Invoke-NpmCommand {
    param([string[]]$CommandArgs, [string]$Root)
    
    $env:ERGOMS_INTERNAL = '1'
    
    $npmRoot = Join-Path $Root "virtual_env\npm"
    Push-Location $npmRoot
    try {
        $npmCache = Join-Path $Root "virtual_env\cache\npm"
        New-Item -ItemType Directory -Path $npmCache -Force | Out-Null
        $env:npm_config_cache = $npmCache
        $env:NPM_CONFIG_CACHE = $npmCache
        $nodeDir = Add-PortableNodejsToPath -Root $Root
        $npmBin = Join-Path $nodeDir "npm.cmd"
        if (-not (Test-Path -LiteralPath $npmBin)) {
            $npmBin = "npm"
        }
        if ($npmBin -eq "npm") {
            $npmCmdLine = "npm " + ($CommandArgs -join ' ')
        } else {
            $npmCmdLine = "`"$npmBin`" " + ($CommandArgs -join ' ')
        }
        & cmd /c $npmCmdLine
        $npmExit = $LASTEXITCODE

        if ($CommandArgs.Count -gt 0 -and $CommandArgs[0] -eq 'update' -and $npmExit -eq 0) {
            $pkgArgs = @()
            if ($CommandArgs.Count -gt 1) {
                foreach ($arg in $CommandArgs[1..($CommandArgs.Count - 1)]) {
                    if ($arg -and -not $arg.StartsWith('-')) {
                        $pkgArgs += $arg
                    }
                }
            }
            $nodeBin = Join-Path $nodeDir "node.exe"
            if (-not (Test-Path -LiteralPath $nodeBin)) {
                $nodeBin = "node"
            }
            $syncScript = Join-Path $Root "core\deployment\scripts\sync-module-npm-deps.js"
            Write-ErgomsMessage -Key 'npm_updating_modules' -Color Cyan
            & $nodeBin $syncScript '--update' '--install-missing' @pkgArgs
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        } elseif ($npmExit -ne 0) {
            exit $npmExit
        }
    }
    finally {
        Pop-Location
    }
}

# Export-ModuleMember -Function *  # Удалено, так как это не модуль
