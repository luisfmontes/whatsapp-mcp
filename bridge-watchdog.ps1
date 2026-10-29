# Starts one bridge account and watches that it actually comes up. The Task
# Scheduler task runs this (through start-bridge-hidden.vbs) instead of the
# account's start-bridge.ps1.
#
# Why it exists (issue #48): the .vbs returns as soon as it has spawned
# PowerShell, so the task reports success at once and its RestartCount never
# engages. After an abnormal shutdown, a launcher was seen alive for minutes
# without ever starting whatsapp-bridge.exe, and nothing noticed. This script
# owns the launcher's PID from the moment it starts it, so it can tell "stuck"
# from "slow" and restart only what it started - never by process name or port.
#
# ASCII-only on purpose, like install.ps1: Windows PowerShell 5.1 runs it.

param(
    [Parameter(Mandatory = $true)][string]$Launcher,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$Log,
    # A healthy start binds the port in a few seconds; the slowest seen after a
    # boot was under 40 s.
    [int]$GraceSeconds = 90,
    [int]$MaxAttempts = 3
)

# Its own log rather than bridge.log: the running launcher holds bridge.log
# open, and appending to it from here could fail exactly when it matters.
function Write-WatchdogLog {
    param([string]$Message)
    try {
        Add-Content -Path $Log -Value ("{0} watchdog[{1}] {2}" -f (Get-Date -Format s), $Port, $Message) -Encoding Ascii
    }
    catch { }
}

function Test-PortListening {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.ConnectAsync('127.0.0.1', $Port)
        return ($connect.Wait(1000) -and $client.Connected)
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Get-BridgeChild {
    param([int]$ParentId)
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentId AND Name = 'whatsapp-bridge.exe'" -ErrorAction SilentlyContinue
}

if (Test-PortListening) {
    Write-WatchdogLog "port already listening, nothing to start"
    exit 0
}

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    # -NoNewWindow keeps the launcher in this hidden console. -WindowStyle
    # Hidden would not: Windows Terminal ignores it (see install.ps1).
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$Launcher`"") -NoNewWindow -PassThru
    Write-WatchdogLog "attempt $attempt/$MaxAttempts started launcher pid $($proc.Id)"

    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening) {
            Write-WatchdogLog "port up (launcher pid $($proc.Id))"
            exit 0
        }
        if ($proc.HasExited) { break }
        Start-Sleep -Seconds 2
    }

    if ($proc.HasExited) {
        Write-WatchdogLog "launcher pid $($proc.Id) exited with code $($proc.ExitCode) before the port came up"
        continue
    }

    # A bridge that exists but has not bound yet is slow, not stuck. Killing
    # its launcher would orphan it, and a second bridge on the same store and
    # port fails to bind while staying alive.
    $child = Get-BridgeChild -ParentId $proc.Id
    if ($child) {
        Write-WatchdogLog "port still down after $GraceSeconds s but bridge pid $($child.ProcessId) is running; leaving it"
        exit 0
    }

    Write-WatchdogLog "launcher pid $($proc.Id) stuck for $GraceSeconds s without starting the bridge; stopping it"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    $child = Get-BridgeChild -ParentId $proc.Id
    if ($child) {
        Write-WatchdogLog "bridge pid $($child.ProcessId) started while stopping its launcher; leaving it"
        exit 0
    }
}

Write-WatchdogLog "gave up after $MaxAttempts attempts; port $Port is not listening"
exit 1
