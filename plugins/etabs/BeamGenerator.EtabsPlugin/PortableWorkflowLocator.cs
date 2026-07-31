using System.Reflection;

namespace BeamGenerator.EtabsPlugin;

internal sealed record PortableWorkflowInstallation(
    string RunnerExecutable,
    string ConfigPath,
    string WorkingDirectory
)
{
    public bool IsComplete =>
        File.Exists(RunnerExecutable)
        && File.Exists(ConfigPath)
        && Directory.Exists(WorkingDirectory);
}

internal static class PortableWorkflowLocator
{
    private static readonly string[] RunnerRelativePath =
    {
        "workflow",
        "BeamGenerator.Workflow",
        "BeamGenerator.Workflow.exe",
    };

    private static readonly string[] ConfigRelativePath =
    {
        "workflow",
        "BeamGenerator.Workflow",
        "config.yaml",
    };

    public static PortableWorkflowInstallation Locate()
    {
        var pluginDirectory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)
            ?? throw new InvalidOperationException("无法确定插件发布目录。");
        var configuredRunner = Environment.GetEnvironmentVariable("ETABS_BEAM_WORKFLOW_RUNNER");
        var configuredConfig = Environment.GetEnvironmentVariable("ETABS_BEAM_WORKFLOW_CONFIG");
        var runner = string.IsNullOrWhiteSpace(configuredRunner)
            ? Path.Combine(new[] { pluginDirectory }.Concat(RunnerRelativePath).ToArray())
            : Path.GetFullPath(configuredRunner);
        var config = string.IsNullOrWhiteSpace(configuredConfig)
            ? Path.Combine(new[] { pluginDirectory }.Concat(ConfigRelativePath).ToArray())
            : Path.GetFullPath(configuredConfig);
        var workingDirectory = Path.GetDirectoryName(runner)
            ?? throw new InvalidOperationException("无法确定便携工作流目录。");
        return new PortableWorkflowInstallation(runner, config, workingDirectory);
    }
}
