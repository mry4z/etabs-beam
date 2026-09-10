using BeamGenerator.EtabsPlugin;

internal static class UiRecoveryTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    public static async Task RunAsync()
    {
        var completed = 0;
        foreach (var empty in new[] { false, true })
        foreach (var outcome in new[] { "success", "error", "cancelled" })
        {
            var session = new FakeUiSession();
            if (empty) session.Selection.Clear();
            var expected = session.Selection.ToHashSet();
            try
            {
                await UiSessionCheckpoint.RunAsync(session, () =>
                {
                    session.SetActiveTower("T2");
                    session.Units = 99;
                    session.Selection.Add(new SelectedObject(5, "unexpected"));
                    return outcome == "error" ? Task.FromException<int>(new Exception("child failed"))
                        : Task.FromResult(outcome == "cancelled" ? 130 : 0);
                });
                Check(outcome != "error", "child failure must propagate");
            }
            catch (Exception exception) when (exception.Message == "child failed") { }
            Check(session.ActiveTower == "T1" && session.Units == 5, "restore tower and units");
            Check(expected.SetEquals(session.Selection), "restore full mixed/empty selection");
            if (!empty) Check(session.Events.IndexOf("tower:T1") < session.Events.IndexOf("previous"), "restore Tower before selection");
            // The next selected run still sees the same beam, not an empty set.
            UiSessionCheckpoint.Capture(session).Restore(session);
            Check(expected.SetEquals(session.ReadSelection()), "continuous selected run");
            completed++;
        }
        foreach (var failure in new[] { "tower", "selection", "units", "model" })
        {
            var session = new FakeUiSession();
            var checkpoint = UiSessionCheckpoint.Capture(session);
            session.SetActiveTower("T2");
            session.Units = 99;
            session.Failure = failure;
            if (failure == "model") session.ModelPath = "different.EDB";
            var eventCount = session.Events.Count;
            var rejected = false;
            try { checkpoint.Restore(session); }
            catch (InvalidOperationException) { rejected = true; }
            Check(rejected, $"must block {failure} recovery failure");
            if (failure == "model") Check(session.Events.Count == eventCount, "never write another model");
            completed++;
        }
        var unchanged = new FakeUiSession();
        UiSessionCheckpoint.Capture(unchanged).Restore(unchanged);
        Check(unchanged.Events.Count == 0, "unchanged UI requires no selection mutation");
        completed++;

        var previousMismatch = new FakeUiSession { PreviousSelectionMismatch = true };
        var previousExpected = previousMismatch.Selection.ToHashSet();
        var previousMismatchCheckpoint = UiSessionCheckpoint.Capture(previousMismatch);
        previousMismatch.SetActiveTower("T2");
        previousMismatchCheckpoint.Restore(previousMismatch);
        Check(previousExpected.SetEquals(previousMismatch.Selection), "mismatched PreviousSelection must use exact fallback");
        Check(previousMismatch.Events.Count(item => item == "previous") == 1, "PreviousSelection called once");
        Check(previousMismatch.Events.Count(item => item.StartsWith("select:", StringComparison.Ordinal)) == previousExpected.Count,
            "fallback selection count is bounded by the recovery path");
        completed++;

        var previousError = new FakeUiSession { Failure = "previous" };
        var previousErrorExpected = previousError.Selection.ToHashSet();
        var previousErrorCheckpoint = UiSessionCheckpoint.Capture(previousError);
        previousError.SetActiveTower("T2");
        previousErrorCheckpoint.Restore(previousError);
        Check(previousErrorExpected.SetEquals(previousError.Selection), "nonzero PreviousSelection fallback");
        completed++;

        // A normally exiting child can report that it already completed the
        // full object fallback.  If that fallback was not verified, the host
        // must not silently run a second full loop over the same objects.
        var childFallbackFailed = new FakeUiSession { Failure = "selection" };
        var childFallbackExpected = childFallbackFailed.Selection.ToHashSet();
        var handoffRejected = false;
        try
        {
            await UiSessionCheckpoint.RunAsync(
                childFallbackFailed,
                () =>
                {
                    childFallbackFailed.SetActiveTower("T2");
                    childFallbackFailed.ClearSelection();
                    foreach (var item in childFallbackExpected)
                        childFallbackFailed.Select(item);
                    childFallbackFailed.SetActiveTower("T1");
                    return Task.FromResult(1);
                },
                _ => true);
        }
        catch (InvalidOperationException exception) when (exception.Message.Contains("HANDOFF_BLOCKED"))
        {
            handoffRejected = true;
        }
        Check(handoffRejected, "failed child fallback must remain blocked at the host boundary");
        Check(childFallbackFailed.Events.Count(item => item == "previous") == 0,
            "host must not retry PreviousSelection after a child object fallback");
        Check(childFallbackFailed.Events.Count(item => item == "clear") == 1,
            "host must not repeat ClearSelection after a child object fallback");
        Check(childFallbackFailed.Events.Count(item => item.StartsWith("select:", StringComparison.Ordinal))
            == childFallbackExpected.Count, "host must not repeat per-object selection after a child fallback");
        completed++;

        // A verified child fallback leaves the original active Tower and exact
        // selection in place, so the host accepts the no-op boundary.
        var childFallbackSuccess = new FakeUiSession();
        var childSuccessExpected = childFallbackSuccess.Selection.ToHashSet();
        await UiSessionCheckpoint.RunAsync(
            childFallbackSuccess,
            () =>
            {
                childFallbackSuccess.SetActiveTower("T2");
                childFallbackSuccess.SetActiveTower("T1");
                childFallbackSuccess.ClearSelection();
                foreach (var item in childSuccessExpected)
                    childFallbackSuccess.Select(item);
                return Task.FromResult(0);
            },
            _ => true);
        Check(childSuccessExpected.SetEquals(childFallbackSuccess.Selection), "verified child fallback survives host handoff");
        Check(childFallbackSuccess.Events.Count(item => item == "previous") == 0,
            "verified child fallback must not be replayed by the host");
        Check(childFallbackSuccess.Events.Count(item => item.StartsWith("select:", StringComparison.Ordinal))
            == childSuccessExpected.Count, "verified child fallback has one object loop total");
        completed++;

        var previousModelChange = new FakeUiSession { ChangeModelAfterPrevious = true };
        var previousModelCheckpoint = UiSessionCheckpoint.Capture(previousModelChange);
        previousModelChange.Selection.Clear();
        var previousModelRejected = false;
        try { previousModelCheckpoint.Restore(previousModelChange); }
        catch (UiSessionModelIdentityException) { previousModelRejected = true; }
        Check(previousModelRejected, "model change during PreviousSelection must abort immediately");
        Check(previousModelChange.Events.Count(item => item == "clear") == 0
            && previousModelChange.Events.Count(item => item.StartsWith("select:", StringComparison.Ordinal)) == 0,
            "model change during PreviousSelection must not enter object fallback");
        completed++;

        var objectModelChange = new FakeUiSession
        {
            PreviousSelectionMismatch = true,
            ChangeModelAfterSelect = true,
        };
        var objectModelCheckpoint = UiSessionCheckpoint.Capture(objectModelChange);
        objectModelChange.Selection.Clear();
        var objectModelRejected = false;
        try { objectModelCheckpoint.Restore(objectModelChange); }
        catch (UiSessionModelIdentityException) { objectModelRejected = true; }
        Check(objectModelRejected, "model change after first object restore must abort immediately");
        Check(objectModelChange.Events.Count(item => item == "clear") == 1
            && objectModelChange.Events.Count(item => item.StartsWith("select:", StringComparison.Ordinal)) == 1,
            "model change after first object restore must not select the remaining objects");
        completed++;

        var finalReadModelChange = new FakeUiSession();
        var finalReadCheckpoint = UiSessionCheckpoint.Capture(finalReadModelChange);
        finalReadModelChange.Selection.Clear();
        finalReadModelChange.ChangeModelAfterNextRead = true;
        var finalReadRejected = false;
        try { finalReadCheckpoint.Restore(finalReadModelChange); }
        catch (UiSessionModelIdentityException) { finalReadRejected = true; }
        Check(finalReadRejected, "model change after final selection read must not be accepted");
        Check(finalReadModelChange.Events.Count == 0,
            "final-read identity change must not trigger another selection mutation");
        completed++;

        var unsupported = new FakeUiSession();
        unsupported.Selection.Add(new SelectedObject(99, "unknown"));
        var invoked = false;
        try { await UiSessionCheckpoint.RunAsync(unsupported, () => { invoked = true; return Task.FromResult(0); }); }
        catch (InvalidOperationException) { }
        Check(!invoked, "unsupported selection blocks before starting the child");
        completed++;

        // Exercise the actual process runner's hard-cancel path, then the same
        // finally-protected checkpoint wrapper used by MainForm.
        using var runner = new PythonWorkflowRunner();
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var cancelledSession = new FakeUiSession();
        var request = new WorkflowRunRequest(Environment.ProcessPath!, Environment.CurrentDirectory,
            "config.yaml", "unused.dll", "output", "selected", false, false, 123, "model.EDB", TowerCheck: true);
        var cancelled = await UiSessionCheckpoint.RunAsync(cancelledSession, () => runner.RunAsync(request, line =>
        {
            if (line != "OFFLINE_CHILD_READY") return;
            cancelledSession.SetActiveTower("T2");
            cancelledSession.Units = 99;
            cancellation.Cancel();
        }, cancellation.Token));
        Check(cancelled.Cancelled && cancelled.ExitCode == 130 && !runner.IsRunning, "child must exit before recovery");
        Check(cancelledSession.ActiveTower == "T1" && cancelledSession.Units == 5 && cancelledSession.Selection.Count == 3,
            "host recovers after actual hard-killed child");
        completed++;
        Console.WriteLine($"PASS: {completed} offline UI recovery cases (including actual child-process cancellation).");
        TestReviewScope();
    }

    private static void TestReviewScope()
    {
        var beams = new[] { new FrameSelection("B1", "G1", "B1", "L1", "B", "Beam", Array.Empty<string>()) };
        var model = new ModelSnapshot(123, "23", 23, true, "2.16", "API.dll", "model.EDB", "model.EDB",
            "N_mm_C", false, 20, 3, beams, Array.Empty<string>());
        var initial = TowerReviewScope.From(model, "selected", 1, 100);
        Check(initial == TowerReviewScope.From(model with { PresentUnits = "other" }, "selected", 1, 100), "units do not change beam scope");
        Check(initial != TowerReviewScope.From(model with { ProcessId = 456 }, "selected", 1, 100), "PID invalidates review");
        Check(initial != TowerReviewScope.From(model with { ExpectedDurableModelPath = "other.EDB" }, "selected", 1, 100), "model invalidates review");
        Check(initial != TowerReviewScope.From(model, "all", 1, 100), "selection mode invalidates review");
        Check(initial != TowerReviewScope.From(model, "selected", 2, 100), "saved model invalidates review");
        Check(initial != TowerReviewScope.From(model with { SelectedFrames = new[] { beams[0] with { Name = "B2" } } }, "selected", 1, 100), "same-count changed selection invalidates review");
        Check(initial != TowerReviewScope.From(model with { SelectedFrames = new[] { beams[0] with { Guid = "G2" } } }, "selected", 1, 100), "changed GUID invalidates review");
        Check(TowerReviewScope.From(model, "all", 1, 100) == TowerReviewScope.From(model with { SelectedFrames = Array.Empty<FrameSelection>() }, "all", 1, 100), "all scope is independent of UI selection");
        Console.WriteLine("PASS: 8 tower-review scope checks.");
    }

    private sealed class FakeUiSession : IUiSession
    {
        public string ModelPath { get; set; } = "model.EDB";
        public string ActiveTower { get; private set; } = "T1";
        public int Units = 5;
        public int PresentUnits => Units;
        public string Failure = "";
        public HashSet<SelectedObject> Selection = new() { new(1, "P1"), new(2, "B1"), new(5, "A1") };
        public bool PreviousSelectionMismatch;
        public bool ChangeModelAfterPrevious;
        public bool ChangeModelAfterSelect;
        public bool ChangeModelAfterNextRead;
        public List<string> Events = new();
        private HashSet<SelectedObject> Previous = new();
        private bool HasPrevious;
        public IReadOnlyList<SelectedObject> ReadSelection()
        {
            var result = Selection.ToArray();
            if (ChangeModelAfterNextRead)
            {
                ChangeModelAfterNextRead = false;
                ModelPath = "different.EDB";
            }
            return result;
        }
        public bool CanSelect(int type) => type is >= 1 and <= 7;
        public void SetActiveTower(string tower)
        {
            Events.Add("tower:" + tower);
            if (Failure == "tower") throw new InvalidOperationException("tower API failed");
            if (!HasPrevious)
            {
                Previous = Selection.ToHashSet();
                HasPrevious = true;
            }
            ActiveTower = tower;
            Selection.Clear();
        }
        public void SetPresentUnits(int units) { if (Failure != "units") Units = units; }
        public void PreviousSelection()
        {
            Events.Add("previous");
            if (Failure is "selection" or "previous") throw new InvalidOperationException("PreviousSelection API failed");
            Selection = (PreviousSelectionMismatch ? new HashSet<SelectedObject> { new(2, "wrong") } : Previous.ToHashSet());
            if (ChangeModelAfterPrevious) ModelPath = "different.EDB";
        }
        public void ClearSelection() { Events.Add("clear"); Selection.Clear(); }
        public void Select(SelectedObject item)
        {
            Events.Add("select:" + item.Name);
            if (Failure != "selection") Selection.Add(item);
            if (ChangeModelAfterSelect) ModelPath = "different.EDB";
        }
    }
}
