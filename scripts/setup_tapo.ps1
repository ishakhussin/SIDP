[CmdletBinding()]
param(
    [string]$CameraAddress = "",
    [string]$Username = "",
    [ValidateSet(1, 2)][int]$Stream = 1,
    [string]$PythonPath = "",
    [switch]$TestOnly,
    [switch]$SkipTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $PythonPath = $venvPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Create and activate .venv, or pass -PythonPath."
        }
        $PythonPath = $pythonCommand.Source
    }
}

if (-not $env:SENTRYLAB_CAM01_RTSP_URL) {
    if (-not $CameraAddress) {
        $CameraAddress = Read-Host "Tapo camera IP address (example: 192.168.0.120)"
    }
    if (-not $Username) {
        $Username = Read-Host "Tapo Camera Account username"
    }
    if (-not $CameraAddress.Trim() -or -not $Username.Trim()) {
        throw "The camera IP address and Camera Account username are required."
    }

    $securePassword = Read-Host "Tapo Camera Account password" -AsSecureString
    $plainPassword = [System.Net.NetworkCredential]::new("", $securePassword).Password
    if (-not $plainPassword) {
        throw "The Camera Account password is required."
    }
    try {
        $encodedUsername = [Uri]::EscapeDataString($Username.Trim())
        $encodedPassword = [Uri]::EscapeDataString($plainPassword)
        $env:SENTRYLAB_CAM01_RTSP_URL = "rtsp://${encodedUsername}:${encodedPassword}@${CameraAddress}:554/stream${Stream}"
    } finally {
        $plainPassword = $null
        $securePassword.Dispose()
    }
}

# TCP is generally more reliable than UDP for a local monitoring application.
$env:OPENCV_FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp"

if (-not $SkipTest) {
    Write-Host "Testing CAM 01 without displaying or logging its RTSP credentials..."
    & $PythonPath (Join-Path $PSScriptRoot "test_tapo.py")
    if ($LASTEXITCODE -ne 0) {
        throw "CAM 01 did not return a frame. Check the IP address, Camera Account, Wi-Fi, and stream selection."
    }
}

if ($TestOnly) {
    Write-Host "Tapo setup test completed. No server was started."
    exit 0
}

Write-Host "Starting SentryLab with CAM 01 configured for this PowerShell session..."
Push-Location $projectRoot
try {
    & $PythonPath "app.py"
} finally {
    Pop-Location
}
