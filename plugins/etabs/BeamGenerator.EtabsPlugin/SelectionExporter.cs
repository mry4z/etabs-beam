using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace BeamGenerator.EtabsPlugin;

internal static class SelectionExporter
{
    public static void Write(ModelSnapshot snapshot, string outputPath, string pluginVersion)
    {
        ArgumentNullException.ThrowIfNull(snapshot);
        if (string.IsNullOrWhiteSpace(outputPath))
        {
            throw new ArgumentException("选择清单输出路径不能为空。", nameof(outputPath));
        }

        var document = new SelectionExportDocument
        {
            GeneratedAt = DateTimeOffset.Now,
            Complete = snapshot.Complete,
            PluginVersion = pluginVersion,
            ProcessId = snapshot.ProcessId,
            HostVersion = snapshot.HostVersion,
            ApiAssemblyVersion = snapshot.ApiAssemblyVersion,
            ModelPath = snapshot.ModelPath,
            ExpectedDurableModelPath = snapshot.ExpectedDurableModelPath,
            PresentUnits = snapshot.PresentUnits,
            ModelLocked = snapshot.ModelLocked,
            FrameCount = snapshot.FrameCount,
            SelectedObjectCount = snapshot.SelectedObjectCount,
            SelectedFrames = snapshot.SelectedFrames,
            Diagnostics = snapshot.Diagnostics,
        };
        var options = new JsonSerializerOptions
        {
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        };
        File.WriteAllText(
            outputPath,
            JsonSerializer.Serialize(document, options),
            new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)
        );
    }
}

