using System.Text.Json;

namespace BeamGenerator.EtabsPlugin;

internal sealed partial class MainForm
{
    private readonly Button _towerCheckButton = new();
    private readonly Button _towerGroupsButton = new();
    private bool _towerPreflightConfirmed;
    private string _towerReportPath = "";

    private void ConfigureTowerActions(FlowLayoutPanel actions)
    {
        actions.AutoSize = true;
        actions.WrapContents = true;
        _towerCheckButton.Text = "检查 Tower 分配";
        _towerCheckButton.AutoSize = true;
        _towerCheckButton.Click += async (_, _) => await RunTowerOperationAsync(createGroups: false);
        _towerGroupsButton.Text = "创建 Tower 审查 Group";
        _towerGroupsButton.AutoSize = true;
        _towerGroupsButton.Enabled = false;
        _towerGroupsButton.Click += async (_, _) => await RunTowerOperationAsync(createGroups: true);
        actions.Controls.AddRange(new Control[] { _towerCheckButton, _towerGroupsButton });
    }

    private WorkflowRunRequest PrepareTowerRequest(bool createGroups)
    {
        if (_snapshot is null || !_snapshot.SupportedHost)
            throw new InvalidOperationException("无法读取受支持的 ETABS 模型。");
        if (createGroups && _snapshot.ModelLocked)
            throw new InvalidOperationException("模型处于锁定状态，请先人工解锁；插件不会自动解锁。");
        var runner = RequireFile(PortableWorkflowLocator.Locate().RunnerExecutable, "内置计算引擎");
        var directory = Path.GetDirectoryName(runner)!;
        var config = RequireFile(ResolvePath(directory, _configText.Text), "config.yaml");
        var model = RequireFile(_snapshot.ExpectedDurableModelPath, "当前 durable EDB");
        var api = string.IsNullOrWhiteSpace(_etabsApiText.Text)
            ? RequireFile(_snapshot.ApiAssemblyPath, "当前 ETABS API 程序集")
            : RequireFile(ResolvePath(directory, _etabsApiText.Text), "ETABS API DLL");
        var selection = Convert.ToString(_selectionMode.SelectedItem) ?? "selected";
        if (selection == "selected" && _snapshot.SelectedFrames.Count == 0)
            throw new InvalidOperationException("selected 模式要求先在 ETABS 中选择至少一个 Frame Object。");
        _lastOutputDirectory = CreateUniqueOutputDirectory(ResolvePath(directory, _outputRootText.Text));
        Directory.CreateDirectory(_lastOutputDirectory);
        SaveSettings(showConfirmation: false);
        return new WorkflowRunRequest(runner, directory, config, api, _lastOutputDirectory,
            selection, false, false, _snapshot.ProcessId, model,
            TowerCheck: true, TowerPreflightOnly: !createGroups, TowerContinueOnReview: createGroups,
            TowerCreateGroups: createGroups, TowerPlanPath: createGroups ? _towerReportPath : "");
    }

    private async Task RunTowerOperationAsync(bool createGroups)
    {
        if (_workflowRunner.IsRunning) return;
        try
        {
            RefreshModel();
            if (createGroups)
            {
                if (!File.Exists(_towerReportPath)) throw new InvalidOperationException("请先完成 Tower 检查。");
                if (MessageBox.Show(this,
                    "将创建/更新插件所有的 Tower 审查 Group；不修改 Tower 归属，不自动保存模型。是否继续？",
                    "确认审查分组", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;
            }
            var request = PrepareTowerRequest(createGroups);
            _towerPreflightConfirmed = false;
            _towerCheckButton.Enabled = _towerGroupsButton.Enabled = _runButton.Enabled = false;
            _cancelButton.Enabled = true;
            _openOutputButton.Enabled = false;
            _workflowCancellation = new CancellationTokenSource();
            AppendLog(createGroups ? "正在创建 Tower 审查 Group（不保存模型）。" : "正在检查 Tower 分配（不修改模型）。");
            var result = await _workflowRunner.RunAsync(request, AppendLog, _workflowCancellation.Token);
            var reportPath = Directory.GetFiles(_lastOutputDirectory, "tower_assignment_check_*.json")
                .OrderByDescending(File.GetLastWriteTimeUtc).FirstOrDefault();
            _towerReportPath = reportPath ?? "";
            var status = "BLOCKED";
            if (reportPath is not null)
            {
                using var document = JsonDocument.Parse(File.ReadAllText(reportPath));
                var report = document.RootElement;
                status = report.GetProperty("status").GetString() ?? "BLOCKED";
                AppendLog($"[tower] 状态：{status}；检查梁 {report.GetProperty("checked_beams")}，疑似对象 {report.GetProperty("issue_count")}。");
                foreach (var group in report.GetProperty("groups").EnumerateArray())
                    AppendLog($"[tower] 审查 Group：{group.GetProperty("group_name")}；梁 {group.GetProperty("frame_names").GetArrayLength()}，点 {group.GetProperty("point_names").GetArrayLength()}。");
                AppendLog($"[tower] 报告：{reportPath}");
            }
            _towerPreflightConfirmed = !result.Cancelled && result.ExitCode == 0 && (status == "PASS" || createGroups);
            _towerGroupsButton.Enabled = !result.Cancelled && status == "REVIEW_REQUIRED";
            if (!createGroups && !result.Cancelled && status == "REVIEW_REQUIRED")
                _towerPreflightConfirmed = MessageBox.Show(this,
                    "Tower 检查发现疑似错误对象。可先创建审查 Group 后人工核对，也可明确确认后继续只读成果。是否确认继续？",
                    "Tower 需要复核", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes;
            _completionErrorFlag = _towerPreflightConfirmed ? 0 : 1;
            SetStatus(result.Cancelled ? "Tower 操作已取消。" : _towerPreflightConfirmed
                ? "Tower 检查已通过或人工确认，可继续生成梁表。" : "Tower 尚未通过，请查看报告。 ");
        }
        catch (Exception exception)
        {
            _towerPreflightConfirmed = false;
            _completionErrorFlag = 1;
            AppendLog($"[tower error] {exception}");
            ShowError("Tower 操作失败", exception);
        }
        finally
        {
            _workflowCancellation?.Dispose();
            _workflowCancellation = null;
            _towerCheckButton.Enabled = _runButton.Enabled = true;
            _cancelButton.Enabled = false;
            _openOutputButton.Enabled = Directory.Exists(_lastOutputDirectory);
        }
    }
}
