using System.Diagnostics;

namespace BeamGenerator.EtabsPlugin;

internal sealed record WorkflowRunResult(int ExitCode, bool Cancelled);

internal sealed class PythonWorkflowRunner : IDisposable
{
    private readonly object _sync = new();
    private Process? _process;

    public bool IsRunning
    {
        get
        {
            lock (_sync)
            {
                return _process is { HasExited: false };
            }
        }
    }

    public async Task<WorkflowRunResult> RunAsync(
        WorkflowRunRequest request,
        Action<string> output,
        CancellationToken cancellationToken
    )
    {
        var startInfo = BuildStartInfo(request);
        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, eventArgs) =>
        {
            if (eventArgs.Data is not null)
            {
                output(eventArgs.Data);
            }
        };
        process.ErrorDataReceived += (_, eventArgs) =>
        {
            if (eventArgs.Data is not null)
            {
                output($"[stderr] {eventArgs.Data}");
            }
        };

        lock (_sync)
        {
            if (_process is not null)
            {
                throw new InvalidOperationException("已有 Python 工作流正在运行。");
            }

            _process = process;
        }

        var cancelled = false;
        try
        {
            output($"启动：{FormatCommand(startInfo)}");
            if (!process.Start())
            {
                throw new InvalidOperationException("Python 进程未能启动。");
            }

            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            using var registration = cancellationToken.Register(() =>
            {
                cancelled = true;
                TryKill(process);
            });
            await process.WaitForExitAsync(CancellationToken.None);
            return new WorkflowRunResult(cancelled ? 130 : process.ExitCode, cancelled);
        }
        finally
        {
            lock (_sync)
            {
                _process = null;
            }

            process.Dispose();
        }
    }

    public void Cancel()
    {
        Process? process;
        lock (_sync)
        {
            process = _process;
        }

        if (process is not null)
        {
            TryKill(process);
        }
    }

    public void Dispose()
    {
        Cancel();
    }

    private static ProcessStartInfo BuildStartInfo(WorkflowRunRequest request)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = request.RunnerExecutable,
            WorkingDirectory = request.WorkingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("--pid");
        startInfo.ArgumentList.Add(request.EtabsProcessId.ToString());
        startInfo.ArgumentList.Add("--expected-model");
        startInfo.ArgumentList.Add(request.ExpectedModelPath);
        startInfo.ArgumentList.Add("--etabs-api");
        startInfo.ArgumentList.Add(request.EtabsApiPath);
        startInfo.ArgumentList.Add("--config");
        startInfo.ArgumentList.Add(request.ConfigPath);
        startInfo.ArgumentList.Add("--read-only");
        startInfo.ArgumentList.Add("--selection");
        startInfo.ArgumentList.Add(request.SelectionMode);
        startInfo.ArgumentList.Add("--output-dir");
        startInfo.ArgumentList.Add(request.OutputDirectory);
        if (request.ExportExcel)
        {
            startInfo.ArgumentList.Add("--export-excel");
        }

        return startInfo;
    }

    private static string FormatCommand(ProcessStartInfo startInfo)
    {
        static string Quote(string value)
        {
            return value.Any(char.IsWhiteSpace) ? $"\"{value.Replace("\"", "\\\"")}\"" : value;
        }

        return string.Join(" ", new[] { Quote(startInfo.FileName) }.Concat(startInfo.ArgumentList.Select(Quote)));
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
            // The process exited between the state check and Kill.
        }
    }
}
