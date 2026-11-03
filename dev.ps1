param(
    [ValidateSet('check', 'api', 'chat', 'test', 'setup', 'demo', 'eval', 'archive-demo')]
    [string]$Mode = 'check'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Project virtual environment was not found.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Write-Host "=== Personal Emotional Agent | $Mode ===" -ForegroundColor Cyan

switch ($Mode) {
    'check' {
        & $pythonExecutable -B -m app.cli --check
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host 'Commands: .\dev.ps1 -Mode api / chat / test / setup / demo / eval / archive-demo'
    }
    'api' {
        & $pythonExecutable -B -m app.cli --check
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host 'API docs: http://127.0.0.1:8000/docs'
        & $pythonExecutable -B -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
    }
    'chat' {
        & $pythonExecutable -B -m app.cli
    }
    'test' {
        & $pythonExecutable -B -m pytest -v -p no:cacheprovider
    }
    'demo' {
        & $pythonExecutable -B -m app.cli --demo
    }
    'archive-demo' {
        & $pythonExecutable -B -m app.cli --archive-demo
    }
    'eval' {
        & $pythonExecutable -B -m app.evaluation
    }
    'setup' {
        & $pythonExecutable -B -m app.cli --configure
    }
}
exit $LASTEXITCODE
