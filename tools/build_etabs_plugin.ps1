[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EtabsInstallDirectory,

    [string]$OutputDirectory = "",

    [string]$AdditionalEtabsInstallDirectory = "",

    [string]$PythonExecutable = "python",

    [string]$LookupAssetsDirectory = "",

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
$releaseNotesPath = Join-Path $workspaceRoot "plugins\etabs\RELEASE_NOTES.md"
$defaultLookupAssetsDirectory = Join-Path $workspaceRoot "src\design\lookup_assets"
$resolvedLookupAssetsDirectory = if ([string]::IsNullOrWhiteSpace($LookupAssetsDirectory)) {
    [System.IO.Path]::GetFullPath($defaultLookupAssetsDirectory)
} else {
    [System.IO.Path]::GetFullPath($LookupAssetsDirectory)
}
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
if (-not (Test-Path -LiteralPath $resolvedLookupAssetsDirectory -PathType Container)) {
    throw "Lookup asset directory not found: $resolvedLookupAssetsDirectory"
}
$standardLookupManifest = Get-Content -LiteralPath (Join-Path $resolvedLookupAssetsDirectory "standard_indices\manifest.json") -Raw | ConvertFrom-Json
$contextLookupManifest = Get-Content -LiteralPath (Join-Path $resolvedLookupAssetsDirectory "context_indices_manifest.json") -Raw | ConvertFrom-Json

$apiInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($apiPath)
$hostInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($hostPath)
$hostMajor = ([version]$hostInfo.FileVersion).Major
if ($hostMajor -notin @(22, 23)) {
    throw "Unsupported ETABS host version: $($hostInfo.FileVersion)"
}

$projectXml = [xml](Get-Content -LiteralPath $projectPath -Raw)
$pluginVersion = [string]$projectXml.Project.PropertyGroup.Version
if ($pluginVersion -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
    throw "Plugin Version must be an explicit three- or four-part version: $pluginVersion"
}
$expectedAssemblyVersion = if ($pluginVersion -match '^\d+\.\d+\.\d+$') {
    "$pluginVersion.0"
} else {
    $pluginVersion
}

# Record the exact worktree inputs, including intentionally uncommitted fixes.
$sourcePaths = @(
    Get-ChildItem -LiteralPath (Join-Path $workspaceRoot "src") -Recurse -File -Filter *.py
    Get-ChildItem -LiteralPath (Split-Path -Parent $projectPath) -File -Filter *.cs
    Get-Item -LiteralPath $projectPath, $guidePath, $releaseNotesPath, $PSCommandPath, $portableBuilderPath,
        (Join-Path $PSScriptRoot "portable_workflow.spec"),
        (Join-Path $PSScriptRoot "portable_workflow_entry.py"),
        (Join-Path $PSScriptRoot "requirements-portable-build.txt"),
        (Join-Path $PSScriptRoot "compress_lookup_indices_xz.py"),
        (Join-Path $workspaceRoot "src\design\policies\drawing_20260924_v1.json"),
        (Join-Path $workspaceRoot "plugins\etabs\portable\config.yaml")
)
$sourceRecords = @($sourcePaths | Sort-Object FullName -Unique | ForEach-Object {
    [ordered]@{
        path = [System.IO.Path]::GetRelativePath($workspaceRoot, $_.FullName)
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})
$sourceCommit = (& git -C $workspaceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read source commit." }
$sourceStatus = @(& git -C $workspaceRoot status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "Could not read source worktree status." }

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDirectory = Join-Path $workspaceRoot "build\etabs-plugin\v$pluginVersion-v2-etabs$hostMajor-$stamp"
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot "build\etabs-plugin"))
if (-not $resolvedOutput.StartsWith($allowedRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must stay within $allowedRoot"
}
if (Test-Path -LiteralPath $resolvedOutput) {
    throw "OutputDirectory already exists; choose a new non-overwriting path: $resolvedOutput"
}
if (Test-Path -LiteralPath "$resolvedOutput.zip") {
    throw "Package ZIP already exists; choose a new non-overwriting path: $resolvedOutput.zip"
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

Copy-Item -LiteralPath $releaseNotesPath -Destination (Join-Path $resolvedOutput "README.md")
Copy-Item -LiteralPath $releaseNotesPath -Destination (Join-Path $resolvedOutput "RELEASE_NOTES.md")

& $portableBuilderPath `
    -OutputDirectory (Join-Path $resolvedOutput "workflow") `
    -PythonExecutable $PythonExecutable `
    -LookupAssetsDirectory $resolvedLookupAssetsDirectory `
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
$validation = ($validatorOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($validation.verdict -ne "passed" -or $validation.plugin_version -ne $expectedAssemblyVersion) {
    throw "Plugin contract/version mismatch."
}
$supportedHosts = @($hostMajor)
if ($AdditionalEtabsInstallDirectory) {
    $additionalApi = Join-Path $AdditionalEtabsInstallDirectory "ETABSv1.dll"
    $additionalHost = Join-Path $AdditionalEtabsInstallDirectory "ETABS.exe"
    if (-not (Test-Path -LiteralPath $additionalApi) -or -not (Test-Path -LiteralPath $additionalHost)) { throw "Additional ETABS installation is incomplete." }
    $additionalMajor = ([version][System.Diagnostics.FileVersionInfo]::GetVersionInfo($additionalHost).FileVersion).Major
    if ($additionalMajor -notin @(22, 23) -or $additionalMajor -eq $hostMajor) { throw "Additional host must be the other supported ETABS major version." }
    $additionalOutput = & dotnet run --project $validatorPath --configuration Release -- $pluginPath $additionalApi
    if ($LASTEXITCODE -ne 0) { throw "Additional plugin contract validation failed." }
    $additionalOutput | Set-Content -LiteralPath (Join-Path $resolvedOutput "contract-validation-etabs$additionalMajor.json") -Encoding UTF8
    $additionalValidation = ($additionalOutput -join [Environment]::NewLine) | ConvertFrom-Json
    if ($additionalValidation.verdict -ne "passed" -or $additionalValidation.plugin_version -ne $expectedAssemblyVersion) { throw "Additional plugin contract/version mismatch." }
    $supportedHosts += $additionalMajor
}
foreach ($sourceRecord in $sourceRecords) {
    $currentHash = (Get-FileHash -LiteralPath (Join-Path $workspaceRoot $sourceRecord.path) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($currentHash -ne $sourceRecord.sha256) { throw "Source changed during build: $($sourceRecord.path)" }
}
if (@(Get-ChildItem -LiteralPath $resolvedOutput -Recurse -File -Filter ETABSv1.dll).Count -ne 0) {
    throw "ETABSv1.dll must not be embedded anywhere in the package."
}

$artifactRecords = Get-ChildItem -LiteralPath $resolvedOutput -Recurse -File |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($resolvedOutput, $_.FullName)
        [ordered]@{
            name = $relative
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
$manifest = [ordered]@{
    schema = "etabs-beam-generator/plugin-package@1"
    generated_at = (Get-Date).ToString("o")
    plugin_version = $pluginVersion
    source = [ordered]@{
        workspace = $workspaceRoot
        git_commit = $sourceCommit
        dirty = $sourceStatus.Count -gt 0
        worktree_status = $sourceStatus
        files = $sourceRecords
    }
    target = "net8.0-windows/x64"
    portable_workflow = "workflow/BeamGenerator.Workflow/BeamGenerator.Workflow.exe"
    lookup_assets = [ordered]@{
        source_directory = $resolvedLookupAssetsDirectory
        index_compression = if ($contextLookupManifest.compression) { [string]$contextLookupManifest.compression } else { "gzip (legacy)" }
        geometry_compression = "gzip (unchanged)"
        standard_index_shards = @($standardLookupManifest.files).Count
        context_index_shards = @($contextLookupManifest.files).Count
        runtime_selection = "Prefer XZ shards when XZ and gzip versions coexist; continue to accept gzip."
    }
    local_python_required = $false
    repository_source_required = $false
    compile_reference_host = $hostInfo.FileVersion
    supported_hosts = $supportedHosts
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
