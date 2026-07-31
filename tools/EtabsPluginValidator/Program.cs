using System.Diagnostics;
using System.Reflection;
using System.Reflection.PortableExecutable;
using System.Runtime.Loader;
using System.Text.Json;

if (args.Length != 2)
{
    Console.Error.WriteLine("Usage: EtabsPluginValidator <plugin.dll> <ETABSv1.dll>");
    return 2;
}

var pluginPath = Path.GetFullPath(args[0]);
var apiPath = Path.GetFullPath(args[1]);
if (!File.Exists(pluginPath) || !File.Exists(apiPath))
{
    Console.Error.WriteLine($"Missing input: plugin={pluginPath}, api={apiPath}");
    return 2;
}

var apiAssembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(apiPath);
AssemblyLoadContext.Default.Resolving += (_, name) =>
    name.Name == apiAssembly.GetName().Name ? apiAssembly : null;
var pluginAssembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(pluginPath);
var expectedPluginTypeName = $"{pluginAssembly.GetName().Name}.cPlugin";
var pluginType = pluginAssembly.GetType(expectedPluginTypeName, throwOnError: true)
    ?? throw new InvalidOperationException(
        $"ETABS loader entry type {expectedPluginTypeName} is missing."
    );
var contractType = apiAssembly.GetType("ETABSv1.cPluginContract", throwOnError: true)
    ?? throw new InvalidOperationException("ETABSv1.cPluginContract is missing.");
var sapType = apiAssembly.GetType("ETABSv1.cSapModel", throwOnError: true)
    ?? throw new InvalidOperationException("ETABSv1.cSapModel is missing.");
var callbackType = apiAssembly.GetType("ETABSv1.cPluginCallback", throwOnError: true)
    ?? throw new InvalidOperationException("ETABSv1.cPluginCallback is missing.");

Require(pluginType.IsPublic && !pluginType.IsAbstract, "cPlugin must be a public concrete type.");
Require(pluginType.GetConstructor(Type.EmptyTypes) is not null, "cPlugin must have a public parameterless constructor.");
Require(contractType.IsAssignableFrom(pluginType), "cPlugin must implement ETABSv1.cPluginContract.");
Require(
    Activator.CreateInstance(pluginType) is not null,
    $"Activator could not create ETABS loader entry type {expectedPluginTypeName}."
);

var info = pluginType.GetMethod("Info", BindingFlags.Instance | BindingFlags.Public);
Require(info is not null && info.ReturnType == typeof(int), "Info must return Int32.");
RequireParameters(info!, typeof(string).MakeByRefType());

var main = pluginType.GetMethod("Main", BindingFlags.Instance | BindingFlags.Public);
Require(main is not null && main.ReturnType == typeof(void), "Main must return Void.");
RequireParameters(main!, sapType.MakeByRefType(), callbackType.MakeByRefType());

var copiedApi = Path.Combine(Path.GetDirectoryName(pluginPath)!, "ETABSv1.dll");
Require(!File.Exists(copiedApi), "Published plugin directory must not contain ETABSv1.dll.");
using var pluginStream = File.OpenRead(pluginPath);
using var peReader = new PEReader(pluginStream);
var machine = peReader.PEHeaders.CoffHeader.Machine;
Require(machine == Machine.Amd64, $"Plugin must target x64; PE machine is {machine}.");

var targetFramework = pluginAssembly
    .GetCustomAttribute<System.Runtime.Versioning.TargetFrameworkAttribute>()
    ?.FrameworkName;
Require(
    targetFramework?.Contains(".NETCoreApp,Version=v8.0", StringComparison.Ordinal) == true,
    $"Unexpected target framework: {targetFramework}"
);

var result = new
{
    verdict = "passed",
    plugin_path = pluginPath,
    plugin_assembly = pluginAssembly.GetName().Name,
    plugin_version = pluginAssembly.GetName().Version?.ToString(),
    target_framework = targetFramework,
    pe_machine = machine.ToString(),
    etabs_loader_type_name = expectedPluginTypeName,
    plugin_type = pluginType.FullName,
    contract = contractType.FullName,
    api_path = apiPath,
    api_file_version = FileVersionInfo.GetVersionInfo(apiPath).FileVersion,
    copied_etabsv1 = false,
};
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
return 0;

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

static void RequireParameters(MethodInfo method, params Type[] expected)
{
    var actual = method.GetParameters().Select(parameter => parameter.ParameterType).ToArray();
    if (!actual.SequenceEqual(expected))
    {
        throw new InvalidOperationException(
            $"{method.Name} parameter mismatch: actual=[{string.Join(", ", actual.Select(type => type.FullName))}]"
        );
    }
}
