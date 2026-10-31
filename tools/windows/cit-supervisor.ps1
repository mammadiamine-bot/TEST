# CIT one-click start + crash recovery (native Windows, no administrator, no service install).
#   cit-supervisor.ps1            : start everything and open the dashboard
#   cit-supervisor.ps1 -Run       : (internal) hidden supervisor loop that restarts the backend if it exits
param([switch]$Run)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Work = if ($env:CIT_WORK) { $env:CIT_WORK } else { Join-Path (Split-Path $Repo -Parent) 'work' }
$Py   = Join-Path $Work 'venv\Scripts\python.exe'
$TokF = Join-Path $Work 'api_token.txt'
$Logs = Join-Path $Work 'logs'; New-Item -ItemType Directory -Force $Logs | Out-Null
$Url  = 'http://127.0.0.1:8765'

function Test-Ready { try { (Invoke-WebRequest "$Url/ready" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false } }

if ($Run) {   # ---- supervisor loop: restart the API on crash, with a small back-off
    $env:CIT_API_TOKEN = (Get-Content $TokF -Raw).Trim()
    $env:CIT_LOG_LEVEL = 'WARNING'
    $delay = 3
    while ($true) {
        $t0 = Get-Date
        $p = Start-Process -FilePath $Py -ArgumentList '-m', 'app.main' -WorkingDirectory (Join-Path $Repo 'backend') -WindowStyle Hidden -PassThru `
             -RedirectStandardOutput (Join-Path $Logs 'backend.out.log') -RedirectStandardError (Join-Path $Logs 'backend.err.log')
        $p.WaitForExit()
        Add-Content (Join-Path $Logs 'supervisor.log') ("{0:s} backend exited with code {1}; restarting in {2}s" -f (Get-Date), $p.ExitCode, $delay)
        $delay = if (((Get-Date) - $t0).TotalSeconds -gt 60) { 3 } else { [Math]::Min($delay * 2, 60) }
        Start-Sleep $delay
    }
}

# ---- one-click path
if (-not (Test-Path $Py))  { Write-Host "Python environment not found: $Py" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $TokF)) { Write-Host "Owner token file not found: $TokF" -ForegroundColor Red; exit 1 }

# dashboard build (only if missing)
if (-not (Test-Path (Join-Path $Repo 'frontend\dist\index.html'))) {
    Write-Host 'Building the dashboard (first start only)...'
    Push-Location (Join-Path $Repo 'frontend')
    if (-not (Test-Path 'node_modules')) { npm ci --no-audit --no-fund | Out-Null }
    $env:VITE_API_URL = ''; npm run build | Out-Null
    Pop-Location
}

# local AI: start Ollama if it is installed but not listening
if (-not (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)) {
    $ol = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ol) { $c = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'; if (Test-Path $c) { $ol = Get-Item $c } }
    if ($ol) { $exe = if ($ol.Source) { $ol.Source } else { $ol.FullName }; Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden }
}

# backend under the supervisor (only if not already answering)
if (-not (Test-Ready)) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Run'
    for ($i = 0; $i -lt 40 -and -not (Test-Ready); $i++) { Start-Sleep 1 }
}
if (-not (Test-Ready)) { Write-Host "The CIT backend did not become ready. See $Logs" -ForegroundColor Red; exit 2 }

$tok = [uri]::EscapeDataString((Get-Content $TokF -Raw).Trim())
Start-Process "$Url/ui/#token=$tok"   # fragment stays in the browser; it is never sent to the server
Write-Host "CIT is running: $Url/ui/"
