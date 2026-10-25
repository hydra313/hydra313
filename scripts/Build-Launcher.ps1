param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$ProjectRoot
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Join-Path $PSScriptRoot '..' }
$projectPath = [IO.Path]::GetFullPath($ProjectRoot)
$outputPath = [IO.Path]::GetFullPath($Destination)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework compiler is missing.' }
$icon = Join-Path $projectPath 'dealfinder\web\static\consolehawk.ico'
$source = Join-Path $projectPath 'desktop\Launcher.cs'
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $outputPath 'App'),(Join-Path $outputPath 'Assets') -Force | Out-Null
$exe = Join-Path $outputPath 'ConsoleHawk.exe'
& $compiler /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$exe" $source
if ($LASTEXITCODE -ne 0) { throw 'ConsoleHawk executable compilation failed.' }
$outputUri = New-Object Uri ($outputPath.TrimEnd('\') + '\')
$projectUri = New-Object Uri ($projectPath.TrimEnd('\') + '\')
$relativePath = [Uri]::UnescapeDataString($outputUri.MakeRelativeUri($projectUri).ToString()).Replace('/', '\')
if ($outputUri.Scheme -ne $projectUri.Scheme -or $outputPath.Substring(0,2) -ne $projectPath.Substring(0,2)) { $relativePath = $projectPath }
$encoding = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $outputPath 'App\project-path.txt'), $relativePath, $encoding)
Copy-Item -LiteralPath $icon -Destination (Join-Path $outputPath 'Assets\consolehawk.ico') -Force
Write-Host "Built $exe"
