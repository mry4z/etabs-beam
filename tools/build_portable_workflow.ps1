[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$PythonExecutable = "python",

    [string]$EtabsApiPath = ""
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$specPath = Join-Path $PSScriptRoot "portable_workflow.spec"
$configSource = Join-Path $workspaceRoot "plugins\etabs\portable\config.yaml"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot "build\etabs-plugin"))

if (-not $resolvedOutput.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must stay within $allowedRoot"
}
if (Test-Path -LiteralPath $resolvedOutput) {
    throw "Portable workflow output already exists: $resolvedOutput"
}
if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
    throw "PyInstaller spec not found: $specPath"
}
if (-not (Test-Path -LiteralPath $configSource -PathType Leaf)) {
    throw "Portable workflow config not found: $configSource"
}

$workDirectory = Join-Path (Split-Path -Parent $resolvedOutput) (
    ".portable-work-" + [System.IO.Path]::GetFileName($resolvedOutput)
)
if (Test-Path -LiteralPath $workDirectory) {
    throw "Portable workflow work directory already exists: $workDirectory"
}

& $PythonExecutable -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $resolvedOutput `
    --workpath $workDirectory `
    $specPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$runnerDirectory = Join-Path $resolvedOutput "BeamGenerator.Workflow"
$runnerPath = Join-Path $runnerDirectory "BeamGenerator.Workflow.exe"
$configPath = Join-Path $runnerDirectory "config.yaml"
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "Portable workflow executable not found: $runnerPath"
}
Copy-Item -LiteralPath $configSource -Destination $configPath

if (-not [string]::IsNullOrWhiteSpace($EtabsApiPath)) {
    if (-not (Test-Path -LiteralPath $EtabsApiPath -PathType Leaf)) {
        throw "ETABS API DLL not found for portable self-test: $EtabsApiPath"
    }
    $selfTest = & $runnerPath `
        --runtime-self-test `
        --config $configPath `
        --etabs-api $EtabsApiPath
    if ($LASTEXITCODE -ne 0) {
        throw "Portable workflow self-test failed with exit code $LASTEXITCODE"
    }
    $selfTest | Set-Content -LiteralPath (Join-Path $runnerDirectory "runtime-self-test.json") -Encoding UTF8
}

Write-Host "Portable workflow: $runnerPath"
