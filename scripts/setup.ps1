$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$envFile = Join-Path $projectRoot '.env'

function Find-Python {
    foreach ($name in @('python.exe', 'py.exe')) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -eq $candidate) { continue }
        try {
            if ($name -eq 'py.exe') { & $candidate.Source -3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' *> $null }
            else { & $candidate.Source -c 'import sys; sys.exit(sys.version_info < (3, 10))' *> $null }
            if ($LASTEXITCODE -eq 0) { return $candidate.Source }
        }
        catch { }
    }
    return $null
}

$systemPython = Find-Python
if (-not $systemPython) { throw 'Python 3 is required. Install Python, then rerun scripts/setup.ps1.' }
Write-Host '[OK] Python'

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host 'Creating .venv...'
    if ([IO.Path]::GetFileName($systemPython) -eq 'py.exe') {
        & $systemPython -3 -m venv (Join-Path $projectRoot '.venv')
    }
    else {
        & $systemPython -m venv (Join-Path $projectRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Check the Python venv installation.' }
}
Write-Host '[OK] Virtual environment'

& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network access and requirements.txt.' }
Write-Host '[OK] Dependencies'

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $envFile
    Write-Host 'Created .env from .env.example.'
}
else { Write-Host 'Existing .env was preserved.' }
Write-Host 'Open .env and fill in GEMINI_API_KEY manually. Never share this value.'

$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    throw 'FFmpeg is required for Test File mode. Install FFmpeg and add ffmpeg.exe to PATH, then rerun scripts/setup.ps1.'
}
& $ffmpeg.Source -version *> $null
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not run. Reinstall FFmpeg and check PATH.' }
Write-Host '[OK] FFmpeg'
Write-Host 'Setup complete. Run scripts/doctor.ps1, then scripts/start.ps1.'
