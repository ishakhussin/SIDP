[CmdletBinding()]
param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$modelRoot = Join-Path $projectRoot "models"

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

function Install-UltralyticsModel {
    param(
        [Parameter(Mandatory)][string]$FileName,
        [Parameter(Mandatory)][string]$Destination
    )
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $target = Join-Path $Destination $FileName
    if (Test-Path -LiteralPath $target) {
        Write-Host "Already installed: $FileName"
        return
    }
    Write-Host "Downloading official Ultralytics model: $FileName"
    Push-Location $Destination
    try {
        & $PythonPath -c "from ultralytics import YOLO; YOLO('$FileName')"
        if ($LASTEXITCODE -ne 0) {
            throw "Ultralytics failed to download $FileName."
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $target)) {
        throw "Download finished but $target was not created."
    }
}

$restrictedDir = Join-Path $modelRoot "restricted_zone"
$proximityDir = Join-Path $modelRoot "unsafe_proximity"
$ppeDir = Join-Path $modelRoot "ppe"

Install-UltralyticsModel "yolo11n-pose.pt" $restrictedDir
Install-UltralyticsModel "yolo11n.pt" $proximityDir
Install-UltralyticsModel "yolov8n.pt" $ppeDir

$depthWeights = Join-Path $proximityDir "model.safetensors"
if (Test-Path -LiteralPath $depthWeights) {
    Write-Host "Already installed: DepthPro"
} else {
    Write-Host "Downloading official DepthPro model from Hugging Face..."
    New-Item -ItemType Directory -Force -Path $proximityDir | Out-Null
    & $PythonPath -c "from huggingface_hub import snapshot_download; import sys; snapshot_download(repo_id='apple/DepthPro-hf', local_dir=sys.argv[1])" $proximityDir
    if ($LASTEXITCODE -ne 0) {
        throw "DepthPro download failed. Check the network connection and try again."
    }
}

$ppeClassifier = Join-Path $ppeDir "ppe_multilabel_best.pt"
if (Test-Path -LiteralPath $ppeClassifier) {
    Write-Host "Already installed: custom PPE classifier"
} elseif ($env:SENTRYLAB_PPE_MODEL_URL) {
    Write-Host "Downloading the custom PPE classifier..."
    New-Item -ItemType Directory -Force -Path $ppeDir | Out-Null
    $partial = "$ppeClassifier.download"
    $request = @{
        Uri = $env:SENTRYLAB_PPE_MODEL_URL
        OutFile = $partial
    }
    if ($env:SENTRYLAB_MODEL_TOKEN) {
        $request.Headers = @{ Authorization = "Bearer $($env:SENTRYLAB_MODEL_TOKEN)" }
    }
    try {
        Invoke-WebRequest @request
        Move-Item -LiteralPath $partial -Destination $ppeClassifier -Force
    } finally {
        if (Test-Path -LiteralPath $partial) {
            Remove-Item -LiteralPath $partial -Force
        }
    }
} else {
    Write-Warning "Custom PPE weights were not downloaded. Set SENTRYLAB_PPE_MODEL_URL and run this script again."
}

$required = @(
    (Join-Path $restrictedDir "yolo11n-pose.pt"),
    (Join-Path $proximityDir "yolo11n.pt"),
    (Join-Path $proximityDir "config.json"),
    (Join-Path $proximityDir "preprocessor_config.json"),
    (Join-Path $proximityDir "model.safetensors"),
    (Join-Path $ppeDir "yolov8n.pt"),
    $ppeClassifier
)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -gt 0) {
    Write-Warning "Model setup is incomplete. Missing:"
    $missing | ForEach-Object { Write-Warning "  $_" }
    exit 2
}

Write-Host "All SentryLab AI models are installed."
