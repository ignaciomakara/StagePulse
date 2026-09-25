param(
    [string]$HostAddress = '127.0.0.1',
    [int]$Port = 8000,
    [string]$Config = 'config/stages.gate3.json'
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$envFile = Join-Path $projectRoot '.env'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment not found at $python. Create .venv and install requirements.txt first."
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Local .env file not found at $envFile. Add GEMINI_API_KEY before starting StagePulse."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}

Push-Location -LiteralPath $projectRoot
try {
    & $python backend/serve.py --host $HostAddress --port $Port --config $Config
    if ($LASTEXITCODE -ne 0) {
        throw "StagePulse server exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
