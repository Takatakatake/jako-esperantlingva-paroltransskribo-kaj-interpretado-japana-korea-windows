<#
.SYNOPSIS
  Beginner-friendly launcher for Windows users.
.NOTES
  Activates .venv311 when present, then runs the easy-start CLI.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemainingArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location $repoRoot

$venvDir = Join-Path $repoRoot ".venv311"
$venvScripts = Join-Path $venvDir "Scripts"
$venvActivate = Join-Path $venvScripts "Activate.ps1"
$venvPython = Join-Path $venvScripts "python.exe"
$bootstrapMarker = Join-Path $venvDir ".easy_start_bootstrap"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$pythonBaseArgs = @()

if (-not $pythonCmd) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        Write-Warning "Python was not found; falling back to 'py -3'."
        $pythonCmd = $launcher
        $pythonBaseArgs = @("-3")
    }
}

if (-not $pythonCmd) {
    Write-Error "Python was not found. Install it from python.org and add it to PATH."
    exit 127
}

$pythonPath = $pythonCmd.Source
if (-not $pythonPath) {
    $pythonPath = $pythonCmd.Path
}

function Invoke-HostPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )
    & $pythonPath @pythonBaseArgs @Arguments
    return $LASTEXITCODE
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment (.venv311)..."
    $createExit = Invoke-HostPython @("-m", "venv", ".venv311")
    if ($createExit -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Error "Failed to create .venv311. Please install Python 3.10+ and try again."
        exit $createExit
    }
}

if (Test-Path $venvActivate) {
    . $venvActivate
} else {
    Write-Warning "Virtual environment activation script not found at $venvActivate."
}

if (Test-Path $venvPython) {
    $pythonPath = $venvPython
    $pythonBaseArgs = @()
}

$requirementsPath = Join-Path $repoRoot "requirements.txt"
$requirementsHash = $null
if (Test-Path $requirementsPath) {
    try {
        $requirementsHash = (Get-FileHash -Algorithm SHA256 -Path $requirementsPath).Hash
    } catch {
        Write-Warning "Could not compute hash for requirements.txt: $($_.Exception.Message)"
    }
}

$bootstrapNeeded = $false
if (-not (Test-Path $bootstrapMarker)) {
    $bootstrapNeeded = $true
} elseif ($requirementsHash) {
    try {
        $existingHash = (Get-Content -Path $bootstrapMarker -ErrorAction Stop | Select-Object -First 1)
        if ($existingHash -ne $requirementsHash) {
            $bootstrapNeeded = $true
        }
    } catch {
        $bootstrapNeeded = $true
    }
}

if ($bootstrapNeeded -and (Test-Path $venvPython) -and $requirementsHash) {
    Write-Host "Installing Python dependencies from requirements.txt (first run may take a while)..."
    & $venvPython "-m" "pip" "install" "--upgrade" "pip"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pip upgrade failed; continuing with existing version."
    }
    & $venvPython "-m" "pip" "install" "-r" $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dependency installation failed. Please review the errors above."
        exit $LASTEXITCODE
    }
    try {
        Set-Content -Path $bootstrapMarker -Value $requirementsHash -Encoding ASCII -Force
    } catch {
        Write-Warning "Could not write bootstrap marker: $($_.Exception.Message)"
    }
}

$pythonArgs = @("-m", "transcriber.cli", "--easy-start") + $RemainingArgs
Write-Host ("Using interpreter: {0}" -f $pythonPath)

& $pythonPath @pythonArgs
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Warning ("easy_start exited with code {0}. Review the output above or the logs directory." -f $exitCode)
    exit $exitCode
}
