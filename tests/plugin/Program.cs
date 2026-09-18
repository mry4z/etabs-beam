using BeamGenerator.EtabsPlugin;
using System.Text.Json;

if (args.Contains("--pid"))
{
    // Test child only: no API assembly, ETABS model, or external app is loaded.
    Console.WriteLine("OFFLINE_CHILD_READY");
    await Task.Delay(30000);
    return;
}

static void Check(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}

var baseline = new WorkflowRunRequest("runner.exe", "work", "config.yaml", "ETABSv1.dll",
    "output", "selected", true, true, 123, "model.EDB");
Check(new PluginSettings().DxfTextHeight == 100, "plugin UI DXF text height default");
Check(baseline.DxfTextHeight == 100, "workflow request DXF text height default");
var cases = new[]
{
    baseline,
    baseline with { TowerCheck = true, TowerPreflightOnly = true, ExportExcel = false, ExportRebarWorkbook = false },
    baseline with { TowerCheck = true, TowerContinueOnReview = true },
    baseline with { TowerCheck = true, TowerCreateGroups = true, TowerPlanPath = "审查 计划.json", ExportExcel = false, ExportRebarWorkbook = false },
};
foreach (var request in cases)
{
    var start = PythonWorkflowRunner.BuildStartInfo(request);
    var arguments = start.ArgumentList;
    Check(start.CreateNoWindow && !start.UseShellExecute, "runner must be hidden and avoid shell quoting");
    Check(arguments.Contains("--read-only") == !request.TowerCreateGroups, "read-only/mutation modes must be exclusive");
    Check(arguments.Contains("--tower-check") == request.TowerCheck, "tower check flag");
    Check(arguments.Contains("--tower-preflight-only") == request.TowerPreflightOnly, "tower-only flag");
    Check(arguments.Contains("--tower-continue-on-review") == request.TowerContinueOnReview, "review opt-in flag");
    Check(arguments.Contains("--allow-tower-group-mutation") == request.TowerCreateGroups, "group opt-in flag");
    Check(arguments.Contains("--tower-create-groups") == request.TowerCreateGroups, "group action flag");
    Check(!arguments.Intersect(new[] { "--writeback", "--run-analysis", "--run-design", "--save" }).Any(), "no unrelated mutation flags");
    if (request.TowerCreateGroups) Check(arguments.Contains(request.TowerPlanPath), "plan path must remain a single argument");
}
var dxfRequest = baseline with { ExportBeamResultDxf = true, DxfTextHeight = 75 };
var dxfArguments = PythonWorkflowRunner.BuildStartInfo(dxfRequest).ArgumentList;
Check(dxfArguments.Contains("--export-beam-result-dxf"), "beam DXF export flag");
var dxfHeightIndex = dxfArguments.IndexOf("--dxf-text-height");
Check(dxfHeightIndex >= 0 && dxfArguments[dxfHeightIndex + 1] == "75", "beam DXF text height");
Check(!PythonWorkflowRunner.BuildStartInfo(baseline).ArgumentList.Contains("--export-beam-result-dxf"), "baseline omits DXF flag");
Console.WriteLine($"PASS: {cases.Length} offline plugin request cases (no ETABS attachment).");

var previousSettingsPath = Environment.GetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH");
var settingsPath = Path.Combine(Path.GetTempPath(), $"etabs-beam-plugin-settings-{Guid.NewGuid():N}.json");
try
{
    Environment.SetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH", settingsPath);
    var defaults = PluginSettingsStore.Load(out _);
    Check(defaults.DxfTextHeight == 100, "new settings DXF text height default");
    PluginSettingsStore.Save(new PluginSettings { SchemaVersion = 3, DxfTextHeight = 17 });
    Check(PluginSettingsStore.Load(out _).DxfTextHeight == 100, "invalid settings fallback to 100");
    foreach (var validHeight in new[] { 25, 50, 75, 100 })
    {
        PluginSettingsStore.Save(new PluginSettings { SchemaVersion = 3, DxfTextHeight = validHeight });
        Check(PluginSettingsStore.Load(out _).DxfTextHeight == validHeight,
            $"valid settings height preserved: {validHeight}");
    }
}
finally
{
    Environment.SetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH", previousSettingsPath);
    if (File.Exists(settingsPath)) File.Delete(settingsPath);
}
Console.WriteLine("PASS: DXF text height defaults and settings migration.");

var normalBeamRequest = new WorkflowRunRequest("runner.exe", "work", "config.yaml", "ETABSv1.dll",
    "output", "selected", true, true, 123, "model.EDB", TowerCheck: false);
var normalBeamArguments = PythonWorkflowRunner.BuildStartInfo(normalBeamRequest).ArgumentList;
Check(!normalBeamArguments.Contains("--tower-check"), "normal beam-table workflow must not serialize Tower preflight");
Check(!normalBeamArguments.Contains("--tower-continue-on-review"), "normal beam-table workflow must not carry Tower review gate");

using var blockedReport = JsonDocument.Parse("""
{
  "status": "BLOCKED",
  "checked_beams": 795,
  "issue_count": 0,
  "diagnostics": [
    "TOWER_TABLE_ONLY_REJECTED: public tables lack a complete catalog.",
    "TOWER_STORY_ELEVATION_INCOMPLETE: same-name Stories are not distinguishable.",
    "TOWER_TIMING_MS: selection_capture=1.0"
  ],
  "records": []
}
""");
var summary = TowerReportSummary.Parse(blockedReport.RootElement);
Check(summary.Status == "BLOCKED" && summary.IssueCount == 0, "blocked Tower report status");
Check(summary.Reasons.Count == 2, "Tower summary keeps actionable reasons and drops timing telemetry");
Check(summary.StatusLine.Contains("TOWER_TABLE_ONLY_REJECTED", StringComparison.Ordinal), "Tower status exposes blocked reason");
Console.WriteLine("PASS: normal beam workflow bypasses Tower gate and blocked Tower reasons are surfaced.");
await UiRecoveryTests.RunAsync();
