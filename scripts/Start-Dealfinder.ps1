param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dashboardUrl = 'http://127.0.0.1:8000/?show_all=true'
$logDirectory = Join-Path $projectRoot 'logs'
$launchMutex = New-Object System.Threading.Mutex($false, 'Local\DealfinderLaunch8000')
$ownsMutex = $false

function Test-Dealfinder {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/openapi.json' -UseBasicParsing -TimeoutSec 2
    } catch {
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            throw 'Port 8000 is busy or the app is still starting. Wait a moment and try again.'
        }
        return $false
    }
    $api = $response.Content | ConvertFrom-Json
    if ($api.info.title -ne 'dealfinder') {
        throw 'Port 8000 is already occupied by another application.'
    }
    return $true
}

try {
    try { $ownsMutex = $launchMutex.WaitOne(60000) }
    catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another Dealfinder launch is still in progress. Try again shortly.' }

    if (-not (Test-Dealfinder)) {
        if (-not (Test-Path -LiteralPath $pythonExe)) {
            throw 'The Python environment is missing. Complete the README installation steps first, keeping .venv in the project folder.'
        }
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $appProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','dealfinder' `
            -WorkingDirectory $projectRoot -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDirectory 'app-stdout.log') `
            -RedirectStandardError (Join-Path $logDirectory 'app-stderr.log') -PassThru

        $ready = $false
        $deadline = (Get-Date).AddSeconds(45)
        do {
            Start-Sleep -Milliseconds 400
            try {
                $api = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 2
                $ready = $api.info.title -eq 'dealfinder'
            } catch { }
            if ($ready) { break }
            $appProcess.Refresh()
            if ($appProcess.HasExited) {
                throw "Dealfinder stopped during startup. Check $logDirectory\app-stderr.log."
            }
        } while ((Get-Date) -lt $deadline)
        if (-not $ready) { throw "Dealfinder did not become ready. Check $logDirectory\app-stderr.log." }
        Write-Host 'Dealfinder started.'
    } else {
        Write-Host 'Dealfinder is already running.'
    }
} finally {
    if ($ownsMutex) { $launchMutex.ReleaseMutex() }
    $launchMutex.Dispose()
}

if (-not $NoBrowser) { Start-Process $dashboardUrl }
Write-Host $dashboardUrl
