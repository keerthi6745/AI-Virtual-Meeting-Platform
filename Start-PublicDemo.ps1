$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot "backend\venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot "backend\.env"

$cloudflaredCommand = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflaredCommand) {
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

$logPrefix = Join-Path ([System.IO.Path]::GetTempPath()) ("meetiq-tunnel-" + [guid]::NewGuid().ToString("N"))
$tunnelStdout = "$logPrefix.out.log"
$tunnelStderr = "$logPrefix.err.log"
$tunnel = $null
$backend = $null

try {
    # Start the tunnel first so the app can use its public origin for emailed links.
    $tunnel = Start-Process -FilePath $cloudflaredCommand.Source -ArgumentList @("tunnel", "--url", "http://127.0.0.1:5000") -RedirectStandardOutput $tunnelStdout -RedirectStandardError $tunnelStderr -PassThru -WindowStyle Hidden
    $publicUrl = $null
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if ($tunnel.HasExited) {
            throw "Cloudflare Tunnel stopped before creating a public link. Check the cloudflared output and try again."
        }
        $tunnelOutput = ""
        foreach ($logPath in @($tunnelStdout, $tunnelStderr)) {
            if (Test-Path -LiteralPath $logPath) {
                try { $tunnelOutput += [System.IO.File]::ReadAllText($logPath) } catch { }
            }
        }
        $linkMatch = [regex]::Match($tunnelOutput, "https://[a-z0-9-]+\.trycloudflare\.com")
        if ($linkMatch.Success) {
            $publicUrl = $linkMatch.Value
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $publicUrl) {
        throw "Cloudflare Tunnel did not provide a public link within 45 seconds. Check your internet connection and cloudflared setup."
    }

    $env:MEETIQ_PUBLIC_URL = $publicUrl
    $backend = Start-Process -FilePath $pythonPath -ArgumentList "app.py" -WorkingDirectory (Join-Path $projectRoot "backend") -PassThru -WindowStyle Hidden
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

    Write-Host "MeetIQ public demo link: $publicUrl"
    Write-Host "Keep this window open while people use the site. Press Ctrl+C to stop sharing."
    Write-Host "Admin registration code for this run: $env:ADMIN_REGISTRATION_KEY"
    while ($true) {
        if ($backend.HasExited) { throw "MeetIQ stopped unexpectedly." }
        if ($tunnel.HasExited) { throw "Cloudflare Tunnel stopped unexpectedly." }
        Start-Sleep -Seconds 1
    }
} finally {
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force
    }
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-Process -Id $tunnel.Id -Force
    }
    Remove-Item -LiteralPath $tunnelStdout, $tunnelStderr -ErrorAction SilentlyContinue
}
