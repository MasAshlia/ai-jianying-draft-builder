Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Virtual environment not found. Follow README to create .venv first."
}

& $pythonExe -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pythonExe -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "AIDraftBuilder.spec")
exit $LASTEXITCODE
