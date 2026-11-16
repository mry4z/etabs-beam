using System.Diagnostics;
using System.Drawing.Imaging;
using System.Reflection;
using System.Text.Json;
using BeamGenerator.EtabsPlugin;
using ETABSv1;

namespace EtabsPluginSmokeHost;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if ((args.Length is not 1 and not 2 and not 5)
            || !int.TryParse(args[0], out var processId)
            || processId <= 0)
        {
            Console.Error.WriteLine(
                "Usage: EtabsPluginSmokeHost <ETABS PID> [selection.json] "
                    + "[<portable-runner.exe> <config.yaml> <output-root>]"
            );
            return 2;
        }

        var selectionOutput = args.Length >= 2 ? Path.GetFullPath(args[1]) : "";
        var runWorkflow = args.Length == 5;
        var workflowOutputRoot = runWorkflow ? Path.GetFullPath(args[4]) : "";
        Environment.SetEnvironmentVariable("ETABS_BEAM_PLUGIN_SMOKE_HOST_PID", processId.ToString());
        if (runWorkflow)
        {
            Environment.SetEnvironmentVariable(
                "ETABS_BEAM_PLUGIN_SETTINGS_PATH",
                Path.Combine(workflowOutputRoot, "plugin-settings.smoke.json")
            );
            Environment.SetEnvironmentVariable(
                "ETABS_BEAM_WORKFLOW_RUNNER",
                Path.GetFullPath(args[2])
            );
            Environment.SetEnvironmentVariable(
                "ETABS_BEAM_WORKFLOW_CONFIG",
                Path.GetFullPath(args[3])
            );
        }

        ApplicationConfiguration.Initialize();
        cHelper helper = new Helper();
        var application = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", processId);
        if (application is null || application.SapModel is null)
        {
            Console.Error.WriteLine($"Unable to attach to ETABS PID {processId}.");
            return 2;
        }

        var pluginAssembly = typeof(cPlugin).Assembly;
        var loaderTypeName = $"{pluginAssembly.GetName().Name}.cPlugin";
        var loaderType = pluginAssembly.GetType(loaderTypeName, throwOnError: true)
            ?? throw new InvalidOperationException(
                $"ETABS loader entry type {loaderTypeName} is missing."
            );
        var plugin = Activator.CreateInstance(loaderType) as cPluginContract
            ?? throw new InvalidOperationException(
                $"ETABS loader could not create {loaderTypeName}."
            );
        var info = "";
        Require(plugin.Info(ref info) == 0 && info.Contains("ETABS Beam Generator", StringComparison.Ordinal), "Info failed.");

        cSapModel sapModel = application.SapModel;
        var callbackImplementation = new SmokeCallback();
        cPluginCallback callback = callbackImplementation;
        plugin.Main(ref sapModel, ref callback);

        Form? form = null;
        object? snapshot = null;
        var timer = Stopwatch.StartNew();
        while (timer.Elapsed < TimeSpan.FromSeconds(15))
        {
            Application.DoEvents();
            form ??= Application.OpenForms.Cast<Form>().SingleOrDefault(candidate =>
                candidate.Text.StartsWith("ETABS Beam Generator 插件", StringComparison.Ordinal)
            );
            if (form is not null)
            {
                snapshot = form
                    .GetType()
                    .GetField("_snapshot", BindingFlags.Instance | BindingFlags.NonPublic)
                    ?.GetValue(form);
                if (snapshot is not null)
                {
                    break;
                }
            }
            Thread.Sleep(50);
        }

        Require(form is not null && form.Visible, "Plugin window did not become visible.");
        Require(snapshot is not null, "Plugin did not read a model snapshot.");
        var snapshotType = snapshot!.GetType();
        var hostVersion = ReadProperty<string>(snapshot, snapshotType, "HostVersion");
        var supportedHost = ReadProperty<bool>(snapshot, snapshotType, "SupportedHost");
        var frameCount = ReadProperty<int>(snapshot, snapshotType, "FrameCount");
        var selectedObjectCount = ReadProperty<int>(snapshot, snapshotType, "SelectedObjectCount");
        var modelPath = ReadProperty<string>(snapshot, snapshotType, "ModelPath");
        var selectionSchema = "";
        if (!string.IsNullOrWhiteSpace(selectionOutput))
        {
            Directory.CreateDirectory(Path.GetDirectoryName(selectionOutput)!);
            var exporter = plugin
                .GetType()
                .Assembly.GetType("BeamGenerator.EtabsPlugin.SelectionExporter", throwOnError: true)
                ?.GetMethod("Write", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            Require(exporter is not null, "SelectionExporter.Write is unavailable.");
            exporter!.Invoke(null, new[] { snapshot, selectionOutput, plugin.GetType().Assembly.GetName().Version!.ToString() });
            using var document = JsonDocument.Parse(File.ReadAllText(selectionOutput));
            selectionSchema = document.RootElement.GetProperty("schema").GetString() ?? "";
            Require(
                selectionSchema == "etabs-beam-generator/plugin-selection@1",
                $"Unexpected selection schema: {selectionSchema}"
            );
        }

        var screenshotDirectory = Environment.GetEnvironmentVariable(
            "ETABS_BEAM_PLUGIN_SMOKE_SCREENSHOT_DIR"
        );
        var modelScreenshot = "";
        var workflowScreenshot = "";
        if (!string.IsNullOrWhiteSpace(screenshotDirectory))
        {
            var screenshots = CaptureScreenshots(form!, screenshotDirectory);
            modelScreenshot = screenshots.Model;
            workflowScreenshot = screenshots.Workflow;
        }

        var workflowOutputDirectory = "";
        var workflowManifest = "";
        if (runWorkflow)
        {
            Directory.CreateDirectory(workflowOutputRoot);
            SetTextBox(form!, "_configText", Path.GetFullPath(args[3]));
            SetTextBox(form!, "_outputRootText", workflowOutputRoot);
            var selectionMode = ReadField<ComboBox>(form!, "_selectionMode");
            selectionMode.SelectedItem = "all";
            ReadField<CheckBox>(form!, "_exportExcel").Checked = false;

            var runMethod = form!
                .GetType()
                .GetMethod("RunWorkflowAsync", BindingFlags.Instance | BindingFlags.NonPublic);
            Require(runMethod is not null, "MainForm.RunWorkflowAsync is unavailable.");
            var task = runMethod!.Invoke(form, null) as Task;
            Require(task is not null, "MainForm.RunWorkflowAsync did not return a Task.");
            timer.Restart();
            while (!task!.IsCompleted && timer.Elapsed < TimeSpan.FromMinutes(10))
            {
                Application.DoEvents();
                Thread.Sleep(100);
            }
            Require(task.IsCompleted, "Python read-only workflow did not finish within ten minutes.");
            task.GetAwaiter().GetResult();
            Require(
                ReadProperty<int>(form, form.GetType(), "CompletionErrorFlag") == 0,
                "Python read-only workflow returned a plugin error."
            );
            workflowOutputDirectory = ReadField<string>(form, "_lastOutputDirectory");
            Require(Directory.Exists(workflowOutputDirectory), "Workflow output directory is missing.");
            workflowManifest = Directory.GetFiles(workflowOutputDirectory, "run_manifest_*.json").SingleOrDefault() ?? "";
            Require(!string.IsNullOrWhiteSpace(workflowManifest), "Workflow run manifest is missing.");
        }

        form!.Close();
        Application.DoEvents();
        Require(callbackImplementation.FinishCount == 1, "cPluginCallback.Finish must be called exactly once.");
        Require(callbackImplementation.ErrorFlag == 0, $"Plugin callback returned error {callbackImplementation.ErrorFlag}.");

        var result = new
        {
            verdict = "passed",
            etabs_process_id = processId,
            host_version = hostVersion,
            supported_host = supportedHost,
            model_path = modelPath,
            frame_count = frameCount,
            selected_object_count = selectedObjectCount,
            selection_output = selectionOutput,
            selection_schema = selectionSchema,
            workflow_output_directory = workflowOutputDirectory,
            workflow_manifest = workflowManifest,
            model_screenshot = modelScreenshot,
            workflow_screenshot = workflowScreenshot,
            window_visible = true,
            callback_finish_count = callbackImplementation.FinishCount,
            callback_error_flag = callbackImplementation.ErrorFlag,
            loader_type_name = loaderTypeName,
            note = "External smoke host resolved AssemblyName.cPlugin and invoked the exact CSI plugin contract with a real ETABS SapModel.",
        };
        Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }

    private static T ReadProperty<T>(object instance, Type type, string name)
    {
        var value = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public)?.GetValue(instance);
        return value is T typed ? typed : throw new InvalidOperationException($"Snapshot property {name} is unavailable.");
    }

    private static T ReadField<T>(object instance, string name)
    {
        var value = instance.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(instance);
        return value is T typed ? typed : throw new InvalidOperationException($"Field {name} is unavailable.");
    }

    private static void SetTextBox(Form form, string fieldName, string value)
    {
        ReadField<TextBox>(form, fieldName).Text = value;
    }

    private static (string Model, string Workflow) CaptureScreenshots(
        Form form,
        string outputDirectory
    )
    {
        var resolved = Path.GetFullPath(outputDirectory);
        Directory.CreateDirectory(resolved);
        var tabs = form.Controls.OfType<TabControl>().Single();
        var originalIndex = tabs.SelectedIndex;

        string Capture(int index, string fileName)
        {
            tabs.SelectedIndex = index;
            Application.DoEvents();
            using var bitmap = new Bitmap(form.ClientSize.Width, form.ClientSize.Height);
            form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, form.ClientSize));
            var path = Path.Combine(resolved, fileName);
            bitmap.Save(path, ImageFormat.Png);
            return path;
        }

        var model = Capture(0, "plugin-model-tab.png");
        var workflow = Capture(1, "plugin-workflow-tab.png");
        tabs.SelectedIndex = originalIndex;
        Application.DoEvents();
        return (model, workflow);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
        {
            throw new InvalidOperationException(message);
        }
    }

    private sealed class SmokeCallback : cPluginCallback
    {
        public int ErrorFlag { get; private set; }

        public bool Finished { get; private set; }

        public int FinishCount { get; private set; }

        public void Finish(int iVal)
        {
            FinishCount++;
            ErrorFlag = iVal;
            Finished = true;
        }
    }
}
