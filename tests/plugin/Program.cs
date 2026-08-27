using BeamGenerator.EtabsPlugin;

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
