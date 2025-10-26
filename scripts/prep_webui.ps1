<#
.SYNOPSIS
    PowerShell analogue of prep_webui.sh.

.DESCRIPTION
    Stops lingering transcription CLI processes, frees the Web UI port,
    and waits until the port reports free before handing control back.
#>
[CmdletBinding()]
param()

$Port = if ($env:PORT) { [int]$env:PORT } else { 8765 }
$CliPattern = if ($env:CLI_PATTERN) { $env:CLI_PATTERN } else { "python -m transcriber.cli" }
$WaitLoops = 25
$WaitSleepMilliseconds = 200

function Write-PrepLog {
    param([string]$Message)
    Write-Host "[prep_webui] $Message"
}

function Stop-ProcessesByPattern {
    param([string]$Pattern)

    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Pattern*" }

    if (-not $processes) {
        return
    }

    Write-PrepLog "Terminating processes matching '$Pattern'"
    foreach ($proc in $processes) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "[prep_webui] Failed to stop PID $($proc.ProcessId) ($_)" 
        }
    }
    Start-Sleep -Milliseconds $WaitSleepMilliseconds
}

function Close-PortListeners {
    param([int]$ListenPort)

    $connections = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        return
    }

    $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    if ($pids.Count -eq 0) {
        return
    }

    Write-PrepLog "Closing listeners on port $ListenPort: $($pids -join ', ')"
    foreach ($pid in $pids) {
        try {
            Stop-Process -Id $pid -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "[prep_webui] Failed to stop process $pid ($_)" 
        }
    }
}

function Wait-UntilPortFree {
    param([int]$ListenPort)

    foreach ($i in 1..$WaitLoops) {
        $stillListening = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
        if (-not $stillListening) {
            Write-PrepLog "Port $ListenPort is free."
            return
        }
        Start-Sleep -Milliseconds $WaitSleepMilliseconds
    }

    Write-Warning "[prep_webui] Port $ListenPort still appears busy; inspect running processes."
}

Stop-ProcessesByPattern -Pattern $CliPattern
Close-PortListeners -ListenPort $Port
Wait-UntilPortFree -ListenPort $Port
