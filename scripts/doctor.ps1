param(
    [int]$Port = 8000,
    [string]$Config = 'config/stages.gate3.json'
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$envFile = Join-Path $projectRoot '.env'
$configFile = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $projectRoot $Config }
$failed = $false

function Report-Check($name, $ok, $hint) {
    if ($ok) { Write-Host "[OK] $name" }
    else {
        Write-Host "[FAIL] $name - $hint"
        $script:failed = $true
    }
}

Write-Host 'StagePulse Doctor'
$pythonAvailable = $false
foreach ($name in @('python.exe', 'py.exe')) {
    $systemPython = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $systemPython) { continue }
    try {
        if ($name -eq 'py.exe') { & $systemPython.Source -3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' *> $null }
        else { & $systemPython.Source -c 'import sys; sys.exit(sys.version_info < (3, 10))' *> $null }
        $pythonAvailable = $LASTEXITCODE -eq 0
    }
    catch { }
    if ($pythonAvailable) { break }
}
Report-Check 'Python' $pythonAvailable 'Install Python 3.10 or newer and add it to PATH.'

$venvAvailable = Test-Path -LiteralPath $venvPython -PathType Leaf
Report-Check 'Virtual environment' $venvAvailable 'Run scripts/setup.ps1.'

$dependenciesAvailable = $false
if ($venvAvailable) {
    try {
        & $venvPython -c 'import google.genai, dotenv, fastapi, uvicorn, qrcode' *> $null
        $dependenciesAvailable = $LASTEXITCODE -eq 0
    }
    catch { }
}
Report-Check 'Dependencies' $dependenciesAvailable 'Run scripts/setup.ps1 to install requirements.txt.'

$ffmpegAvailable = $false
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if ($ffmpeg) {
    try {
        & $ffmpeg.Source -version *> $null
        $ffmpegAvailable = $LASTEXITCODE -eq 0
    }
    catch { }
}
Report-Check 'FFmpeg' $ffmpegAvailable 'Install FFmpeg and add ffmpeg.exe to PATH.'

$envAvailable = Test-Path -LiteralPath $envFile -PathType Leaf
Report-Check '.env' $envAvailable 'Run scripts/setup.ps1 to create it from .env.example.'

$keyConfigured = $false
if ($envAvailable) {
    foreach ($line in (Get-Content -LiteralPath $envFile)) {
        if ($line -match '^\s*GEMINI_API_KEY\s*=\s*(.*)$') {
            $value = $Matches[1].Trim()
            if ($value.StartsWith('#')) { $value = '' }
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2).Trim()
            }
            $keyConfigured = $value.Length -gt 0
            break
        }
    }
}
Report-Check 'Gemini API key configured' $keyConfigured 'Set GEMINI_API_KEY in .env.'

$stagesValid = $false
try {
    $configuration = Get-Content -Raw -LiteralPath $configFile | ConvertFrom-Json
    if (-not ($configuration.stages -is [array]) -or $configuration.stages.Count -eq 0) {
        throw 'No stages configured'
    }
    $ids = @{}
    foreach ($stage in $configuration.stages) {
        $id = ([string]$stage.id).Trim()
        if (-not $id -or -not ([string]$stage.name).Trim() -or -not ([string]$stage.audio_file).Trim()) {
            throw 'A stage is missing id, name, or audio_file'
        }
        if ($ids.ContainsKey($id)) { throw 'Duplicate stage ID' }
        if ($stage.source_language -ne 'en' -or $stage.target_language -ne 'es') {
            throw 'Stage languages must be en to es'
        }
        $ids[$id] = $true
    }
    $stagesValid = $true
}
catch { }
Report-Check 'Stage configuration' $stagesValid 'Check config/stages.gate3.json for a nonempty stages array, unique IDs, names, audio_file fields, and en-to-es languages.'

$portAvailable = $false
if ($Port -ge 1 -and $Port -le 65535) {
    $listener = $null
    try {
        $occupied = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
            Where-Object { $_.Port -eq $Port }
        if (-not $occupied) {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
            $listener.Start()
            $portAvailable = $true
        }
    }
    catch { }
    finally { if ($listener) { $listener.Stop() } }
}
Report-Check "Port $Port available" $portAvailable 'Choose a free port, or stop the process using this port.'

if ($failed) {
    Write-Host 'Fix the failed checks before starting StagePulse.'
    exit 1
}
Write-Host 'Ready to start StagePulse.'
