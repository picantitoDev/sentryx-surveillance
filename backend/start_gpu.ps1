$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Falta .venv; consultar backend/README.md para instalar.'
}
$env:SENTRIX_PREDICTOR = 'videomae'
$env:SENTRIX_PROVIDER = 'cuda'
$env:SENTRIX_VIDEO_TRANSPORT = 'livekit'
& $pythonPath -m uvicorn app.main:app --app-dir $PSScriptRoot --host 0.0.0.0 --port 8000
if ($LASTEXITCODE -ne 0) { throw "El backend terminó con código $LASTEXITCODE" }
