param(
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Falta .venv en la raiz del repo. Ejecutar: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r backend/requirements-gpu.txt'
}
$env:SENTRIX_PREDICTOR = 'videomae'
$env:SENTRIX_PROVIDER = 'cuda'
$env:SENTRIX_VIDEO_TRANSPORT = 'rtsp'
& $pythonPath (Join-Path $PSScriptRoot 'setup_postgres.py') --ensure-running
if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar o encontrar PostgreSQL; revisar el mensaje anterior.' }
Push-Location $PSScriptRoot
try {
    & $pythonPath (Join-Path $PSScriptRoot 'run_server.py') --host $BindHost --port 8000 --also-loopback
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) { throw "El backend finalizo con codigo $LASTEXITCODE" }
