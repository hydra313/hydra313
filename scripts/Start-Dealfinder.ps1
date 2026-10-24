param([switch]$NoBrowser)
& (Join-Path $PSScriptRoot 'Start-ConsoleHawk.ps1') -NoBrowser:$NoBrowser
