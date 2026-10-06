$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot "backend\venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot "backend\.env"

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared is not installed or is not on PATH. Install it using https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/, then reopen PowerShell."
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "The project's Python environment was not found at $pythonPath. Set up the backend environment and try again."
}
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "backend/.env was not found. Configure the app's database connection before sharing it."
}

$env:APP_ENV = "production"
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
if (-not $env:SECRET_KEY) {
    $secretBytes = New-Object byte[] 48
    $rng.GetBytes($secretBytes)
    $env:SECRET_KEY = [Convert]::ToBase64String($secretBytes)
}
if (-not $env:ADMIN_REGISTRATION_KEY) {
    $registrationBytes = New-Object byte[] 24
    $rng.GetBytes($registrationBytes)
    $env:ADMIN_REGISTRATION_KEY = [Convert]::ToBase64String($registrationBytes)
}
$rng.Dispose()

$backend = Start-Process -FilePath $pythonPath -ArgumentList "app.py" -WorkingDirectory (Join-Path $projectRoot "backend") -PassThru -WindowStyle Hidden
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if ($backend.HasExited) {
            throw "MeetIQ stopped during startup. Check the local backend configuration and try again."
        }
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:5000/healthz" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        throw "MeetIQ did not become ready at http://127.0.0.1:5000 within 45 seconds."
    }

    Write-Host "MeetIQ is running. Keep this window open; press Ctrl+C to stop sharing."
    Write-Host "Admin registration code for this run: $env:ADMIN_REGISTRATION_KEY"
    & cloudflared tunnel --url http://127.0.0.1:5000
} finally {
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force
    }
}
