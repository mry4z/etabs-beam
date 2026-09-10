using System.Text.Json;

namespace BeamGenerator.EtabsPlugin;

internal static class TowerReviewScope
{
    public static string From(ModelSnapshot snapshot, string mode, long modifiedTicks, long modelLength) =>
        JsonSerializer.Serialize(new
        {
            snapshot.ProcessId,
            Model = Path.GetFullPath(snapshot.ExpectedDurableModelPath).ToUpperInvariant(),
            Mode = mode,
            ModifiedTicks = modifiedTicks,
            ModelLength = modelLength,
            snapshot.FrameCount,
            Frames = mode == "selected" ? snapshot.SelectedFrames.OrderBy(frame => frame.Name, StringComparer.Ordinal)
                .Select(frame => new { frame.Name, frame.Guid }).ToArray() : null,
        });
}
