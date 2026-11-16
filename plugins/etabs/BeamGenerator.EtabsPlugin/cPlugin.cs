using ETABSv1;

namespace BeamGenerator.EtabsPlugin;

public sealed class cPlugin : cPluginContract
{
    public int Info(ref string Text)
    {
        Text =
            $"ETABS Beam Generator Plugin v{typeof(cPlugin).Assembly.GetName().Version} (v2)\n"
            + "Tower preflight with selection recovery and portable workflow launcher.\n"
            + "Includes a self-contained workflow runtime; no local Python or repository source is required.\n"
            + "PB/SB workbooks contain static single-sheet results (no formulas, no node).\n"
            + "Review Groups require explicit confirmation; no automatic save, unlock, analysis, design or numbering writeback.";
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
