using System.Text.Json;

namespace BeamGenerator.EtabsPlugin;

internal sealed partial class MainForm
{
    private readonly Button _towerCheckButton = new();
    private readonly Button _towerGroupsButton = new();
    private bool _towerPreflightConfirmed;
    private string _towerReportPath = "";
    private string _towerReviewScope = "";
    private bool _workflowUiProtected;
    private bool _closeAfterRecovery;
    private bool _towerGroupMutationInProgress;

    private void ResetTowerReview()
    {
        _towerPreflightConfirmed = false;
        _towerReviewScope = _towerReportPath = "";
        _towerGroupsButton.Enabled = false;
    }

    private string CurrentTowerReviewScope()
    {
        if (_snapshot is null || string.IsNullOrWhiteSpace(_snapshot.ExpectedDurableModelPath)) return "";
        var file = new FileInfo(_snapshot.ExpectedDurableModelPath);
        return TowerReviewScope.From(_snapshot, Convert.ToString(_selectionMode.SelectedItem) ?? "selected",
            file.Exists ? file.LastWriteTimeUtc.Ticks : 0, file.Exists ? file.Length : 0);
    }

    private async Task<WorkflowRunResult> RunPreservingUiAsync(WorkflowRunRequest request, CancellationToken token)
    {
        _workflowUiProtected = true;
        AppendLog("运行期间请勿更改模型或选择集；退出后将恢复原活动塔、显示单位和选择集。");
        try
        {
            return await UiSessionCheckpoint.RunAsync(
                _uiSession,
                () => _workflowRunner.RunAsync(request, AppendLog, token),
                result => ChildAttemptedCompleteObjectFallback(request, result),
                elapsed => AppendLog($"[ui recovery] host_restore_ms={elapsed.TotalMilliseconds:F1}"));
        }
        catch { ResetTowerReview(); throw; }
        finally { _workflowUiProtected = false; }
    }

    private static bool ChildAttemptedCompleteObjectFallback(WorkflowRunRequest request, WorkflowRunResult result)
    {
        // Cancellation/hard termination has no trusted completed audit.  The
        // host remains allowed one controlled recovery in that case.
        if (!request.TowerCheck || result.Cancelled || !Directory.Exists(request.OutputDirectory)) return false;
        string? reportPath;
        try
        {
            reportPath = FindTowerReport(request.OutputDirectory);
        }
        catch { return false; }
        if (reportPath is null) return false;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(reportPath));
            if (!document.RootElement.TryGetProperty("diagnostics", out var diagnostics)
                || diagnostics.ValueKind != JsonValueKind.Array) return false;
            return diagnostics.EnumerateArray()
                .Select(item => item.GetString())
                .Any(item => item is not null && item.StartsWith(
                    "SELECTION_RESTORE_AUDIT: strategy=object_fallback;",
                    StringComparison.Ordinal));
        }
        catch
        {
            // Missing/corrupt audit is intentionally distinct from a trusted
            // child attempt; the host may perform its one controlled recovery.
            return false;
        }
    }

    private void CloseAfterRecoveryIfRequested()
    {
        if (!_closeAfterRecovery || _workflowUiProtected) return;
        ResetTowerReview();
        Close();
    }

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
        _lastOutputDirectory = CreateModelOutputDirectory(
            ResolvePath(directory, _outputRootText.Text),
            model
        );
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
            var requestScope = CurrentTowerReviewScope();
            _towerPreflightConfirmed = false;
            _towerGroupMutationInProgress = createGroups;
            _towerCheckButton.Enabled = _towerGroupsButton.Enabled = _runButton.Enabled = false;
            // A hard kill can interrupt Group membership rollback; wait for this
            // small, explicitly confirmed transaction instead of killing it.
            _cancelButton.Enabled = !createGroups;
            _openOutputButton.Enabled = false;
            _workflowCancellation = new CancellationTokenSource();
            AppendLog(createGroups ? "正在创建 Tower 审查 Group（不保存模型）。" : "正在检查 Tower 分配（不修改模型）。");
            var result = await RunPreservingUiAsync(request, _workflowCancellation.Token);
            RefreshModel();
            if (requestScope != CurrentTowerReviewScope())
                throw new InvalidOperationException("检查期间模型或处理范围改变，请重新检查。");
            var reportPath = FindTowerReport(_lastOutputDirectory);
            _towerReportPath = reportPath ?? "";
            var reportSummary = TowerReportSummary.MissingReport;
            if (reportPath is not null)
            {
                using var document = JsonDocument.Parse(File.ReadAllText(reportPath));
                var report = document.RootElement;
                if (!report.TryGetProperty("selection_restore_verified", out var restored) || restored.ValueKind != JsonValueKind.True)
                    throw new InvalidOperationException("Tower 报告未证明选择集已恢复，不能继续成果运行。");
                reportSummary = TowerReportSummary.Parse(report);
                AppendLog($"[tower] {reportSummary.LogLine}");
                foreach (var reason in reportSummary.Reasons)
                    AppendLog($"[tower] 原因：{reason}");
                foreach (var group in report.GetProperty("groups").EnumerateArray())
                    AppendLog($"[tower] 审查 Group：{group.GetProperty("group_name")}；梁 {group.GetProperty("frame_names").GetArrayLength()}，点 {group.GetProperty("point_names").GetArrayLength()}。");
                AppendLog($"[tower] 报告：{reportPath}");
            }
            var status = reportSummary.Status;
            _towerPreflightConfirmed = !result.Cancelled && result.ExitCode == 0 && (status == "PASS" || createGroups);
            _towerReviewScope = requestScope;
            _towerGroupsButton.Enabled = !result.Cancelled && status == "REVIEW_REQUIRED";
            if (!createGroups && !result.Cancelled && status == "REVIEW_REQUIRED")
                _towerPreflightConfirmed = MessageBox.Show(this,
                    "Tower 检查发现疑似错误对象。可先创建审查 Group 后人工核对，也可明确确认后继续只读成果。是否确认继续？",
                    "Tower 需要复核", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes;
            SetStatus(result.Cancelled ? "Tower 操作已取消。" : _towerPreflightConfirmed
                ? "Tower 检查已通过或人工确认。" : reportSummary.StatusLine);
        }
        catch (Exception exception)
        {
            ResetTowerReview();
            AppendLog($"[tower error] {exception}");
            ShowError("Tower 操作失败", exception);
        }
        finally
        {
            _towerGroupMutationInProgress = false;
            _workflowCancellation?.Dispose();
            _workflowCancellation = null;
            _towerCheckButton.Enabled = _runButton.Enabled = true;
            _cancelButton.Enabled = false;
            _openOutputButton.Enabled = Directory.Exists(_lastOutputDirectory);
            CloseAfterRecoveryIfRequested();
        }
    }

    private static string? FindTowerReport(string outputDirectory)
    {
        var dataDirectory = Path.Combine(outputDirectory, "_data");
        var searchDirectories = Directory.Exists(dataDirectory)
            ? new[] { dataDirectory, outputDirectory }
            : new[] { outputDirectory };
        return searchDirectories
            .SelectMany(directory => Directory.GetFiles(directory, "tower_assignment_check_*.json"))
            .OrderByDescending(File.GetLastWriteTimeUtc)
            .FirstOrDefault();
    }
}
