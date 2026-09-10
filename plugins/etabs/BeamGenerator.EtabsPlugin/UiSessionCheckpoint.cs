using System.Diagnostics;

namespace BeamGenerator.EtabsPlugin;

internal sealed record SelectedObject(int Type, string Name);

internal interface IUiSession
{
    string ModelPath { get; }
    string ActiveTower { get; }
    int PresentUnits { get; }
    IReadOnlyList<SelectedObject> ReadSelection();
    bool CanSelect(int objectType);
    void SetActiveTower(string tower);
    void SetPresentUnits(int units);
    void PreviousSelection();
    void ClearSelection();
    void Select(SelectedObject item);
}

internal sealed class UiSessionModelIdentityException : InvalidOperationException
{
    public UiSessionModelIdentityException(string message) : base(message) { }
}

/// <summary>Host-side recovery after the child exits, including forced cancellation.</summary>
internal sealed record UiSessionCheckpoint(string ModelPath, string Tower, int Units, IReadOnlyList<SelectedObject> Selection)
{
    public static async Task<T> RunAsync<T>(
        IUiSession session,
        Func<Task<T>> operation,
        Func<T, bool>? childAttemptedCompleteObjectFallback = null,
        Action<TimeSpan>? recoveryTiming = null)
    {
        var checkpoint = Capture(session);
        var childAttemptedFallback = false;
        T result = default!;
        var completed = false;
        try
        {
            result = await operation();
            completed = true;
            childAttemptedFallback = childAttemptedCompleteObjectFallback?.Invoke(result) == true;
            return result;
        }
        finally
        {
            var recoveryStarted = Stopwatch.GetTimestamp();
            try
            {
                checkpoint.Restore(session, allowObjectFallback: !completed || !childAttemptedFallback);
            }
            finally
            {
                recoveryTiming?.Invoke(Stopwatch.GetElapsedTime(recoveryStarted));
            }
        }
    }

    public static UiSessionCheckpoint Capture(IUiSession session)
    {
        string modelBefore;
        try { modelBefore = session.ModelPath; }
        catch (Exception exception)
        {
            throw new UiSessionModelIdentityException(
                $"UI_RESTORE_MODEL_IDENTITY_READ_FAILED：无法读取初始模型身份：{exception.Message}");
        }
        var selected = session.ReadSelection().ToArray();
        if (selected.Any(item => !session.CanSelect(item.Type) || string.IsNullOrWhiteSpace(item.Name))
            || selected.Distinct().Count() != selected.Length)
            throw new InvalidOperationException("选择集包含不可恢复的对象，拒绝启动分塔检查。");
        string modelAfter;
        try { modelAfter = session.ModelPath; }
        catch (Exception exception)
        {
            throw new UiSessionModelIdentityException(
                $"UI_RESTORE_MODEL_IDENTITY_READ_FAILED：无法读取结束模型身份：{exception.Message}");
        }
        if (!string.Equals(modelBefore, modelAfter, StringComparison.OrdinalIgnoreCase))
            throw new UiSessionModelIdentityException(
                "UI_RESTORE_MODEL_CHANGED：捕获选择集期间模型已改变，拒绝启动分塔检查。");
        return new UiSessionCheckpoint(modelBefore, session.ActiveTower, session.PresentUnits, selected);
    }

    public void Restore(IUiSession session, bool allowObjectFallback = true)
    {
        void VerifyModel()
        {
            string currentPath;
            try { currentPath = session.ModelPath; }
            catch (Exception exception)
            {
                throw new UiSessionModelIdentityException(
                    $"UI_RESTORE_MODEL_IDENTITY_READ_FAILED：无法读取当前模型身份：{exception.Message}");
            }
            if (!string.Equals(ModelPath, currentPath, StringComparison.OrdinalIgnoreCase))
                throw new UiSessionModelIdentityException(
                    "UI_RESTORE_MODEL_CHANGED：当前模型已改变，拒绝向另一模型恢复会话状态。");
        }

        VerifyModel();
        var errors = new List<string>();
        void Attempt(Action restore)
        {
            try { restore(); }
            catch (UiSessionModelIdentityException) { throw; }
            catch (Exception exception) { errors.Add(exception.Message); }
        }
        void GuardedWrite(Action write)
        {
            VerifyModel();
            try { write(); }
            catch (Exception)
            {
                // A COM call may change the active model before reporting an
                // error.  Re-read identity before any caller can downgrade the
                // failure to an ordinary recovery error.
                VerifyModel();
                throw;
            }
            VerifyModel();
        }
        // Tower switches can clear selection, so they must precede re-selection.
        Attempt(() =>
        {
            VerifyModel();
            if (session.ActiveTower != Tower) GuardedWrite(() => session.SetActiveTower(Tower));
            VerifyModel();
            if (session.ActiveTower != Tower) throw new InvalidOperationException("活动塔恢复校验失败。");
        });
        Attempt(() =>
        {
            VerifyModel();
            if (session.PresentUnits != Units) GuardedWrite(() => session.SetPresentUnits(Units));
            VerifyModel();
            if (session.PresentUnits != Units) throw new InvalidOperationException("显示单位恢复校验失败。");
        });
        var matches = false;
        bool SelectionMatches()
        {
            VerifyModel();
            IReadOnlyList<SelectedObject> actual;
            try { actual = session.ReadSelection(); }
            catch (Exception)
            {
                VerifyModel();
                throw;
            }
            VerifyModel();
            return actual.Count == Selection.Count && new HashSet<SelectedObject>(Selection).SetEquals(actual);
        }
        try { matches = SelectionMatches(); }
        catch (UiSessionModelIdentityException) { throw; }
        catch (Exception) { /* Recover from the validated original checkpoint below. */ }
        if (!matches)
        {
            if (!allowObjectFallback)
            {
                errors.Add(
                    "UI_RESTORE_SELECTION_HANDOFF_BLOCKED：子进程已完成一次完整逐对象恢复但未通过校验；"
                    + "宿主不重复恢复，请在 ETABS 中人工核对选择集。");
            }
            else
            {
                // ETABS can restore its previous selection in one public API call,
                // but that history is not assumed to be this checkpoint.  Accept
                // it only after comparing the complete (type, name) set.
                var previousSucceeded = false;
                try
                {
                    GuardedWrite(() => session.PreviousSelection());
                    previousSucceeded = true;
                }
                catch (UiSessionModelIdentityException) { throw; }
                catch (Exception)
                {
                    // The checked object fallback below remains available.
                }
                if (previousSucceeded)
                {
                    try { matches = SelectionMatches(); }
                    catch (UiSessionModelIdentityException) { throw; }
                    catch (Exception) { matches = false; }
                }
                if (!matches)
                {
                    var cleared = false;
                    Attempt(() =>
                    {
                        GuardedWrite(() => session.ClearSelection());
                        cleared = true;
                    });
                    if (cleared)
                        foreach (var item in Selection)
                            Attempt(() =>
                            {
                                GuardedWrite(() => session.Select(item));
                            });
                }
            }
        }
        Attempt(() =>
        {
            if (!SelectionMatches())
                throw new InvalidOperationException("选择集恢复校验失败，请在 ETABS 中人工核对后重新检查。");
        });
        if (errors.Count > 0) throw new InvalidOperationException("UI_RESTORE_FAILED: " + string.Join("; ", errors));
    }
}
