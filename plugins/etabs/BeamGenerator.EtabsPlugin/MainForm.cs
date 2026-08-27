using System.Diagnostics;
using System.Reflection;
using ETABSv1;

namespace BeamGenerator.EtabsPlugin;

internal sealed partial class MainForm : Form
{
    private readonly EtabsModelReader _modelReader;
    private readonly PythonWorkflowRunner _workflowRunner = new();
    private readonly TextBox _hostText = ReadOnlyTextBox();
    private readonly TextBox _apiText = ReadOnlyTextBox();
    private readonly TextBox _pidText = ReadOnlyTextBox();
    private readonly TextBox _modelText = ReadOnlyTextBox();
    private readonly TextBox _unitsText = ReadOnlyTextBox();
    private readonly TextBox _stateText = ReadOnlyTextBox();
    private readonly TextBox _countText = ReadOnlyTextBox();
    private readonly DataGridView _selectionGrid = new();
    private readonly TextBox _runtimeModeText = ReadOnlyTextBox();
    private readonly TextBox _configText = new();
    private readonly TextBox _etabsApiText = new();
    private readonly TextBox _outputRootText = new();
    private readonly ComboBox _selectionMode = new();
    private readonly CheckBox _exportExcel = new();
    private readonly CheckBox _exportRebarWorkbook = new();
    private readonly RichTextBox _logText = new();
    private readonly Button _runButton = new();
    private readonly Button _cancelButton = new();
    private readonly Button _openOutputButton = new();
    private readonly ToolStripStatusLabel _statusLabel = new();
    private readonly ToolTip _toolTip = new();
    private ModelSnapshot? _snapshot;
    private CancellationTokenSource? _workflowCancellation;
    private string _lastOutputDirectory = "";
    private int _completionErrorFlag;

    public MainForm(cSapModel sapModel)
    {
        _modelReader = new EtabsModelReader(sapModel);
        Text = $"ETABS Beam Generator 插件 v{PluginVersion}";
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(900, 650);
        Size = new Size(1080, 760);
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = new Font("Microsoft YaHei UI", 9F);
        BuildUi();
        LoadSettings();

        Shown += (_, _) => RefreshModel();
        FormClosing += OnFormClosing;
        FormClosed += (_, _) =>
        {
            _workflowRunner.Dispose();
            _toolTip.Dispose();
        };
    }

    public int CompletionErrorFlag => _completionErrorFlag;

    private static string PluginVersion =>
        Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.2.0";

    private void BuildUi()
    {
        var tabs = new TabControl { Dock = DockStyle.Fill };
        tabs.TabPages.Add(BuildModelTab());
        tabs.TabPages.Add(BuildWorkflowTab());

        var statusStrip = new StatusStrip();
        _statusLabel.Text = "准备就绪";
        statusStrip.Items.Add(_statusLabel);
        statusStrip.Items.Add(new ToolStripStatusLabel { Spring = true });
        statusStrip.Items.Add(new ToolStripStatusLabel("成果只读；审查 Group 需确认；不自动保存模型"));

        Controls.Add(tabs);
        Controls.Add(statusStrip);
    }

    private TabPage BuildModelTab()
    {
        var page = new TabPage("模型与选择集");
        var content = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 2,
        };
        content.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        content.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        content.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        var summary = new TableLayoutPanel
        {
            Dock = DockStyle.Top,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 4,
            RowCount = 5,
            Padding = new Padding(10),
        };
        summary.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 115));
        summary.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        summary.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 115));
        summary.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        for (var row = 0; row < 5; row++)
        {
            summary.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        }

        AddSummaryField(summary, 0, 0, "ETABS 宿主", _hostText);
        AddSummaryField(summary, 2, 0, "API 程序集", _apiText);
        AddSummaryField(summary, 0, 1, "ETABS PID", _pidText);
        AddSummaryField(summary, 2, 1, "显示单位", _unitsText);
        AddSummaryField(summary, 0, 2, "当前模型", _modelText, span: 3);
        AddSummaryField(summary, 0, 3, "模型状态", _stateText);
        AddSummaryField(summary, 2, 3, "对象计数", _countText);

        var actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            AutoSize = true,
        };
        var refreshButton = new Button { Text = "刷新模型与选择集", AutoSize = true };
        refreshButton.Click += (_, _) => RefreshModel();
        var exportButton = new Button { Text = "导出选择清单 JSON", AutoSize = true };
        exportButton.Click += (_, _) => ExportSelection();
        var aboutButton = new Button { Text = "关于 / 安全边界", AutoSize = true };
        aboutButton.Click += (_, _) => ShowAbout();
        actions.Controls.Add(refreshButton);
        actions.Controls.Add(exportButton);
        actions.Controls.Add(aboutButton);
        summary.Controls.Add(actions, 1, 4);
        summary.SetColumnSpan(actions, 3);

        ConfigureSelectionGrid();
        content.Controls.Add(summary, 0, 0);
        content.Controls.Add(_selectionGrid, 0, 1);
        page.Controls.Add(content);
        return page;
    }

    private TabPage BuildWorkflowTab()
    {
        var page = new TabPage("运行只读工作流");
        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 3,
            RowCount = 8,
            Padding = new Padding(12),
        };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 135));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
        for (var row = 0; row < 6; row++)
        {
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        }
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        AddReadOnlyRow(layout, 0, "内置计算引擎", _runtimeModeText);
        AddPathRow(layout, 1, "config.yaml", _configText, BrowseConfig);
        AddPathRow(layout, 2, "输出根目录", _outputRootText, () => BrowseFolder(_outputRootText));
        AddPathRow(layout, 3, "ETABS API DLL", _etabsApiText, BrowseEtabsApi);

        layout.Controls.Add(new Label { Text = "处理范围", TextAlign = ContentAlignment.MiddleRight, Dock = DockStyle.Fill }, 0, 4);
        _selectionMode.DropDownStyle = ComboBoxStyle.DropDownList;
        _selectionMode.Items.AddRange(new object[] { "selected", "all" });
        _selectionMode.SelectedIndex = 0;
        _selectionMode.Dock = DockStyle.Left;
        _selectionMode.Width = 150;
        layout.Controls.Add(_selectionMode, 1, 4);

        layout.Controls.Add(new Label { Text = "可选成果", TextAlign = ContentAlignment.MiddleRight, Dock = DockStyle.Fill }, 0, 5);
        var outputOptions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            AutoSize = true,
            Anchor = AnchorStyles.Left,
        };
        _exportExcel.Text = "同时生成基础 Excel（要求当前模型已有设计结果）";
        _exportExcel.AutoSize = true;
        _exportExcel.Anchor = AnchorStyles.Left;
        _exportRebarWorkbook.Text = "同时生成配筋工作簿（三表 + SB 表，带公式）";
        _exportRebarWorkbook.AutoSize = true;
        _exportRebarWorkbook.Anchor = AnchorStyles.Left;
        outputOptions.Controls.AddRange(new Control[] { _exportExcel, _exportRebarWorkbook });
        layout.Controls.Add(outputOptions, 1, 5);
        layout.SetColumnSpan(outputOptions, 2);

        var actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
        };
        _runButton.Text = "启动便携只读工作流";
        _runButton.AutoSize = true;
        _runButton.Click += async (_, _) => await RunWorkflowAsync();
        _cancelButton.Text = "取消运行";
        _cancelButton.AutoSize = true;
        _cancelButton.Enabled = false;
        _cancelButton.Click += (_, _) => CancelWorkflow();
        _openOutputButton.Text = "打开输出目录";
        _openOutputButton.AutoSize = true;
        _openOutputButton.Enabled = false;
        _openOutputButton.Click += (_, _) => OpenLastOutput();
        var saveSettingsButton = new Button { Text = "保存设置", AutoSize = true };
        saveSettingsButton.Click += (_, _) => SaveSettings(showConfirmation: true);
        ConfigureTowerActions(actions);
        actions.Controls.AddRange(new Control[] { _runButton, _cancelButton, _openOutputButton, saveSettingsButton });
        layout.Controls.Add(actions, 1, 6);
        layout.SetColumnSpan(actions, 2);

        _logText.Dock = DockStyle.Fill;
        _logText.ReadOnly = true;
        _logText.Font = new Font("Consolas", 9F);
        _logText.BackColor = Color.FromArgb(250, 250, 250);
        layout.Controls.Add(_logText, 0, 7);
        layout.SetColumnSpan(_logText, 3);
        page.Controls.Add(layout);
        return page;
    }

    private void ConfigureSelectionGrid()
    {
        _selectionGrid.Dock = DockStyle.Fill;
        _selectionGrid.ReadOnly = true;
        _selectionGrid.AllowUserToAddRows = false;
        _selectionGrid.AllowUserToDeleteRows = false;
        _selectionGrid.AllowUserToOrderColumns = true;
        _selectionGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
        _selectionGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
        _selectionGrid.RowHeadersVisible = false;
        _selectionGrid.Columns.Add("Name", "Object");
        _selectionGrid.Columns.Add("Guid", "GUID");
        _selectionGrid.Columns.Add("Label", "Label");
        _selectionGrid.Columns.Add("Story", "Story");
        _selectionGrid.Columns.Add("Section", "Section");
        _selectionGrid.Columns.Add("Orientation", "方向");
        _selectionGrid.Columns.Add("Diagnostics", "诊断");
        _selectionGrid.Columns["Guid"].FillWeight = 170;
        _selectionGrid.Columns["Diagnostics"].FillWeight = 200;
    }

    private void LoadSettings()
    {
        var settings = PluginSettingsStore.Load(out var warning);
        var installation = PortableWorkflowLocator.Locate();
        _configText.Text = File.Exists(settings.ConfigPath)
            ? settings.ConfigPath
            : installation.ConfigPath;
        _outputRootText.Text = settings.OutputRoot;
        _selectionMode.SelectedItem = settings.SelectionMode is "all" ? "all" : "selected";
        _exportExcel.Checked = settings.ExportExcel;
        _exportRebarWorkbook.Checked = settings.ExportRebarWorkbook;
        _etabsApiText.Text = settings.EtabsApiPath ?? "";
        UpdateRuntimeMode();
        if (!string.IsNullOrWhiteSpace(warning))
        {
            AppendLog($"[settings] {warning}");
        }
        AppendLog($"设置文件：{PluginSettingsStore.SettingsPath}");
    }

    private void SaveSettings(bool showConfirmation)
    {
        try
        {
            PluginSettingsStore.Save(
                new PluginSettings
                {
                    SchemaVersion = 2,
                    ConfigPath = _configText.Text.Trim(),
                    OutputRoot = _outputRootText.Text.Trim(),
                    SelectionMode = Convert.ToString(_selectionMode.SelectedItem) ?? "selected",
                    ExportExcel = _exportExcel.Checked,
                    ExportRebarWorkbook = _exportRebarWorkbook.Checked,
                    EtabsApiPath = _etabsApiText.Text.Trim(),
                }
            );
            if (showConfirmation)
            {
                SetStatus($"设置已保存：{PluginSettingsStore.SettingsPath}");
            }
        }
        catch (Exception exception)
        {
            ShowError("保存插件设置失败", exception);
        }
    }

    private void UpdateRuntimeMode()
    {
        var installation = PortableWorkflowLocator.Locate();
        var runnerReady = File.Exists(installation.RunnerExecutable);
        var configReady = File.Exists(_configText.Text.Trim());
        _runtimeModeText.Text = runnerReady && configReady
            ? "已就绪（后台运行，无需本机 Python 或项目源码）"
            : "缺失，请重新解压完整插件 ZIP";
        _runtimeModeText.ForeColor = runnerReady && configReady
            ? Color.DarkGreen
            : Color.DarkRed;
    }

    private void RefreshModel()
    {
        UseWaitCursor = true;
        try
        {
            var previousModel = _snapshot?.ExpectedDurableModelPath;
            _snapshot = _modelReader.Read();
            if (!string.Equals(previousModel, _snapshot.ExpectedDurableModelPath, StringComparison.OrdinalIgnoreCase))
            {
                _towerPreflightConfirmed = false;
                _towerReportPath = "";
                _towerGroupsButton.Enabled = false;
            }
            _hostText.Text = $"{_snapshot.HostVersion} ({(_snapshot.SupportedHost ? "受支持" : "不受支持")})";
            _apiText.Text = $"{_snapshot.ApiAssemblyVersion} — {_snapshot.ApiAssemblyPath}";
            _pidText.Text = _snapshot.ProcessId.ToString();
            _modelText.Text = _snapshot.ModelPath;
            _unitsText.Text = _snapshot.PresentUnits;
            _stateText.Text = _snapshot.ModelLocked ? "已锁定" : "未锁定";
            _countText.Text =
                $"Frame {_snapshot.FrameCount:N0}；选择对象 {_snapshot.SelectedObjectCount:N0}；"
                + $"选择 Frame {_snapshot.SelectedFrames.Count:N0}";
            _toolTip.SetToolTip(_apiText, _snapshot.ApiAssemblyPath);
            _toolTip.SetToolTip(_modelText, _snapshot.ModelPath);
            _toolTip.SetToolTip(_countText, _countText.Text);

            _selectionGrid.Rows.Clear();
            foreach (var frame in _snapshot.SelectedFrames)
            {
                _selectionGrid.Rows.Add(
                    frame.Name,
                    frame.Guid,
                    frame.Label,
                    frame.Story,
                    frame.Section,
                    frame.DesignOrientation,
                    string.Join(" | ", frame.Diagnostics)
                );
            }

            var status = _snapshot.Complete
                ? $"刷新完成：选择了 {_snapshot.SelectedFrames.Count:N0} 个 Frame。"
                : $"刷新完成，但存在 {_snapshot.Diagnostics.Count + _snapshot.SelectedFrames.Count(frame => !frame.Complete)} 项诊断。";
            if (string.IsNullOrWhiteSpace(_etabsApiText.Text) && !string.IsNullOrWhiteSpace(_snapshot.ApiAssemblyPath))
            {
                _etabsApiText.Text = _snapshot.ApiAssemblyPath;
            }
            SetStatus(status);
        }
        catch (Exception exception)
        {
            _completionErrorFlag = 1;
            ShowError("读取 ETABS 模型失败", exception);
        }
        finally
        {
            UseWaitCursor = false;
        }
    }

    private void ExportSelection()
    {
        if (_snapshot is null)
        {
            RefreshModel();
        }
        if (_snapshot is null)
        {
            return;
        }

        var initialDirectory = ResolvePathOrEmpty(
            PortableWorkflowLocator.Locate().WorkingDirectory,
            _outputRootText.Text
        );
        using var dialog = new SaveFileDialog
        {
            Title = "导出 ETABS 选择清单",
            Filter = "JSON 文件 (*.json)|*.json",
            FileName = $"etabs_selection_{DateTime.Now:yyyyMMdd_HHmmss}.json",
            AddExtension = true,
            DefaultExt = "json",
            InitialDirectory = Directory.Exists(initialDirectory) ? initialDirectory : "",
        };
        if (dialog.ShowDialog(this) != DialogResult.OK)
        {
            return;
        }

        try
        {
            SelectionExporter.Write(_snapshot, dialog.FileName, PluginVersion);
            SetStatus($"选择清单已导出：{dialog.FileName}");
        }
        catch (Exception exception)
        {
            _completionErrorFlag = 1;
            ShowError("导出选择清单失败", exception);
        }
    }

    private async Task RunWorkflowAsync()
    {
        if (_workflowRunner.IsRunning)
        {
            return;
        }

        RefreshModel();
        if (!_towerPreflightConfirmed)
        {
            await RunTowerOperationAsync(createGroups: false);
            if (!_towerPreflightConfirmed) return;
        }

        try
        {
            RefreshModel();
            if (_snapshot is null)
            {
                throw new InvalidOperationException("无法读取当前 ETABS 模型。");
            }
            if (!_snapshot.SupportedHost)
            {
                throw new InvalidOperationException($"当前 ETABS 宿主版本不受支持：{_snapshot.HostVersion}");
            }

            var installation = PortableWorkflowLocator.Locate();
            var runnerExecutable = RequireFile(
                installation.RunnerExecutable,
                "内置计算引擎"
            );
            var workingDirectory = Path.GetDirectoryName(runnerExecutable)
                ?? throw new InvalidOperationException("无法确定便携工作流目录。");
            var configPath = RequireFile(
                ResolvePath(workingDirectory, _configText.Text),
                "config.yaml"
            );
            var expectedModel = RequireFile(_snapshot.ExpectedDurableModelPath, "当前 durable EDB");
            var etabsApiPath = !string.IsNullOrWhiteSpace(_etabsApiText.Text.Trim())
                ? RequireFile(ResolvePath(workingDirectory, _etabsApiText.Text), "ETABS API DLL")
                : RequireFile(_snapshot.ApiAssemblyPath, "当前 ETABS API 程序集");

            var selection = Convert.ToString(_selectionMode.SelectedItem) ?? "selected";
            if (selection == "selected" && _snapshot.SelectedFrames.Count == 0)
            {
                throw new InvalidOperationException("selected 模式要求先在 ETABS 中选择至少一个 Frame Object。");
            }

            var outputRoot = ResolvePath(workingDirectory, _outputRootText.Text);
            Directory.CreateDirectory(outputRoot);
            _lastOutputDirectory = CreateUniqueOutputDirectory(outputRoot);
            Directory.CreateDirectory(_lastOutputDirectory);

            SaveSettings(showConfirmation: false);
            _logText.Clear();
            AppendLog("模式：只读。插件不会传入任何写回、保存、分析或设计参数。");
            AppendLog($"便携运行器：{runnerExecutable}");
            AppendLog($"ETABS PID：{_snapshot.ProcessId}");
            AppendLog($"期望模型：{expectedModel}");
            AppendLog($"输出目录：{_lastOutputDirectory}");

            var request = new WorkflowRunRequest(
                runnerExecutable,
                workingDirectory,
                configPath,
                etabsApiPath,
                _lastOutputDirectory,
                selection,
                _exportExcel.Checked,
                _exportRebarWorkbook.Checked,
                _snapshot.ProcessId,
                expectedModel,
                TowerCheck: true,
                TowerContinueOnReview: _towerPreflightConfirmed
            );

            _runButton.Enabled = false;
            _towerCheckButton.Enabled = false;
            _towerGroupsButton.Enabled = false;
            _cancelButton.Enabled = true;
            _openOutputButton.Enabled = false;
            _workflowCancellation = new CancellationTokenSource();
            SetStatus("便携只读工作流正在运行……");
            var result = await _workflowRunner.RunAsync(request, AppendLog, _workflowCancellation.Token);
            AppendLog(result.Cancelled ? "运行已由用户取消。" : $"运行结束，退出码：{result.ExitCode}");
            _completionErrorFlag = result.ExitCode == 0 ? 0 : 1;
            SetStatus(result.ExitCode == 0 ? "便携只读工作流完成。" : $"便携工作流未成功，退出码 {result.ExitCode}。");
            _openOutputButton.Enabled = Directory.Exists(_lastOutputDirectory);
        }
        catch (Exception exception)
        {
            _completionErrorFlag = 1;
            AppendLog($"[plugin error] {exception}");
            ShowError("无法启动或完成只读工作流", exception);
        }
        finally
        {
            _workflowCancellation?.Dispose();
            _workflowCancellation = null;
            _runButton.Enabled = true;
            _towerCheckButton.Enabled = true;
            _cancelButton.Enabled = false;
        }
    }

    private void CancelWorkflow()
    {
        _workflowCancellation?.Cancel();
        _workflowRunner.Cancel();
        _cancelButton.Enabled = false;
        SetStatus("正在取消便携工作流……");
    }

    private void OpenLastOutput()
    {
        if (!Directory.Exists(_lastOutputDirectory))
        {
            SetStatus("当前没有可打开的输出目录。");
            return;
        }

        Process.Start(new ProcessStartInfo("explorer.exe", _lastOutputDirectory) { UseShellExecute = true });
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs eventArgs)
    {
        if (!_workflowRunner.IsRunning)
        {
            return;
        }

        var answer = MessageBox.Show(
            this,
            "便携只读工作流仍在运行。关闭插件将终止由本插件启动的进程，是否继续？",
            "确认关闭",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Warning
        );
        if (answer != DialogResult.Yes)
        {
            eventArgs.Cancel = true;
            return;
        }

        _completionErrorFlag = 1;
        CancelWorkflow();
    }

    private void ShowAbout()
    {
        MessageBox.Show(
            this,
            $"ETABS Beam Generator Plugin v{PluginVersion}\n\n"
                + "初始版本能力：\n"
                + "• 读取宿主、模型和选择集摘要\n"
                + "• 导出版本化选择清单 JSON\n"
                + "• 启动随插件发布的自包含只读工作流\n\n"
                + "• 检查 Tower 分配并输出审查报告\n"
                + "安全边界：普通成果只读；仅明确确认后创建/更新插件所有的审查 Group；"
                + "不改变 Tower 归属，不自动保存、解锁、分析、设计或编号写回。",
            "关于 ETABS Beam Generator 插件",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information
        );
    }

    private void BrowseEtabsApi()
    {
        var initialDirectory = "";
        if (!string.IsNullOrWhiteSpace(_etabsApiText.Text))
        {
            initialDirectory = Path.GetDirectoryName(Path.GetFullPath(_etabsApiText.Text)) ?? "";
        }
        else if (_snapshot is not null && !string.IsNullOrWhiteSpace(_snapshot.ApiAssemblyPath))
        {
            initialDirectory = Path.GetDirectoryName(Path.GetFullPath(_snapshot.ApiAssemblyPath)) ?? "";
        }

        using var dialog = new OpenFileDialog
        {
            Title = "选择 ETABS API DLL（ETABSv1.dll）",
            Filter = "ETABS API DLL (*.dll)|*.dll|所有文件 (*.*)|*.*",
            CheckFileExists = true,
            InitialDirectory = Directory.Exists(initialDirectory) ? initialDirectory : "",
        };
        if (dialog.ShowDialog(this) == DialogResult.OK)
        {
            _etabsApiText.Text = dialog.FileName;
            SetStatus($"ETABS API DLL 已指定：{dialog.FileName}");
        }
    }

    private void BrowseConfig()
    {
        using var dialog = new OpenFileDialog
        {
            Title = "选择便携工作流 config.yaml",
            Filter = "YAML 文件 (*.yaml;*.yml)|*.yaml;*.yml|所有文件 (*.*)|*.*",
            CheckFileExists = true,
            InitialDirectory = File.Exists(_configText.Text)
                ? Path.GetDirectoryName(Path.GetFullPath(_configText.Text))
                : "",
        };
        if (dialog.ShowDialog(this) == DialogResult.OK)
        {
            _configText.Text = dialog.FileName;
            UpdateRuntimeMode();
        }
    }

    private void BrowseFolder(TextBox target)
    {
        using var dialog = new FolderBrowserDialog
        {
            Description = "选择目录",
            SelectedPath = Directory.Exists(target.Text) ? target.Text : "",
            UseDescriptionForTitle = true,
        };
        if (dialog.ShowDialog(this) == DialogResult.OK)
        {
            target.Text = dialog.SelectedPath;
        }
    }

    private void AppendLog(string line)
    {
        if (IsDisposed)
        {
            return;
        }
        if (InvokeRequired)
        {
            BeginInvoke(() => AppendLog(line));
            return;
        }

        _logText.AppendText($"[{DateTime.Now:HH:mm:ss}] {line}{Environment.NewLine}");
        _logText.SelectionStart = _logText.TextLength;
        _logText.ScrollToCaret();
    }

    private void SetStatus(string message)
    {
        _statusLabel.Text = message;
    }

    private void ShowError(string title, Exception exception)
    {
        SetStatus($"{title}：{exception.Message}");
        MessageBox.Show(this, exception.Message, title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private static TextBox ReadOnlyTextBox()
    {
        return new TextBox
        {
            ReadOnly = true,
            Dock = DockStyle.Fill,
            BackColor = SystemColors.Window,
            BorderStyle = BorderStyle.FixedSingle,
        };
    }

    private static void AddSummaryField(
        TableLayoutPanel layout,
        int column,
        int row,
        string label,
        Control value,
        int span = 1
    )
    {
        layout.Controls.Add(
            new Label
            {
                Text = label,
                TextAlign = ContentAlignment.MiddleRight,
                Dock = DockStyle.Fill,
                Margin = new Padding(3, 6, 3, 6),
            },
            column,
            row
        );
        value.Margin = new Padding(3, 4, 3, 4);
        layout.Controls.Add(value, column + 1, row);
        if (span > 1)
        {
            layout.SetColumnSpan(value, span);
        }
    }

    private static void AddReadOnlyRow(
        TableLayoutPanel layout,
        int row,
        string label,
        TextBox textBox
    )
    {
        layout.Controls.Add(
            new Label
            {
                Text = label,
                TextAlign = ContentAlignment.MiddleRight,
                Dock = DockStyle.Fill,
            },
            0,
            row
        );
        textBox.Dock = DockStyle.Fill;
        layout.Controls.Add(textBox, 1, row);
        layout.SetColumnSpan(textBox, 2);
    }

    private static void AddPathRow(
        TableLayoutPanel layout,
        int row,
        string label,
        TextBox textBox,
        Action browseAction
    )
    {
        layout.Controls.Add(
            new Label { Text = label, TextAlign = ContentAlignment.MiddleRight, Dock = DockStyle.Fill },
            0,
            row
        );
        textBox.Dock = DockStyle.Fill;
        layout.Controls.Add(textBox, 1, row);
        var button = new Button { Text = "浏览…", Dock = DockStyle.Fill };
        button.Click += (_, _) => browseAction();
        layout.Controls.Add(button, 2, row);
    }

    private static string ResolvePath(string baseDirectory, string path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new InvalidOperationException("路径不能为空。");
        }

        return Path.GetFullPath(path.Trim(), baseDirectory);
    }

    private static string ResolvePathOrEmpty(string baseDirectory, string path)
    {
        try
        {
            return ResolvePath(baseDirectory, path);
        }
        catch
        {
            return "";
        }
    }

    private static string RequireDirectory(string value, string label)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"{label}不能为空。");
        }

        var path = Path.GetFullPath(value.Trim());
        return Directory.Exists(path) ? path : throw new InvalidOperationException($"{label}不存在：{path}");
    }

    private static string RequireFile(string value, string label)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"{label}不能为空。");
        }

        var path = Path.GetFullPath(value.Trim());
        return File.Exists(path) ? path : throw new InvalidOperationException($"{label}不存在：{path}");
    }

    private static string CreateUniqueOutputDirectory(string outputRoot)
    {
        var candidate = Path.Combine(outputRoot, $"run_{DateTime.Now:yyyyMMdd_HHmmss}");
        for (var suffix = 0; Directory.Exists(candidate); suffix++)
        {
            candidate = Path.Combine(outputRoot, $"run_{DateTime.Now:yyyyMMdd_HHmmss}_{suffix + 1}");
        }

        return candidate;
    }
}
