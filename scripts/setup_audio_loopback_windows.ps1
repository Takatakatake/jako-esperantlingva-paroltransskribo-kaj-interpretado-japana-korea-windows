<#
.SYNOPSIS
  Inspect loopback-capable audio devices on Windows and provide guidance.
.NOTES
  This script is non-destructive: it only reports detected devices and suggests
  configuration steps (Stereo Mix, WASAPI loopback, or virtual cables).
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    Write-Host "[setup_audio_loopback] $Message"
}

Write-Log "Inspecting Windows loopback-capable audio devices..."

$loopbackTerms = @(
    'Stereo Mix',
    'Loopback',
    'CABLE Output',
    'CABLE Input',
    'VB-Audio',
    'Virtual Audio'
)

$endpoints = @()
try {
    $endpoints = @(Get-PnpDevice -Class AudioEndpoint -Status OK | Sort-Object -Property FriendlyName)
} catch {
    Write-Log "Unable to list devices via Get-PnpDevice: $($_.Exception.Message)"
    $endpoints = @()
}

if ($endpoints.Count -gt 0) {
    Write-Log "Audio endpoints:"
    $endpoints | ForEach-Object {
        Write-Host ("  - {0} ({1})" -f $_.FriendlyName, $_.InstanceId)
    }

    $candidates = @($endpoints | Where-Object {
        $name = $_.FriendlyName
        foreach ($term in $loopbackTerms) {
            if ($name -like "*$term*") { return $true }
        }
        return $false
    })

    if ($candidates.Count -gt 0) {
        Write-Log "Potential loopback devices:"
        $candidates | ForEach-Object {
            Write-Host ("  * {0}" -f $_.FriendlyName)
        }
    } else {
        Write-Log "No obvious loopback endpoints detected. Recommended next steps:"
        Write-Host "  1. Enable 'Stereo Mix' from the Sound control panel (Recording tab)."
        Write-Host "  2. Install a virtual audio cable that offers WASAPI loopback (VB-Audio, VoiceMeeter, etc.)."
        Write-Host "  3. Update or reinstall your audio drivers."
        if (-not $env:CI) {
            Write-Log "Opening the Recording tab (mmsys.cpl) for convenience..."
            try {
                Start-Process -FilePath "control.exe" -ArgumentList "mmsys.cpl,,1" | Out-Null
            } catch {
                Write-Log "Failed to launch control panel: $($_.Exception.Message)"
            }
        }
    }
} else {
    Write-Log "AudioEndpoint class returned no devices. Checking Win32_SoundDevice..."
    try {
        $soundDevices = @(Get-CimInstance -ClassName Win32_SoundDevice | Sort-Object -Property Name)
        if ($soundDevices.Count -eq 0) {
            Write-Log "No sound devices detected. Verify drivers are installed correctly."
        } else {
            $soundDevices | ForEach-Object {
                Write-Host ("  - {0}" -f $_.Name)
            }
        }
    } catch {
        Write-Log "Failed to query Win32_SoundDevice: $($_.Exception.Message)"
    }
}

Write-Log "Adjust loopback routing as needed via 'mmsys.cpl'."
Write-Log "Finished."
exit 0
