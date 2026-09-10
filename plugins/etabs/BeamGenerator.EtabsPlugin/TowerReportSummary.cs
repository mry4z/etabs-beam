using System.Text.Json;

namespace BeamGenerator.EtabsPlugin;

internal sealed record TowerReportSummary(
    string Status,
    int CheckedBeams,
    int IssueCount,
    IReadOnlyList<string> Reasons
)
{
    public static TowerReportSummary MissingReport { get; } = new(
        "BLOCKED",
        0,
        0,
        new[] { "未生成 Tower 检查报告。" }
    );

    public static TowerReportSummary Parse(JsonElement report)
    {
        var diagnostics = new List<string>();
        if (report.TryGetProperty("diagnostics", out var diagnosticArray)
            && diagnosticArray.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in diagnosticArray.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.String) continue;
                var value = item.GetString()?.Trim();
                if (!string.IsNullOrWhiteSpace(value) && !IsTelemetry(value))
                    diagnostics.Add(value);
            }
        }

        // A review report normally carries the actionable reason on each
        // record.  Keep that reason visible even when the global diagnostics
        // array only contains timing/audit entries.
        if (report.TryGetProperty("records", out var records)
            && records.ValueKind == JsonValueKind.Array)
        {
            foreach (var record in records.EnumerateArray())
            {
                var status = ReadString(record, "status");
                var reason = ReadString(record, "reason");
                if (!string.Equals(status, "PASS", StringComparison.OrdinalIgnoreCase)
                    && !string.IsNullOrWhiteSpace(reason))
                    diagnostics.Add(reason);
            }
        }

        return new TowerReportSummary(
            ReadString(report, "status") is { Length: > 0 } statusValue ? statusValue : "BLOCKED",
            ReadInt(report, "checked_beams"),
            ReadInt(report, "issue_count"),
            diagnostics.Distinct(StringComparer.Ordinal).Take(3).ToArray()
        );
    }

    public string LogLine =>
        $"状态：{Status}；检查梁 {CheckedBeams:N0}；疑似对象 {IssueCount:N0}。";

    public string StatusLine
    {
        get
        {
            if (string.Equals(Status, "PASS", StringComparison.OrdinalIgnoreCase))
                return $"Tower 检查已通过：检查梁 {CheckedBeams:N0}，疑似对象 {IssueCount:N0}。";

            var label = string.Equals(Status, "REVIEW_REQUIRED", StringComparison.OrdinalIgnoreCase)
                ? "Tower 检查需要复核"
                : "Tower 检查被阻断";
            var reason = Reasons.Count == 0 ? "报告未提供具体原因。" : Reasons[0];
            return $"{label}：{reason}";
        }
    }

    private static bool IsTelemetry(string value) =>
        value.StartsWith("TOWER_READ_TIMING_MS:", StringComparison.Ordinal)
        || value.StartsWith("TOWER_TIMING_MS:", StringComparison.Ordinal)
        || value.StartsWith("SELECTION_RESTORE_AUDIT:", StringComparison.Ordinal);

    private static string ReadString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? ""
            : "";

    private static int ReadInt(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var value) && value.TryGetInt32(out var result)
            ? result
            : 0;
}
