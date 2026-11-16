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
Check(new PluginSettings().DetailingPolicy == DetailingPolicyIds.DrawingV3, "plugin UI detailing policy defaults to V3");
Check(baseline.DetailingPolicy == DetailingPolicyIds.DrawingV3, "workflow request detailing policy defaults to V3");
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
    Check(arguments.Contains("--detailing-policy") == !request.TowerCheck, "detailing policy is only passed for ordinary generation");
    if (!request.TowerCheck)
    {
        var policyIndex = arguments.IndexOf("--detailing-policy");
        Check(policyIndex >= 0 && arguments[policyIndex + 1] == request.DetailingPolicy,
            "ordinary generation passes the selected stable detailing policy ID");
    }
    if (request.TowerCreateGroups) Check(arguments.Contains(request.TowerPlanPath), "plan path must remain a single argument");
}
var legacyArguments = PythonWorkflowRunner.BuildStartInfo(
    baseline with { DetailingPolicy = DetailingPolicyIds.Legacy }
).ArgumentList;
var legacyPolicyIndex = legacyArguments.IndexOf("--detailing-policy");
Check(legacyArguments[legacyPolicyIndex + 1] == "legacy", "legacy detailing strategy remains selectable");
var dxfRequest = baseline with { ExportBeamResultDxf = true, DxfTextHeight = 75 };
var dxfArguments = PythonWorkflowRunner.BuildStartInfo(dxfRequest).ArgumentList;
Check(dxfArguments.Contains("--export-beam-result-dxf"), "beam DXF export flag");
var dxfHeightIndex = dxfArguments.IndexOf("--dxf-text-height");
Check(dxfHeightIndex >= 0 && dxfArguments[dxfHeightIndex + 1] == "75", "beam DXF text height");
Check(!PythonWorkflowRunner.BuildStartInfo(baseline).ArgumentList.Contains("--export-beam-result-dxf"), "baseline omits DXF flag");
Console.WriteLine($"PASS: {cases.Length} offline plugin request cases and policy forwarding (no ETABS attachment).");

var previousSettingsPath = Environment.GetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH");
var settingsPath = Path.Combine(Path.GetTempPath(), $"etabs-beam-plugin-settings-{Guid.NewGuid():N}.json");
try
{
    Environment.SetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH", settingsPath);
    var defaults = PluginSettingsStore.Load(out _);
    Check(defaults.DxfTextHeight == 100, "new settings DXF text height default");
    Check(defaults.DetailingPolicy == DetailingPolicyIds.Default, "new settings detailing policy default");
    File.WriteAllText(settingsPath, "{\"SchemaVersion\":3}");
    Check(PluginSettingsStore.Load(out _).DetailingPolicy == DetailingPolicyIds.Default,
        "older settings without a detailing policy default to drawing rules");
    PluginSettingsStore.Save(new PluginSettings { SchemaVersion = 4, DxfTextHeight = 17, DetailingPolicy = "invalid" });
    var invalidSettings = PluginSettingsStore.Load(out _);
    Check(invalidSettings.DxfTextHeight == 100, "invalid settings fallback to 100");
    Check(invalidSettings.DetailingPolicy == DetailingPolicyIds.Default, "invalid detailing policy falls back to drawing rules");
    foreach (var validHeight in new[] { 25, 50, 75, 100 })
    {
        PluginSettingsStore.Save(new PluginSettings { SchemaVersion = 4, DxfTextHeight = validHeight });
        Check(PluginSettingsStore.Load(out _).DxfTextHeight == validHeight,
            $"valid settings height preserved: {validHeight}");
    }
    foreach (var policy in new[] { DetailingPolicyIds.Legacy, DetailingPolicyIds.Drawing20260924V1, DetailingPolicyIds.DrawingV3 })
    {
        PluginSettingsStore.Save(new PluginSettings { SchemaVersion = 4, DetailingPolicy = policy });
        Check(PluginSettingsStore.Load(out _).DetailingPolicy == policy,
            $"valid detailing policy is persisted: {policy}");
    }
}
finally
{
    Environment.SetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH", previousSettingsPath);
    if (File.Exists(settingsPath)) File.Delete(settingsPath);
}
Console.WriteLine("PASS: DXF text height and detailing policy defaults, fallback, and persistence.");

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
