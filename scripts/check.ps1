param([string]$Python = ".venv\Scripts\python.exe")
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
if (-not [IO.Path]::IsPathRooted($Python)) { $Python = Join-Path $repo $Python }
$oldMode = $env:INFERENCE_MODE
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$checkTemp = Join-Path $repo "work\checks"
New-Item -ItemType Directory -Force -Path $checkTemp | Out-Null
function Invoke-Check([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Check failed (exit $LASTEXITCODE)." }
}
Push-Location $repo
try {
    $env:INFERENCE_MODE = "demo"
    $env:TEMP = $checkTemp
    $env:TMP = $checkTemp
    Invoke-Check { & $Python backend/manage.py check }
    Invoke-Check { & $Python backend/manage.py makemigrations --check --dry-run }
    Push-Location backend
    try { Invoke-Check { & $Python manage.py test monitor --noinput } }
    finally { Pop-Location }
    Invoke-Check { & $Python scripts/check_demo_web.py }
    Invoke-Check { & npm.cmd --cache "$repo\work\npm-cache" --prefix frontend ci }
    Invoke-Check { & npm.cmd --prefix frontend run check }
    Invoke-Check { & npm.cmd --prefix frontend run build }
    Invoke-Check { & git -c "safe.directory=$repo" diff --exit-code -- frontend/static/js/app.js }
} finally {
    $env:INFERENCE_MODE = $oldMode
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    Pop-Location
}
