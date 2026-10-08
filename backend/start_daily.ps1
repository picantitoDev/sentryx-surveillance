param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Falta .venv; consultar backend/README.md.'
}

$dailyVariables = @{
    SENTRIX_PREDICTOR = 'mock'
    SENTRIX_VIDEO_TRANSPORT = 'webrtc'
    SENTRIX_DETECTION_DB = (Join-Path $PSScriptRoot 'data/daily/detections.sqlite3')
    SENTRIX_LOG_DB = (Join-Path $PSScriptRoot 'data/daily/inference_logs.sqlite3')
    PYTHONUTF8 = '1'
}
$previousValues = @{}
foreach ($name in $dailyVariables.Keys) {
    $previousValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    foreach ($name in $dailyVariables.Keys) {
        [Environment]::SetEnvironmentVariable($name, $dailyVariables[$name], 'Process')
    }
    Write-Host "Daily: simulador; usa PostgreSQL si SENTRIX_DATABASE_URL esta configurada"
    Write-Host "Swagger: http://127.0.0.1:$Port/docs"
    & $pythonPath -m uvicorn app.main:app --app-dir $PSScriptRoot --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "El servidor termino con codigo $LASTEXITCODE" }
}
finally {
    foreach ($name in $dailyVariables.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousValues[$name], 'Process')
    }
}
