<#
.SYNOPSIS
    PowerShell port of run_transcriber.sh for Windows.

.DESCRIPTION
    Ensures the Web UI port is free, then launches the transcription pipeline.
    Reads PORT/LOG_LEVEL/BACKEND from env vars (with defaults) so usage matches
    the original Bash helper.
    Run from a shell where the project virtual environment is active.
#>
[CmdletBinding()]
param()

$Port = if ($env:PORT) { [int]$env:PORT } else { 8765 }
$LogLevel = if ($env:LOG_LEVEL) { $env:LOG_LEVEL } else { "INFO" }
$Backend = if ($env:BACKEND) { $env:BACKEND } else { "speechmatics" }

function Stop-PortListener {
    param([int]$ListenPort)

    $connections = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        return
    }

    $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    if ($pids.Count -gt 0) {
        Write-Host "[run_transcriber] Closing listeners on port $ListenPort: $($pids -join ', ')"
        foreach ($pid in $pids) {
            try {
                Stop-Process -Id $pid -ErrorAction SilentlyContinue
            } catch {
                Write-Warning "[run_transcriber] Failed to stop process $pid ($_)" 
            }
        }
    }

    foreach ($i in 1..20) {
        $stillListening = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
        if (-not $stillListening) {
            return
        }
        Start-Sleep -Milliseconds 200
    }

    Write-Warning "[run_transcriber] Port $ListenPort still busy after waiting."
}

Stop-PortListener -ListenPort $Port

Write-Host "[run_transcriber] Starting pipeline on port $Port with backend=$Backend"

python -m transcriber.cli --backend "$Backend" --log-level "$LogLevel"
