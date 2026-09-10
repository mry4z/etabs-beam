using ETABSv1;

namespace BeamGenerator.EtabsPlugin;

internal sealed class EtabsUiSession(cSapModel sap) : IUiSession
{
    public string ModelPath => Path.GetFullPath(EtabsModelReader.DurableModelPath(sap.GetModelFilename(true)));
    public int PresentUnits => (int)sap.GetPresentUnits();
    public string ActiveTower
    {
        get
        {
            var tower = "";
            EtabsApi.Check(sap.Tower.GetActiveTower(ref tower), "Tower.GetActiveTower");
            if (string.IsNullOrWhiteSpace(tower)) throw new EtabsApiException("活动塔名称为空。");
            return tower;
        }
    }

    public IReadOnlyList<SelectedObject> ReadSelection()
    {
        var count = 0;
        var types = Array.Empty<int>();
        var names = Array.Empty<string>();
        EtabsApi.Check(sap.SelectObj.GetSelected(ref count, ref types, ref names), "SelectObj.GetSelected");
        if (count < 0 || (types?.Length ?? 0) != count || (names?.Length ?? 0) != count)
            throw new EtabsApiException("选择集返回数组长度不一致。");
        return Enumerable.Range(0, count).Select(index => new SelectedObject(types![index], names![index])).ToArray();
    }

    public bool CanSelect(int objectType) => objectType is 1 or 2 or 4 or 5 or 7;
    public void SetActiveTower(string tower) => EtabsApi.Check(sap.Tower.SetActiveTower(tower), "Tower.SetActiveTower(restore)");
    public void SetPresentUnits(int units) => EtabsApi.Check(sap.SetPresentUnits((eUnits)units), "SetPresentUnits(restore)");
    public void PreviousSelection() => EtabsApi.Check(sap.SelectObj.PreviousSelection(), "SelectObj.PreviousSelection");
    public void ClearSelection() => EtabsApi.Check(sap.SelectObj.ClearSelection(), "SelectObj.ClearSelection(restore)");
    public void Select(SelectedObject item)
    {
        var code = item.Type switch
        {
            1 => sap.PointObj.SetSelected(item.Name, true, eItemType.Objects),
            2 => sap.FrameObj.SetSelected(item.Name, true, eItemType.Objects),
            4 => sap.TendonObj.SetSelected(item.Name, true, eItemType.Objects),
            5 => sap.AreaObj.SetSelected(item.Name, true, eItemType.Objects),
            7 => sap.LinkObj.SetSelected(item.Name, true, eItemType.Objects),
            _ => throw new EtabsApiException($"不支持恢复选择类型 {item.Type}。"),
        };
        EtabsApi.Check(code, $"SetSelected(type={item.Type}, name={item.Name})");
    }
}
