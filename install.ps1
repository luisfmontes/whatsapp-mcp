#requires -Version 5.1
<#
    WhatsApp MCP - Windows installer (counterpart of install.sh for macOS/Linux).

        powershell -ExecutionPolicy Bypass -File install.ps1 [-Service] [-Codex]
                                          [-InstallDir <path>] [-BridgePort <n>]

    This file is deliberately ASCII-only. Windows PowerShell 5.1 decodes a
    BOM-less file as ANSI, so any non-ASCII glyph (a check mark, a box-drawing
    rule) becomes stray bytes - including 0x93, which is an opening quote in
    CP-1252 - and the whole script then fails to parse with a misleading
    "missing closing '}'". Keeping the source ASCII means no encoding, editor or
    git setting can break it. Do not "prettify" the output with Unicode glyphs.
#>
param(
    [switch]$Service,
    [switch]$Codex,
    [string]$InstallDir,
    # 8080 is the project default, but Windows commonly reserves it (Hyper-V /
    # WSL exclusions), so the default here is 8081 and the chosen port is
    # validated against the exclusion list before anything else happens.
    [int]$BridgePort = 8081,
    # Which repository to install from. Windows support lives on a fork/branch
    # until it is merged upstream, and a local path works too (useful for
    # testing this script against a working copy).
    [string]$RepoUrl = 'https://github.com/rodrigopg/whatsapp-mcp.git',
    [string]$Branch
)

$ErrorActionPreference = 'Stop'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.whatsapp-mcp'
}
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
function Write-Info {
    param([string]$Message)
    Write-Host "  > $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [ok] $Message" -ForegroundColor Green
}

function Write-Warn2 {
    param([string]$Message)
    Write-Host "  [!] $Message" -ForegroundColor Yellow
}

function Exit-WithError {
    param([string]$Message)
    Write-Host "  [x] $Message" -ForegroundColor Red
    exit 1
}

# Writes UTF-8 WITHOUT a BOM. Set-Content -Encoding UTF8 on PowerShell 5.1 adds
# one, and a BOM in front of JSON makes several MCP clients fail to parse their
# own config file.
function Write-TextNoBom {
    param([string]$Path, [string]$Content)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# Windows reserves TCP ranges for Hyper-V/WSL/NAT. Binding inside one fails with
# "an attempt was made to access a socket in a way forbidden by its access
# permissions" - and the bridge only logs that and keeps running, so the service
# looks healthy while no port is open. Catch it here instead.
function Test-PortExcluded {
    param([int]$Port)
    $out = & netsh interface ipv4 show excludedportrange protocol=tcp 2>$null
    foreach ($line in $out) {
        if ($line -match '^\s*(\d+)\s+(\d+)') {
            $rangeStart = [int]$matches[1]
            $rangeEnd = [int]$matches[2]
            if ($Port -ge $rangeStart -and $Port -le $rangeEnd) {
                return $true
            }
        }
    }
    return $false
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host "    WhatsApp MCP  -  Installer" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------
if (Test-PortExcluded -Port $BridgePort) {
    $altPort = 0
    foreach ($candidate in 8081, 8082, 8090, 9080, 9081) {
        if (-not (Test-PortExcluded -Port $candidate)) {
            $altPort = $candidate
            break
        }
    }
    if ($altPort -eq 0) {
        Exit-WithError "Port $BridgePort is inside a Windows reserved range and no fallback was free. Run 'netsh interface ipv4 show excludedportrange protocol=tcp' and pass -BridgePort with a port outside those ranges."
    }
    Write-Warn2 "Port $BridgePort is inside a Windows reserved range (Hyper-V/WSL); the bridge could not bind to it. Using $altPort instead."
    $BridgePort = $altPort
}
Write-Ok "Bridge port: $BridgePort"

# ---------------------------------------------------------------------------
# Dependencies. None are installed automatically - the script reports how.
# ---------------------------------------------------------------------------
Write-Info "Checking dependencies..."

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Exit-WithError "Required: 'git' not found. Install with: winget install --id Git.Git"
}
Write-Ok "git found"

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    Exit-WithError "Required: 'go' not found. Install with: winget install --id GoLang.Go"
}
$goVersionOutput = & go version
if (-not ($goVersionOutput -match 'go(\d+)\.(\d+)')) {
    Exit-WithError "Could not parse Go version from: $goVersionOutput"
}
$goMajor = [int]$matches[1]
$goMinor = [int]$matches[2]
if (($goMajor -lt 1) -or ($goMajor -eq 1 -and $goMinor -lt 25)) {
    Exit-WithError "Go 1.25+ required (found $goMajor.$goMinor). Update from https://go.dev/dl/"
}
Write-Ok "Go $goMajor.$goMinor"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $pythonCmd) {
    Exit-WithError "Required: 'python' not found. Install with: winget install --id Python.Python.3.13"
}
Write-Ok ("Python " + (& $pythonCmd.Source --version))

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Exit-WithError "Required: 'uv' not found. Install with: winget install --id astral-sh.uv (or see https://docs.astral.sh/uv/getting-started/installation/), then re-run this script."
}
Write-Ok ("uv " + (& uv --version))

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Ok "ffmpeg found (audio conversion enabled)"
} else {
    Write-Warn2 "ffmpeg not found - sending audio messages and the local whisper engine stay disabled. Install with: winget install --id Gyan.FFmpeg (open a new shell afterwards so PATH refreshes), or set FFMPEG_BIN to its full path."
}

# ---------------------------------------------------------------------------
# Clone or update
# ---------------------------------------------------------------------------
Write-Host ""
if (Test-Path (Join-Path $InstallDir '.git')) {
    Write-Info "Updating existing install at $InstallDir ..."
    & git -C $InstallDir pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "git pull failed in $InstallDir - resolve it manually and re-run."
    }
} else {
    Write-Info "Cloning $RepoUrl into $InstallDir ..."
    if ($Branch) {
        & git clone --branch $Branch $RepoUrl $InstallDir
    } else {
        & git clone $RepoUrl $InstallDir
    }
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "git clone failed."
    }
}
Write-Ok "Repository ready"

# ---------------------------------------------------------------------------
# Build the bridge. No C toolchain: on Windows the SQLite driver is pure Go.
# ---------------------------------------------------------------------------
Write-Info "Building Go bridge..."
Push-Location (Join-Path $InstallDir 'whatsapp-bridge')
try {
    $env:CGO_ENABLED = '0'
    & go build -o whatsapp-bridge.exe .
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "Bridge build failed."
    }
    Write-Ok "Bridge compiled -> $InstallDir\whatsapp-bridge\whatsapp-bridge.exe (no C toolchain required)"
}
finally {
    Pop-Location
    Remove-Item env:CGO_ENABLED -ErrorAction SilentlyContinue
}

Write-Info "Installing Python dependencies..."
Push-Location (Join-Path $InstallDir 'whatsapp-mcp-server')
try {
    & uv sync --quiet
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "uv sync failed."
    }
    Write-Ok "Python environment ready"
}
finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Launcher (the Windows start-bridge.sh)
# ---------------------------------------------------------------------------
$launchScript = Join-Path $InstallDir 'start-bridge.ps1'
$launchScriptContent = @"
# Starts the WhatsApp MCP bridge. Generated by install.ps1 - the Windows
# counterpart of start-bridge.sh. ASCII-only on purpose (see install.ps1).

`$BridgeDir = '$InstallDir\whatsapp-bridge'
`$BridgeLog = '$InstallDir\bridge.log'
`$EnvFile   = '$InstallDir\transcription.env'

Set-Location `$BridgeDir

# Transcription is opt-in. Task Scheduler does NOT inherit the environment of an
# interactive shell, so the engine variables have to be loaded here for the
# auto-start to see them - the same reason start-bridge.sh sources this file.
# Create transcription.env next to this script with KEY=VALUE lines
# (TRANSCRIPTION_ENGINE, TRANSCRIPTION_API_KEY, WHISPER_CLI/WHISPER_MODEL, ...).
if (Test-Path `$EnvFile) {
    foreach (`$line in Get-Content `$EnvFile) {
        `$entry = `$line.Trim()
        if (`$entry -and -not `$entry.StartsWith('#')) {
            `$kv = `$entry -split '=', 2
            if (`$kv.Count -eq 2) {
                [Environment]::SetEnvironmentVariable(`$kv[0].Trim(), `$kv[1].Trim().Trim('"'), 'Process')
            }
        }
    }
}

`$env:WHATSAPP_BRIDGE_PORT = '$BridgePort'
# recover_audios.py parses the bridge log for media-retry results, so point it
# at the same file this launcher writes.
`$env:WHATSAPP_BRIDGE_LOG = `$BridgeLog

# Append stdout AND stderr. Pumping the two pipes by hand (read stdout to EOF,
# then stderr) deadlocks a long-running process as soon as the unread pipe fills
# its buffer - the bridge would freeze mid-sync with nothing explaining why.
& '.\whatsapp-bridge.exe' *>> `$BridgeLog
"@
Set-Content -Path $launchScript -Value $launchScriptContent -Encoding Ascii
Write-Ok "Launch script: $launchScript"

# ---------------------------------------------------------------------------
# MCP client configuration
# ---------------------------------------------------------------------------
Write-Host ""
Write-Info "Configuring MCP clients..."

$uvPath = (Get-Command uv).Source
$mcpServerPath = Join-Path $InstallDir 'whatsapp-mcp-server'

$mcpJson = @{
    mcpServers = @{
        whatsapp = @{
            command = $uvPath
            args = @('--directory', $mcpServerPath, 'run', 'main.py')
            env = @{
                WHATSAPP_BRIDGE_PORT = "$BridgePort"
            }
        }
    }
} | ConvertTo-Json -Depth 5

$configured = $false

# Claude Desktop. Same rule as install.sh: never overwrite an existing config -
# print it and let the user merge.
$claudeConfigDir = Join-Path $env:APPDATA 'Claude'
if (Test-Path $claudeConfigDir) {
    $claudeConfigFile = Join-Path $claudeConfigDir 'claude_desktop_config.json'
    if (Test-Path $claudeConfigFile) {
        Write-Warn2 "Claude Desktop config already exists at $claudeConfigFile - merge this in manually:"
        Write-Host ""
        Write-Host $mcpJson
        Write-Host ""
    } else {
        Write-TextNoBom -Path $claudeConfigFile -Content $mcpJson
        Write-Ok "Claude Desktop config written to $claudeConfigFile"
        $configured = $true
    }
} else {
    Write-Warn2 "Claude Desktop config dir not found at $claudeConfigDir (install Claude Desktop first)"
}

# Cursor
$cursorConfigDir = Join-Path $env:USERPROFILE '.cursor'
if (Test-Path $cursorConfigDir) {
    $cursorConfigFile = Join-Path $cursorConfigDir 'mcp.json'
    if (Test-Path $cursorConfigFile) {
        Write-Warn2 "Cursor config already exists at $cursorConfigFile - merge manually if needed."
    } else {
        Write-TextNoBom -Path $cursorConfigFile -Content $mcpJson
        Write-Ok "Cursor config written to $cursorConfigFile"
        $configured = $true
    }
}

# ---------------------------------------------------------------------------
# Auto-start via Task Scheduler (-Service)
# ---------------------------------------------------------------------------
if ($Service) {
    Write-Host ""
    $taskName = 'WhatsAppMCPBridge'

    $bridgeRunning = $false
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:$BridgePort/qr" -TimeoutSec 2 -UseBasicParsing
        $bridgeRunning = $true
    }
    catch {
        if (Get-Process -Name 'whatsapp-bridge' -ErrorAction SilentlyContinue) {
            $bridgeRunning = $true
        }
    }

    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

    if ($existingTask) {
        Write-Warn2 "Scheduled task '$taskName' already exists - leaving it alone. Remove it with Unregister-ScheduledTask if you want it recreated."
    } elseif ($bridgeRunning) {
        Write-Warn2 "A bridge is already running (existing install?) - skipping auto-start setup so two instances do not fight over the same store."
    } else {
        Write-Info "Registering auto-start at logon..."
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launchScript`""
        # ExecutionTimeLimit Zero = no limit: the default kills a task after 3
        # days, which for a long-running bridge means a silent death mid-week.
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval ([TimeSpan]::FromMinutes(1))
        # -User + -RunLevel Limited: runs unelevated inside the logged-on
        # session, so no admin rights and no stored password are needed.
        Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Settings $settings -User $env:USERNAME -RunLevel Limited | Out-Null
        Write-Ok "Auto-start registered as scheduled task '$taskName' (logs: $InstallDir\bridge.log)"
    }
}

# ---------------------------------------------------------------------------
# Codex CLI (-Codex)
# ---------------------------------------------------------------------------
if ($Codex) {
    Write-Host ""
    $codexDir = Join-Path $env:USERPROFILE '.codex'
    $codexFile = Join-Path $codexDir 'config.toml'
    $snippet = @"
[mcp_servers.whatsapp]
command = "$uvPath"
args = ["--directory", "$mcpServerPath", "run", "main.py"]
env = { WHATSAPP_BRIDGE_PORT = "$BridgePort" }
"@

    if (-not (Test-Path $codexDir)) {
        Write-Warn2 "Codex CLI not found ($codexDir missing) - add this to its config.toml manually:"
        Write-Host ""
        Write-Host $snippet
        Write-Host ""
    } elseif (-not (Test-Path $codexFile)) {
        Write-TextNoBom -Path $codexFile -Content $snippet
        Write-Ok "Codex config created: $codexFile"
    } else {
        $content = Get-Content -Path $codexFile -Raw
        if ($content -match 'mcp_servers') {
            # Any mcp_servers form (table, dotted key, inline table): appending a
            # second [mcp_servers.whatsapp] table could corrupt the TOML.
            Write-Warn2 "mcp_servers already configured in $codexFile - not touching it. Merge manually:"
            Write-Host ""
            Write-Host $snippet
            Write-Host ""
        } else {
            Add-Content -Path $codexFile -Value "`r`n$snippet"
            Write-Ok "Codex config updated: $codexFile"
        }
    }
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host "  Installation complete" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:"
Write-Host ""
Write-Host "  1. Start the bridge:"
Write-Host "     powershell -ExecutionPolicy Bypass -File $launchScript" -ForegroundColor Cyan
Write-Host ""
Write-Host "  2. Open this in your browser and scan the QR code with your phone:"
Write-Host "     http://localhost:$BridgePort/qr" -ForegroundColor Cyan
Write-Host ""
Write-Host "  3. Restart Claude Desktop / Cursor / Claude Code"
Write-Host ""
if (-not $configured) {
    Write-Warn2 "Could not auto-configure an MCP client. Add this to claude_desktop_config.json or .cursor\mcp.json:"
    Write-Host ""
    Write-Host $mcpJson
    Write-Host ""
}
