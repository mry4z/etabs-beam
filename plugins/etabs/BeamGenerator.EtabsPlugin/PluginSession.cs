using ETABSv1;

namespace BeamGenerator.EtabsPlugin;

internal static class PluginSession
{
    private static MainForm? _activeForm;

    public static void Start(cSapModel sapModel, cPluginCallback callback)
    {
        if (_activeForm is { IsDisposed: false })
        {
            _activeForm.Activate();
            callback.Finish(0);
            return;
        }

        var completion = new PluginCompletion(callback);
        try
        {
            var form = new MainForm(sapModel);
            _activeForm = form;
            form.FormClosed += (_, _) =>
            {
                _activeForm = null;
                completion.Finish(form.CompletionErrorFlag);
                form.Dispose();
            };
            form.Show();
        }
        catch
        {
            _activeForm = null;
            completion.Finish(1);
            throw;
        }
    }

    private sealed class PluginCompletion
    {
        private cPluginCallback? _callback;

        public PluginCompletion(cPluginCallback callback)
        {
            _callback = callback;
        }

        public void Finish(int errorFlag)
        {
            var callback = Interlocked.Exchange(ref _callback, null);
            callback?.Finish(errorFlag);
        }
    }
}

