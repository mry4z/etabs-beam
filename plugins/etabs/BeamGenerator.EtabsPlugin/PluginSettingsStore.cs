using System.Text.Json;

namespace BeamGenerator.EtabsPlugin;

internal static class PluginSettingsStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public static string SettingsPath
    {
        get
        {
            var overridePath = Environment.GetEnvironmentVariable("ETABS_BEAM_PLUGIN_SETTINGS_PATH");
            if (!string.IsNullOrWhiteSpace(overridePath))
            {
                return Path.GetFullPath(overridePath);
            }

            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "ETABS Beam Generator",
                "plugin-settings.json"
            );
        }
    }

    public static PluginSettings Load(out string warning)
    {
        warning = "";
        var defaults = CreateDefaults();
        if (!File.Exists(SettingsPath))
        {
            return defaults;
        }

        try
        {
            var loaded = JsonSerializer.Deserialize<PluginSettings>(File.ReadAllText(SettingsPath), JsonOptions);
            if (loaded is null)
            {
                return defaults;
            }
            if (loaded.SchemaVersion < 2)
            {
                warning = "检测到依赖本机 Python/仓库路径的旧设置，已迁移为随插件发布的便携运行器。";
                defaults.SelectionMode = loaded.SelectionMode;
                defaults.ExportExcel = loaded.ExportExcel;
                return defaults;
            }
            return loaded;
        }
        catch (Exception exception)
        {
            warning = $"设置文件读取失败，将使用默认值：{exception.Message}";
            return defaults;
        }
    }

    public static void Save(PluginSettings settings)
    {
        var directory = Path.GetDirectoryName(SettingsPath)
            ?? throw new InvalidOperationException("无法确定插件设置目录。");
        Directory.CreateDirectory(directory);
        File.WriteAllText(SettingsPath, JsonSerializer.Serialize(settings, JsonOptions));
    }

    private static PluginSettings CreateDefaults()
    {
        var installation = PortableWorkflowLocator.Locate();
        return new PluginSettings
        {
            SchemaVersion = 2,
            ConfigPath = installation.ConfigPath,
            OutputRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                "ETABS Beam Generator",
                "outputs"
            ),
        };
    }
}
