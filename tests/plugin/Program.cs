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
Console.WriteLine($"PASS: {cases.Length} offline plugin request cases (no ETABS attachment).");

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
