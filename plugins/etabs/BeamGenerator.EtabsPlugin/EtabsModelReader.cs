using System.Diagnostics;
using ETABSv1;

namespace BeamGenerator.EtabsPlugin;

internal sealed class EtabsApiException : InvalidOperationException
{
    public EtabsApiException(string message)
        : base(message)
    {
    }
}

internal static class EtabsApi
{
    public static void Check(int returnCode, string context)
    {
        if (returnCode != 0)
        {
            throw new EtabsApiException($"{context}: ETABS API 返回错误码 {returnCode}。");
        }
    }
}

internal sealed class EtabsModelReader
{
    private static readonly HashSet<int> SupportedHostMajors = new() { 22, 23 };
    private readonly cSapModel _sapModel;

    public EtabsModelReader(cSapModel sapModel)
    {
        _sapModel = sapModel ?? throw new ArgumentNullException(nameof(sapModel));
    }

    public ModelSnapshot Read()
    {
        var diagnostics = new List<string>();
        var hostVersion = "";
        var hostVersionNumber = 0.0;
        EtabsApi.Check(_sapModel.GetVersion(ref hostVersion, ref hostVersionNumber), "SapModel.GetVersion");

        var hostMajor = ParseMajor(hostVersion);
        var supportedHost = hostMajor is not null && SupportedHostMajors.Contains(hostMajor.Value);
        if (!supportedHost)
        {
            diagnostics.Add($"UNSUPPORTED_ETABS_HOST: {hostVersion}");
        }

        // CSI documents these as genuine direct-return methods rather than status-code calls.
        var modelPath = Convert.ToString(_sapModel.GetModelFilename(true)) ?? "";
        var presentUnits = Convert.ToString(_sapModel.GetPresentUnits()) ?? "";
        var modelLocked = _sapModel.GetModelIsLocked();

        if (string.IsNullOrWhiteSpace(modelPath))
        {
            diagnostics.Add("MODEL_PATH_EMPTY: 当前 ETABS 模型尚未保存为可识别文件。");
        }

        var frameCount = 0;
        var frameNames = Array.Empty<string>();
        EtabsApi.Check(_sapModel.FrameObj.GetNameList(ref frameCount, ref frameNames), "FrameObj.GetNameList");
        ValidateArrayLength(frameCount, frameNames, "FrameObj.GetNameList");

        var selectedObjectCount = 0;
        var selectedTypes = Array.Empty<int>();
        var selectedNames = Array.Empty<string>();
        EtabsApi.Check(
            _sapModel.SelectObj.GetSelected(ref selectedObjectCount, ref selectedTypes, ref selectedNames),
            "SelectObj.GetSelected"
        );
        ValidateArrayLength(selectedObjectCount, selectedTypes, "SelectObj.GetSelected(types)");
        ValidateArrayLength(selectedObjectCount, selectedNames, "SelectObj.GetSelected(names)");

        var selectedFrames = new List<FrameSelection>();
        for (var index = 0; index < selectedObjectCount; index++)
        {
            if (selectedTypes[index] != 2)
            {
                continue;
            }

            selectedFrames.Add(ReadFrame(selectedNames[index]));
        }

        var apiAssemblyPath = typeof(cSapModel).Assembly.Location;
        var apiFileVersion = FileVersionInfo.GetVersionInfo(apiAssemblyPath).FileVersion ?? "unknown";
        return new ModelSnapshot(
            HostProcessIdentity.EtabsProcessId,
            hostVersion,
            hostMajor,
            supportedHost,
            apiFileVersion,
            apiAssemblyPath,
            modelPath,
            DurableModelPath(modelPath),
            presentUnits,
            modelLocked,
            frameCount,
            selectedObjectCount,
            selectedFrames,
            diagnostics
        );
    }

    private FrameSelection ReadFrame(string name)
    {
        var diagnostics = new List<string>();
        var guid = ReadString(
            name,
            "FrameObj.GetGUID",
            (ref string value) => _sapModel.FrameObj.GetGUID(name, ref value),
            diagnostics
        );

        var label = "";
        var story = "";
        try
        {
            EtabsApi.Check(
                _sapModel.FrameObj.GetLabelFromName(name, ref label, ref story),
                $"FrameObj.GetLabelFromName({name})"
            );
        }
        catch (Exception exception)
        {
            diagnostics.Add(exception.Message);
        }

        var section = "";
        var autoSection = "";
        try
        {
            EtabsApi.Check(
                _sapModel.FrameObj.GetSection(name, ref section, ref autoSection),
                $"FrameObj.GetSection({name})"
            );
        }
        catch (Exception exception)
        {
            diagnostics.Add(exception.Message);
        }

        var orientation = eFrameDesignOrientation.Null;
        try
        {
            EtabsApi.Check(
                _sapModel.FrameObj.GetDesignOrientation(name, ref orientation),
                $"FrameObj.GetDesignOrientation({name})"
            );
        }
        catch (Exception exception)
        {
            diagnostics.Add(exception.Message);
        }

        return new FrameSelection(name, guid, label, story, section, orientation.ToString(), diagnostics);
    }

    private delegate int RefStringCall(ref string value);

    private static string ReadString(
        string objectName,
        string methodName,
        RefStringCall call,
        ICollection<string> diagnostics
    )
    {
        var value = "";
        try
        {
            EtabsApi.Check(call(ref value), $"{methodName}({objectName})");
        }
        catch (Exception exception)
        {
            diagnostics.Add(exception.Message);
        }

        return value;
    }

    private static int? ParseMajor(string version)
    {
        var first = version.Split('.', 2)[0];
        return int.TryParse(first, out var major) ? major : null;
    }

    private static void ValidateArrayLength<T>(int expected, T[]? values, string context)
    {
        var actual = values?.Length ?? 0;
        if (expected < 0 || actual != expected)
        {
            throw new EtabsApiException($"{context}: 报告数量 {expected}，数组长度 {actual}。");
        }
    }

    internal static string DurableModelPath(string modelPath)
    {
        if (string.IsNullOrWhiteSpace(modelPath))
        {
            return "";
        }

        var extension = Path.GetExtension(modelPath);
        return extension.Equals(".$et", StringComparison.OrdinalIgnoreCase)
            ? Path.ChangeExtension(modelPath, ".EDB")
            : modelPath;
    }
}

internal static class HostProcessIdentity
{
    public static int EtabsProcessId
    {
        get
        {
            var executable = Path.GetFileNameWithoutExtension(Environment.ProcessPath) ?? "";
            if (executable.Equals("ETABS", StringComparison.OrdinalIgnoreCase))
            {
                return Environment.ProcessId;
            }

            var smokePid = Environment.GetEnvironmentVariable("ETABS_BEAM_PLUGIN_SMOKE_HOST_PID");
            return int.TryParse(smokePid, out var processId) && processId > 0 ? processId : Environment.ProcessId;
        }
    }
}
