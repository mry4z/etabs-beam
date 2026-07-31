using ETABSv1;

namespace BeamGenerator.EtabsPlugin;

public sealed class cPlugin : cPluginContract
{
    public int Info(ref string Text)
    {
        Text =
            "ETABS Beam Generator Plugin v0.3.0\n"
            + "Read-only model/selection inspection and portable workflow launcher.\n"
            + "Includes a self-contained workflow runtime; no local Python or repository source is required.\n"
            + "ETABS API DLL path is user-overridable; rebar workbook exports as formula xlsx (no node).\n"
            + "No model writeback, save, unlock, analysis, or design commands.";
        return 0;
    }

    public void Main(ref cSapModel SapModel, ref cPluginCallback ISapPlugin)
    {
        if (SapModel is null || ISapPlugin is null)
        {
            ISapPlugin?.Finish(1);
            return;
        }

        try
        {
            PluginSession.Start(SapModel, ISapPlugin);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                $"ETABS Beam Generator 插件启动失败。\n\n{exception}",
                "ETABS Beam Generator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
