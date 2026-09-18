using System.Text.Json.Serialization;

namespace BeamGenerator.EtabsPlugin;

internal sealed record FrameSelection(
    string Name,
    string Guid,
    string Label,
    string Story,
    string Section,
    string DesignOrientation,
    IReadOnlyList<string> Diagnostics
)
{
    public bool Complete => Diagnostics.Count == 0;
}

internal sealed record ModelSnapshot(
    int ProcessId,
    string HostVersion,
    int? HostMajorVersion,
    bool SupportedHost,
    string ApiAssemblyVersion,
    string ApiAssemblyPath,
    string ModelPath,
    string ExpectedDurableModelPath,
    string PresentUnits,
    bool ModelLocked,
    int FrameCount,
    int SelectedObjectCount,
    IReadOnlyList<FrameSelection> SelectedFrames,
    IReadOnlyList<string> Diagnostics
)
{
    public bool Complete => Diagnostics.Count == 0 && SelectedFrames.All(frame => frame.Complete);
}

internal sealed record SelectionExportDocument
{
    [JsonPropertyName("schema")]
    public string Schema { get; init; } = "etabs-beam-generator/plugin-selection@1";

    [JsonPropertyName("generated_at")]
    public DateTimeOffset GeneratedAt { get; init; }

    [JsonPropertyName("complete")]
    public bool Complete { get; init; }

    [JsonPropertyName("plugin_version")]
    public string PluginVersion { get; init; } = "";

    [JsonPropertyName("process_id")]
    public int ProcessId { get; init; }

    [JsonPropertyName("host_version")]
    public string HostVersion { get; init; } = "";

    [JsonPropertyName("api_assembly_version")]
    public string ApiAssemblyVersion { get; init; } = "";

    [JsonPropertyName("model_path")]
    public string ModelPath { get; init; } = "";

    [JsonPropertyName("expected_durable_model_path")]
    public string ExpectedDurableModelPath { get; init; } = "";

    [JsonPropertyName("present_units")]
    public string PresentUnits { get; init; } = "";

    [JsonPropertyName("model_locked")]
    public bool ModelLocked { get; init; }

    [JsonPropertyName("frame_count")]
    public int FrameCount { get; init; }

    [JsonPropertyName("selected_object_count")]
    public int SelectedObjectCount { get; init; }

    [JsonPropertyName("selected_frames")]
    public IReadOnlyList<FrameSelection> SelectedFrames { get; init; } = Array.Empty<FrameSelection>();

    [JsonPropertyName("diagnostics")]
    public IReadOnlyList<string> Diagnostics { get; init; } = Array.Empty<string>();
}

internal sealed class PluginSettings
{
    public int SchemaVersion { get; set; }

    public string ConfigPath { get; set; } = "";

    public string OutputRoot { get; set; } = "";

    public string SelectionMode { get; set; } = "selected";

    public bool ExportExcel { get; set; }

    /// <summary>Optional ETABS API DLL override. Empty means use the host assembly path.</summary>
    public string EtabsApiPath { get; set; } = "";

    public bool ExportRebarWorkbook { get; set; }

    public bool ExportBeamResultDxf { get; set; }

    public int DxfTextHeight { get; set; } = 100;
}

internal sealed record WorkflowRunRequest(
    string RunnerExecutable,
    string WorkingDirectory,
    string ConfigPath,
    string EtabsApiPath,
    string OutputDirectory,
    string SelectionMode,
    bool ExportExcel,
    bool ExportRebarWorkbook,
    int EtabsProcessId,
    string ExpectedModelPath,
    bool TowerCheck = false,
    bool TowerPreflightOnly = false,
    bool TowerContinueOnReview = false,
    bool TowerCreateGroups = false,
    string TowerPlanPath = "",
    bool ExportBeamResultDxf = false,
    int DxfTextHeight = 100
);
