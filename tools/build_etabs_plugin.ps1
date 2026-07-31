[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EtabsInstallDirectory,

    [string]$OutputDirectory = "",

    [string]$PythonExecutable = "python",

    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

# 本脚本依赖 PowerShell Core API（Get-FileHash 等）；Windows PowerShell 5.1 缺失这些 cmdlet。
# 若在 5.1 下运行且存在 pwsh，则自动重启到 pwsh，避免最终 manifest 阶段失败。
if ($PSVersionTable.PSEdition -ne 'Core') {
    $pwshCommand = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($null -eq $pwshCommand) {
        throw "build_etabs_plugin.ps1 requires PowerShell 7 (pwsh). Install PowerShell 7 or invoke this script with pwsh."
    }
    $forwardArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath)
    foreach ($parameter in $PSBoundParameters.GetEnumerator()) {
        $forwardArgs += "-$($parameter.Key)"
        $forwardArgs += [string]$parameter.Value
    }
    Write-Host "Windows PowerShell 5.1 detected; restarting build under pwsh: $($pwshCommand.Source)"
    & $pwshCommand.Source $forwardArgs
    exit $LASTEXITCODE
}

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$projectPath = Join-Path $workspaceRoot "plugins\etabs\BeamGenerator.EtabsPlugin\BeamGenerator.EtabsPlugin.csproj"
$validatorPath = Join-Path $workspaceRoot "tools\EtabsPluginValidator\EtabsPluginValidator.csproj"
$portableBuilderPath = Join-Path $workspaceRoot "tools\build_portable_workflow.ps1"
$guidePath = Join-Path $workspaceRoot "docs\guides\ETABS_PLUGIN.md"
$apiPath = Join-Path $EtabsInstallDirectory "ETABSv1.dll"
$hostPath = Join-Path $EtabsInstallDirectory "ETABS.exe"

if (-not (Test-Path -LiteralPath $apiPath -PathType Leaf)) {
    throw "ETABSv1.dll not found: $apiPath"
}
if (-not (Test-Path -LiteralPath $hostPath -PathType Leaf)) {
    throw "ETABS.exe not found: $hostPath"
}
if (-not (Test-Path -LiteralPath $guidePath -PathType Leaf)) {
    throw "Plugin guide not found: $guidePath"
}
if (-not (Test-Path -LiteralPath $portableBuilderPath -PathType Leaf)) {
    throw "Portable workflow builder not found: $portableBuilderPath"
}

$apiInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($apiPath)
$hostInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($hostPath)
$hostMajor = ([version]$hostInfo.FileVersion).Major
if ($hostMajor -notin @(22, 23)) {
    throw "Unsupported ETABS host version: $($hostInfo.FileVersion)"
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDirectory = Join-Path $workspaceRoot "build\etabs-plugin\v0.2.0-etabs$hostMajor-$stamp"
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot "build\etabs-plugin"))
if (-not $resolvedOutput.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must stay within $allowedRoot"
}
if (Test-Path -LiteralPath $resolvedOutput) {
    throw "OutputDirectory already exists; choose a new non-overwriting path: $resolvedOutput"
}
New-Item -ItemType Directory -Path $resolvedOutput | Out-Null

& dotnet publish $projectPath `
    --configuration $Configuration `
    --runtime win-x64 `
    --self-contained false `
    --output $resolvedOutput `
    "-p:EtabsApiPath=$apiPath" `
    "-p:DebugSymbols=false" `
    "-p:DebugType=None"
if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE"
}

$pluginPath = Join-Path $resolvedOutput "BeamGenerator.EtabsPlugin.dll"
if (-not (Test-Path -LiteralPath $pluginPath -PathType Leaf)) {
    throw "Published plugin DLL not found: $pluginPath"
}
if (Test-Path -LiteralPath (Join-Path $resolvedOutput "ETABSv1.dll")) {
    throw "ETABSv1.dll must not be copied into the plugin package."
}

Copy-Item -LiteralPath $guidePath -Destination (Join-Path $resolvedOutput "README.md")

& $portableBuilderPath `
    -OutputDirectory (Join-Path $resolvedOutput "workflow") `
    -PythonExecutable $PythonExecutable `
    -EtabsApiPath $apiPath
if ($LASTEXITCODE -ne 0) {
    throw "Portable workflow build failed with exit code $LASTEXITCODE"
}

$validatorOutput = & dotnet run `
    --project $validatorPath `
    --configuration Release `
    -- `
    $pluginPath `
    $apiPath
if ($LASTEXITCODE -ne 0) {
    throw "Plugin contract validation failed with exit code $LASTEXITCODE"
}
$validatorOutput | Set-Content -LiteralPath (Join-Path $resolvedOutput "contract-validation.json") -Encoding UTF8

$artifactRecords = Get-ChildItem -LiteralPath $resolvedOutput -Recurse -File |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName
        if ($relative.StartsWith($resolvedOutput, [System.StringComparison]::OrdinalIgnoreCase)) {
            $relative = $relative.Substring($resolvedOutput.Length).TrimStart('\', '/')
        }
        [ordered]@{
            name = $relative
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
$manifest = [ordered]@{
    schema = "etabs-beam-generator/plugin-package@1"
    generated_at = (Get-Date).ToString("o")
    plugin_version = "0.2.0"
    target = "net8.0-windows/x64"
    portable_workflow = "workflow/BeamGenerator.Workflow/BeamGenerator.Workflow.exe"
    local_python_required = $false
    repository_source_required = $false
    etabs_host_version = $hostInfo.FileVersion
    etabs_api_file_version = $apiInfo.FileVersion
    etabs_api_reference = $apiPath
    etabsv1_in_package = $false
    contract_validation = "passed"
    artifacts = $artifactRecords
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $resolvedOutput "package-manifest.json") -Encoding UTF8

$zipPath = "$resolvedOutput.zip"
Compress-Archive -Path (Join-Path $resolvedOutput "*") -DestinationPath $zipPath

Write-Host "Plugin package: $resolvedOutput"
Write-Host "Plugin DLL:     $pluginPath"
Write-Host "Package ZIP:    $zipPath"
