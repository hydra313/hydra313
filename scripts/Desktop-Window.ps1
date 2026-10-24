function Get-ConsoleHawkBrowserPath {
    param([string[]]$Candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ))
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Install Microsoft Edge or Google Chrome to use the app window, or launch with -Browser.'
}

function Start-ConsoleHawkWindow {
    param([string]$ProjectRoot, [string]$Url)
    $browserExe = Get-ConsoleHawkBrowserPath
    $profilePath = Join-Path $ProjectRoot '.desktop-profile'
    # A separate local profile keeps this window independent of ordinary tabs.
    # Do not disable sandboxing, certificate checks or web security.
    Start-Process -FilePath $browserExe -ArgumentList @(
        "--app=$Url",
        "--user-data-dir=`"$profilePath`"",
        '--window-size=1440,960',
        '--no-first-run',
        '--no-default-browser-check'
    )
}
