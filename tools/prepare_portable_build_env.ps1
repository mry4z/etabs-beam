[CmdletBinding()]
param(
    [string]$BasePython = "python",

    [string]$EnvironmentDirectory = ""
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$requirements = Join-Path $PSScriptRoot "requirements-portable-build.txt"
if ([string]::IsNullOrWhiteSpace($EnvironmentDirectory)) {
    $EnvironmentDirectory = Join-Path $workspaceRoot "build\portable-build-env"
}
$resolvedEnvironment = [System.IO.Path]::GetFullPath($EnvironmentDirectory)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot "build"))
if (-not $resolvedEnvironment.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "EnvironmentDirectory must stay within $allowedRoot"
}
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Portable build requirements not found: $requirements"
}

$environmentPython = Join-Path $resolvedEnvironment "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $environmentPython -PathType Leaf)) {
    & $BasePython -m venv $resolvedEnvironment
    if ($LASTEXITCODE -ne 0) {
        throw "venv creation failed with exit code $LASTEXITCODE"
    }
}

& $environmentPython -m pip install --disable-pip-version-check -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Portable build dependency installation failed with exit code $LASTEXITCODE"
}

& $environmentPython -c "import PyInstaller, clr, pandas, openpyxl, yaml; print('portable_build_env=ready')"
if ($LASTEXITCODE -ne 0) {
    throw "Portable build environment import check failed with exit code $LASTEXITCODE"
}

Write-Host "Portable build Python: $environmentPython"
