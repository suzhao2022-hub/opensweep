# Dev helper: merge pending strings, run the tests, then start OpenSweep from source.
$ErrorActionPreference = "Stop"
$py = "$env:LOCALAPPDATA\opensweep-dev\venv\Scripts\python.exe"
$pyw = "$env:LOCALAPPDATA\opensweep-dev\venv\Scripts\pythonw.exe"
Set-Location $PSScriptRoot
if (Test-Path _locale_update.py) { & $py _locale_update.py }
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "TESTS FAILED" -ForegroundColor Red; exit 1 }
& $py -m pyflakes src/opensweep tests
$env:PYTHONPATH = (Resolve-Path src).Path
Start-Process -FilePath $pyw -ArgumentList "-m", "opensweep"
Write-Host "OpenSweep started." -ForegroundColor Green
