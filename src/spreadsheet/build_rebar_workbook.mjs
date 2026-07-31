import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewDir) {
  throw new Error(
    "usage: node build_rebar_workbook.mjs <payload.json> <output.xlsx> <preview-dir>",
  );
}
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
for (const key of ["metadata", "raw_rows", "review_rows", "final_rows"]) {
  if (!(key in payload)) throw new Error(`payload missing ${key}`);
}

const workbook = Workbook.create();
const rawSheet = workbook.worksheets.add("原始分跨数据");
const reviewSheet = workbook.worksheets.add("配筋调整复核");
const finalSheet = workbook.worksheets.add("整理梁表");

const colors = {
  navy: "#17365D",
  blue: "#1F4E78",
  lightBlue: "#DDEBF7",
  input: "#FFF2CC",
  formula: "#E2F0D9",
  warning: "#FCE4D6",
  white: "#FFFFFF",
  gray: "#F2F2F2",
  border: "#B4C6E7",
  ok: "#C6E0B4",
  ng: "#F4CCCC",
};

function colLetter(index) {
  let n = index + 1;
  let value = "";
  while (n > 0) {
    const remainder = (n - 1) % 26;
    value = String.fromCharCode(65 + remainder) + value;
    n = Math.floor((n - 1) / 26);
  }
  return value;
}

function colIndex(label) {
  return [...label].reduce(
    (value, character) => value * 26 + character.charCodeAt(0) - 64,
    0,
  ) - 1;
}

function styleTitle(sheet, endCol, title, subtitle, metadata) {
  // Very wide audit sheets can render a merge that extends far beyond the
  // preview as an empty partial range. Keep the title panel compact and
  // readable while the data table remains full width.
  const titleEnd = colLetter(Math.min(colIndex(endCol), colIndex("R")));
  sheet.mergeCells(`A1:${titleEnd}1`);
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${titleEnd}1`).format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 16 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  sheet.getRange("A1").format.rowHeight = 28;
  sheet.mergeCells(`A2:${titleEnd}2`);
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${titleEnd}2`).format = {
    fill: colors.lightBlue,
    font: { color: colors.navy, size: 10 },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("A2").format.rowHeight = 32;
  sheet.mergeCells(`A3:${titleEnd}3`);
  sheet.getRange("A3").values = [[metadata]];
  sheet.getRange(`A3:${titleEnd}3`).format = {
    fill: colors.gray,
    font: { color: "#595959", size: 9 },
    wrapText: true,
  };
  sheet.getRange("A3").format.rowHeight = 32;
}

function styleHeader(range) {
  range.format = {
    fill: colors.blue,
    font: { bold: true, color: colors.white, size: 9 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: colors.border },
  };
  range.format.rowHeight = 34;
}

function rebarAreaFormula(cell) {
  const rawToken = `LEFT(TRIM(${cell})&" ",FIND(" ",TRIM(${cell})&" ")-1)`;
  const token = `IFERROR(LEFT(${rawToken},FIND("+(",${rawToken})-1),${rawToken})`;
  const first = `IFERROR(VALUE(LEFT(${token},FIND("T",${token})-1))*PI()*VALUE(MID(${token},FIND("T",${token})+1,FIND("+",${token}&"+")-FIND("T",${token})-1))^2/4,0)`;
  const second = `IFERROR(VALUE(MID(${token},FIND("+",${token})+1,FIND("T",${token},FIND("+",${token})+1)-FIND("+",${token})-1))*PI()*VALUE(MID(${token},FIND("T",${token},FIND("+",${token})+1)+1,LEN(${token})))^2/4,0)`;
  return `=IF(OR(TRIM(${cell})="",UPPER(TRIM(${cell}))="N.A."),0,${first}+${second})`;
}

function stirrupAtFormula(cell) {
  return `=IFERROR(PI()*VALUE(MID(${cell},2,FIND("@",${cell})-2))^2/4/VALUE(MID(${cell},FIND("@",${cell})+1,99)),0)`;
}

function addStatusFormats(range) {
  range.conditionalFormats.add("containsText", {
    text: "OK",
    format: { fill: colors.ok, font: { color: "#375623", bold: true } },
  });
  range.conditionalFormats.add("containsText", {
    text: "NG",
    format: { fill: colors.ng, font: { color: "#9C0006", bold: true } },
  });
  range.conditionalFormats.add("containsText", {
    text: "ENGINE",
    format: { fill: colors.warning, font: { color: "#9C5700", bold: true } },
  });
}

const m = payload.metadata;
const coverText =
  m.cover_calculation_basis === "ETABS_LONGITUDINAL_CENTROID"
    ? "保护层计算口径：ETABS CoverTop/CoverBot（混凝土面至纵筋重心，逐截面取控制值）"
    : `箍筋外缘净保护层：${m.clear_cover_to_stirrup_outer_mm ?? "未确认"} mm` +
      `（${m.clear_cover_source || "来源未记录"}）`;
const metadataText =
  `模型：${m.model}｜单位：${m.units}｜规范：${m.design_code}` +
  `｜梁 Object：${m.frame_objects}｜梁线：${m.beam_lines}｜结构跨：${m.spans}` +
  `｜Profile 来源：${m.detailing_profile_source ?? m.is_smf_source ?? "未记录"}` +
  `｜最大骨料：${m.max_aggregate_size_mm ?? "未确认"} mm` +
  `（${m.max_aggregate_size_source || "来源未记录"}）` +
  `｜多排纵筋每排≥${m.minimum_bars_per_layer ?? 2}根` +
  `｜受弯主筋≥T${m.minimum_main_diameter_mm ?? 16}` +
  `｜架立筋T${m.hanger_diameter_mm ?? 12}` +
  `｜${coverText}`;

const rawColumns = [
  ["beam_no", "梁编号"], ["beam_line", "梁线编号"], ["span_no", "跨号"],
  ["story", "楼层"], ["beam_type", "主次梁"], ["position", "位置"],
  ["section", "采用截面"], ["all_sections", "本跨全部截面"],
  ["width_mm", "梁宽/mm"], ["depth_mm", "梁高D/mm"], ["length_mm", "跨长/mm"],
  ["etabs_longitudinal_cover_mm", "ETABS纵筋设计cover/mm"],
  ["cover_calculation_basis", "保护层计算口径"],
  ["etabs_longitudinal_cover_source", "ETABS cover来源"],
  ["clear_cover_to_stirrup_outer_mm", "箍筋外缘净保护层/mm"],
  ["clear_cover_source", "净保护层来源"],
  ["fc_mpa", "fc'/MPa"], ["fy_mpa", "fy/MPa"], ["fyt_mpa", "fyt/MPa"],
  ["max_aggregate_size_mm", "最大骨料/mm"],
  ["max_aggregate_size_source", "骨料来源"],
  ["frame_type", "ETABS FrameType"], ["detailing_profile", "配筋Profile"],
  ["profile_source", "Profile来源"], ["is_smf", "是否SMF"],
  ["source_objects", "源 Object"], ["design_status", "设计结果状态"],
  ["design_status_message", "设计状态说明"], ["design_result_source", "设计字段来源"],
  ["design_units", "设计结果单位"],
  ["engine_status", "引擎状态"], ["warnings", "警告"],
  ["top_total_mm2", "顶部AsTotal/mm²"], ["top_total_station_mm", "顶部Total站点/mm"],
  ["top_total_combo", "顶部Total组合"], ["top_flex_mm2", "顶部AsFlex/mm²"],
  ["top_flex_station_mm", "顶部Flex站点/mm"], ["top_flex_combo", "顶部Flex组合"],
  ["bottom_total_mm2", "底部AsTotal/mm²"], ["bottom_total_station_mm", "底部Total站点/mm"],
  ["bottom_total_combo", "底部Total组合"], ["bottom_flex_mm2", "底部AsFlex/mm²"],
  ["bottom_flex_station_mm", "底部Flex站点/mm"], ["bottom_flex_combo", "底部Flex组合"],
  ["as_min_top_mm2", "AsMinTop/mm²"], ["as_min_bottom_mm2", "AsMinBot/mm²"],
  ["as_provided_top_mm2", "AsProvidedTop/mm²"], ["as_provided_bottom_mm2", "AsProvidedBot/mm²"],
  ["raw_as_provided_top", "ETABS顶部Provided原始值"], ["raw_as_provided_bottom", "ETABS底部Provided原始值"],
  ["av_over_s", "VMajorArea Av/s"], ["av_station_mm", "Av/s站点/mm"], ["av_combo", "Av/s组合"],
  ["al_mm2", "TLArea Al/mm²"], ["al_station_mm", "Al站点/mm"], ["al_combo", "Al组合"],
  ["at_over_s", "TTArea At/s"], ["at_station_mm", "At/s站点/mm"], ["at_combo", "At/s组合"],
  ["source_station_count", "区域站点数"], ["source_objects", "区域来源Object"],
  ["units", "区域单位"], ["result_source", "区域结果API"],
  ["design_errors", "区域设计Error"], ["design_warnings", "区域设计Warning"],
];
const rawRows = [];
for (const row of payload.raw_rows) {
  for (const [prefix, label] of [["left", "左端2D"], ["middle", "跨中"], ["right", "右端2D"]]) {
    const output = { ...row, position: label, is_smf: row.is_smf ? "是" : "否" };
    for (const [key] of rawColumns) {
      const sourceKey = `${prefix}_${key}`;
      if (sourceKey in row) output[key] = row[sourceKey];
    }
    rawRows.push(output);
  }
}
const rawEnd = colLetter(rawColumns.length - 1);
const rawEndRow = rawRows.length + 5;
styleTitle(
  rawSheet,
  rawEnd,
  "ETABS 梁跨原始设计数据",
  "一行代表一个梁跨位置（左端2D / 跨中 / 右端2D）。AsFlex 在每个 station 按 AsTotal−Al/2 计算后再取区域最大值；控制组合和 station 保留供校核。",
  metadataText,
);
rawSheet.getRange(`A5:${rawEnd}5`).values = [rawColumns.map(([, header]) => header)];
styleHeader(rawSheet.getRange(`A5:${rawEnd}5`));
rawSheet.getRange(`A6:${rawEnd}${rawEndRow}`).values = rawRows.map((row) =>
  rawColumns.map(([key]) => row[key] ?? null),
);
rawSheet.tables.add(`A5:${rawEnd}${rawEndRow}`, true, "RawSpanData").style = "TableStyleMedium2";
rawSheet.freezePanes.freezeRows(5);
rawSheet.freezePanes.freezeColumns(6);
rawSheet.showGridLines = false;
rawSheet.getRange(`I6:P${rawEndRow}`).format.numberFormat = "0.0";
rawSheet.getRange(`Y6:AX${rawEndRow}`).format.numberFormat = "0.000";
rawSheet.getRange(`A6:${rawEnd}${rawEndRow}`).format.font = { size: 9 };
rawSheet.getRange(`C1:R${rawEndRow}`).format.columnWidth = 11;
rawSheet.getRange(`W1:AV${rawEndRow}`).format.columnWidth = 13;
for (const col of ["A", "B", "G"]) rawSheet.getRange(`${col}1:${col}${rawEndRow}`).format.columnWidth = 22;
for (const col of ["H", "N", "P", "U", "V"]) {
  rawSheet.getRange(`${col}1:${col}${rawEndRow}`).format.columnWidth = 32;
}
rawSheet.getRange(`M1:M${rawEndRow}`).format.columnWidth = 28;

const reviewColumns = [
  ["beam_no", "梁编号"], ["beam_line", "梁线编号"], ["span_no", "跨号"],
  ["story", "楼层"], ["beam_type", "主次梁"], ["section", "截面"],
  ["width_mm", "梁宽/mm"], ["depth_mm", "梁高/mm"], ["frame_type", "FrameType"],
  ["detailing_profile", "配筋Profile"], ["is_smf", "SMF"],
  ["mode", "引擎模式"], ["engine_status", "引擎状态"],
  ["warnings", "警告"], ["legs", "Legs（可编辑）"],
  ["iteration_status", "纵筋/箍筋迭代状态"], ["iteration_count", "迭代次数"],
  ["coordination_status", "相邻跨协调状态"], ["continuity_option", "公共支座方案"],
  ["face_required_mm2", "Face计算面积/侧"], ["face_text", "Face配筋文本/侧"],
  ["face_actual", "Face实配面积/侧"], ["face_diff", "Face差值"], ["face_status", "Face状态"],
];
for (const [key, label] of [["et1", "ET1"], ["t1", "T1"], ["et2", "ET2"], ["eb1", "EB1"], ["b1", "B1"], ["eb2", "EB2"]]) {
  reviewColumns.push([`${key}_required_mm2`, `${label}计算面积`]);
  reviewColumns.push([`${key}_text`, `${label}配筋文本`]);
  reviewColumns.push([`${key}_actual`, `${label}实配面积`]);
  reviewColumns.push([`${key}_diff`, `${label}差值`]);
  reviewColumns.push([`${key}_status`, `${label}状态`]);
}
reviewColumns.push(
  ["s2_text", "S2配筋文本"], ["s2_left_at_required", "左At/s计算"],
  ["s2_left_combo_required", "左剪扭计算"], ["s2_at_actual", "S2单腿At/s实配"],
  ["s2_combo_actual", "S2总腿实配"], ["s2_left_status", "左S2状态"],
  ["s2_right_at_required", "右At/s计算"], ["s2_right_combo_required", "右剪扭计算"],
  ["s2_right_status", "右S2状态"], ["s1_text", "S1配筋文本"],
  ["s1_at_required", "中At/s计算"], ["s1_combo_required", "中剪扭计算"],
  ["s1_at_actual", "S1单腿At/s实配"], ["s1_combo_actual", "S1总腿实配"],
  ["s1_status", "中S1状态"],
);
for (const [prefix, label] of [["s2_left", "左端"], ["s1", "跨中"], ["s2_right", "右端"]]) {
  reviewColumns.push(
    [`${prefix}_av_required`, `${label}原始Av/s`],
    [`${prefix}_av_min`, `${label}最小Av/s`],
    [`${prefix}_at_provided`, `${label}引擎单腿At/s实配`],
    [`${prefix}_combo_provided`, `${label}引擎总腿实配`],
    [`${prefix}_maximum_spacing_mm`, `${label}最大允许间距/mm`],
    [`${prefix}_governing_reason`, `${label}间距控制原因`],
    [`${prefix}_final_main_db_mm`, `${label}最终纵筋db/mm`],
    [`${prefix}_high_shear`, `${label}高剪分支`],
    [`${prefix}_spacing_limits`, `${label}全部间距限值`],
    [`${prefix}_shear_source`, `${label}剪力需求来源`],
  );
}
const reviewIndex = Object.fromEntries(reviewColumns.map(([key], index) => [key, index]));
const engineStatusCol = colLetter(reviewIndex.engine_status);
const reviewEnd = colLetter(reviewColumns.length - 1);
const reviewStart = 6;
const reviewEndRow = reviewStart + payload.review_rows.length - 1;
const calculatedReviewStatusKeys = [
  "face_status",
  "et1_status",
  "t1_status",
  "et2_status",
  "eb1_status",
  "b1_status",
  "eb2_status",
  "s2_left_status",
  "s2_right_status",
  "s1_status",
];
styleTitle(
  reviewSheet,
  reviewEnd,
  "梁配筋调整与计算面积复核",
  "黄色单元格可编辑：纵筋如 8T25 6/2、2T25+2T20 或 2T16+(2T12)；层数说明写在第一个空格后，括号内为架立筋。受弯面积公式忽略分层后缀及括号架立筋。每排至少2根，不允许单根末排。S2 同时校核左右端，S1 校核跨中。",
  metadataText,
);
reviewSheet.getRange(`A5:${reviewEnd}5`).values = [reviewColumns.map(([, header]) => header)];
styleHeader(reviewSheet.getRange(`A5:${reviewEnd}5`));
reviewSheet.getRange(`A${reviewStart}:${reviewEnd}${reviewEndRow}`).values = payload.review_rows.map((row, index) => {
  const initial = payload.final_rows[index] ?? {};
  const values = { ...row, is_smf: row.is_smf ? "是" : "否", s2_text: initial.s2 ?? "" };
  return reviewColumns.map(([key]) => {
    if (
      key.endsWith("_actual") ||
      key.endsWith("_diff") ||
      calculatedReviewStatusKeys.includes(key)
    ) {
      return null;
    }
    return values[key] ?? null;
  });
});

for (const group of ["face", "et1", "t1", "et2", "eb1", "b1", "eb2"]) {
  const textCol = colLetter(reviewIndex[`${group}_text`]);
  const actualCol = colLetter(reviewIndex[`${group}_actual`]);
  const requiredCol = colLetter(reviewIndex[group === "face" ? "face_required_mm2" : `${group}_required_mm2`]);
  const diffCol = colLetter(reviewIndex[`${group}_diff`]);
  const statusCol = colLetter(reviewIndex[`${group}_status`]);
  reviewSheet.getRange(`${actualCol}${reviewStart}:${actualCol}${reviewEndRow}`).formulas = Array.from(
    { length: payload.review_rows.length }, (_, i) => {
      if (payload.review_rows[i]?.engine_status !== "OK") return ['=""'];
      const row = reviewStart + i;
      return [rebarAreaFormula(`${textCol}${row}`)];
    },
  );
  reviewSheet.getRange(`${diffCol}${reviewStart}:${diffCol}${reviewEndRow}`).formulas = Array.from(
    { length: payload.review_rows.length }, (_, i) => {
      if (payload.review_rows[i]?.engine_status !== "OK") return ['=""'];
      const row = reviewStart + i;
      return [`=${actualCol}${row}-${requiredCol}${row}`];
    },
  );
  reviewSheet.getRange(`${statusCol}${reviewStart}:${statusCol}${reviewEndRow}`).formulas = Array.from(
    { length: payload.review_rows.length }, (_, i) => {
      const row = reviewStart + i;
      if (group === "face") {
        return [`=IF($${engineStatusCol}${row}<>"OK","ENGINE",IF(AND(UPPER(TRIM(${textCol}${row}))="N.A.",${requiredCol}${row}<=0),"N.A.",IF(${actualCol}${row}+0.001>=${requiredCol}${row},"OK","NG")))`];
      }
      return [`=IF($${engineStatusCol}${row}<>"OK","ENGINE",IF(${actualCol}${row}+0.001>=${requiredCol}${row},"OK","NG"))`];
    },
  );
}

const legsCol = colLetter(reviewIndex.legs);
for (const group of ["s2", "s1"]) {
  const textCol = colLetter(reviewIndex[`${group}_text`]);
  const atCol = colLetter(reviewIndex[`${group}_at_actual`]);
  const comboCol = colLetter(reviewIndex[`${group}_combo_actual`]);
  reviewSheet.getRange(`${atCol}${reviewStart}:${atCol}${reviewEndRow}`).formulas = Array.from(
    { length: payload.review_rows.length }, (_, i) => {
      if (payload.review_rows[i]?.engine_status !== "OK") return ['=""'];
      const row = reviewStart + i;
      return [stirrupAtFormula(`${textCol}${row}`)];
    },
  );
  reviewSheet.getRange(`${comboCol}${reviewStart}:${comboCol}${reviewEndRow}`).formulas = Array.from(
    { length: payload.review_rows.length }, (_, i) => {
      if (payload.review_rows[i]?.engine_status !== "OK") return ['=""'];
      const row = reviewStart + i;
      return [`=${atCol}${row}*${legsCol}${row}`];
    },
  );
}
for (const [statusKey, atReqKey, comboReqKey, atActualKey, comboActualKey] of [
  ["s2_left_status", "s2_left_at_required", "s2_left_combo_required", "s2_at_actual", "s2_combo_actual"],
  ["s2_right_status", "s2_right_at_required", "s2_right_combo_required", "s2_at_actual", "s2_combo_actual"],
  ["s1_status", "s1_at_required", "s1_combo_required", "s1_at_actual", "s1_combo_actual"],
]) {
  const statusCol = colLetter(reviewIndex[statusKey]);
  const atReqCol = colLetter(reviewIndex[atReqKey]);
  const comboReqCol = colLetter(reviewIndex[comboReqKey]);
  const atActualCol = colLetter(reviewIndex[atActualKey]);
  const comboActualCol = colLetter(reviewIndex[comboActualKey]);
  reviewSheet.getRange(`${statusCol}${reviewStart}:${statusCol}${reviewEndRow}`).formulas = Array.from(
    { length: payload.review_rows.length }, (_, i) => {
      const row = reviewStart + i;
      return [`=IF($${engineStatusCol}${row}<>"OK","ENGINE",IF(AND(${atActualCol}${row}+0.000001>=${atReqCol}${row},${comboActualCol}${row}+0.000001>=${comboReqCol}${row}),"OK","NG"))`];
    },
  );
}

reviewSheet.tables.add(`A5:${reviewEnd}${reviewEndRow}`, true, "RebarReviewTable").style = "TableStyleMedium4";
reviewSheet.freezePanes.freezeRows(5);
reviewSheet.freezePanes.freezeColumns(6);
reviewSheet.showGridLines = false;
reviewSheet.getRange(`A6:${reviewEnd}${reviewEndRow}`).format.font = { size: 9 };
for (const key of ["legs", "face_text", "et1_text", "t1_text", "et2_text", "eb1_text", "b1_text", "eb2_text", "s2_text", "s1_text"]) {
  const col = colLetter(reviewIndex[key]);
  reviewSheet.getRange(`${col}${reviewStart}:${col}${reviewEndRow}`).format.fill = colors.input;
  reviewSheet.getRange(`${col}1:${col}${reviewEndRow}`).format.columnWidth = key.includes("text") ? 16 : 10;
}
for (const [key] of reviewColumns) {
  const col = colLetter(reviewIndex[key]);
  if (key.includes("required") || key.endsWith("_actual") || key.endsWith("_diff")) {
    reviewSheet.getRange(`${col}${reviewStart}:${col}${reviewEndRow}`).format.numberFormat = "0.000";
    if (key.endsWith("_actual") || key.endsWith("_diff")) {
      reviewSheet.getRange(`${col}${reviewStart}:${col}${reviewEndRow}`).format.fill = colors.formula;
    }
  }
  if (key.endsWith("_status")) addStatusFormats(reviewSheet.getRange(`${col}${reviewStart}:${col}${reviewEndRow}`));
}
reviewSheet.getRange(`A1:A${reviewEndRow}`).format.columnWidth = 24;
reviewSheet.getRange(`B1:B${reviewEndRow}`).format.columnWidth = 22;
reviewSheet.getRange(`F1:F${reviewEndRow}`).format.columnWidth = 18;
reviewSheet.getRange(`I1:I${reviewEndRow}`).format.columnWidth = 18;
reviewSheet.getRange(`K1:L${reviewEndRow}`).format.columnWidth = 20;
reviewSheet.getRange(`M1:M${reviewEndRow}`).format.columnWidth = 34;
reviewSheet.getRange(`N1:N${reviewEndRow}`).format.columnWidth = 26;
reviewSheet.getRange(`A1:${reviewEnd}${reviewEndRow}`).format.verticalAlignment = "center";

const finalColumns = [
  ["beam_line", "梁线编号"], ["beam_no", "梁编号"], ["story", "楼层"],
  ["beam_type", "主次梁"], ["width_mm", "梁宽/mm"], ["depth_mm", "梁高/mm"],
  ["et1", "ET1"], ["t1", "T1"], ["et2", "ET2"], ["eb1", "EB1"],
  ["b1", "B1"], ["eb2", "EB2"], ["s2", "S2"], ["s1", "S1"],
  ["legs", "Legs"], ["face", "Face/side"], ["mode", "模式"],
  ["engine_status", "引擎状态"], ["qc_status", "复核状态"],
];
const finalIndex = Object.fromEntries(finalColumns.map(([key], index) => [key, index]));
const finalEnd = colLetter(finalColumns.length - 1);
const finalStart = 6;
const finalEndRow = finalStart + payload.final_rows.length - 1;
styleTitle(
  finalSheet,
  finalEnd,
  "整理后的混凝土梁配筋表",
  "本表由“配筋调整复核”黄色输入单元格驱动。修改配筋文本或 Legs 后，实配面积、状态和本表同步更新；S2 为左右端共用，S1 为跨中。",
  metadataText,
);
finalSheet.getRange(`A5:${finalEnd}5`).values = [finalColumns.map(([, header]) => header)];
styleHeader(finalSheet.getRange(`A5:${finalEnd}5`));
const reviewRefs = {
  beam_line: "beam_line", beam_no: "beam_no", story: "story", beam_type: "beam_type",
  width_mm: "width_mm", depth_mm: "depth_mm", et1: "et1_text", t1: "t1_text",
  et2: "et2_text", eb1: "eb1_text", b1: "b1_text", eb2: "eb2_text",
  s2: "s2_text", s1: "s1_text", legs: "legs", face: "face_text",
  mode: "mode", engine_status: "engine_status",
};
const guardedFinalFields = new Set([
  "et1", "t1", "et2", "eb1", "b1", "eb2", "s2", "s1",
  "legs", "face", "mode",
]);
for (const [key, reviewKey] of Object.entries(reviewRefs)) {
  const finalCol = colLetter(finalIndex[key]);
  const reviewCol = colLetter(reviewIndex[reviewKey]);
  finalSheet.getRange(`${finalCol}${finalStart}:${finalCol}${finalEndRow}`).formulas = Array.from(
    { length: payload.final_rows.length }, (_, i) => {
      const row = reviewStart + i;
      const valueRef = `'配筋调整复核'!${reviewCol}${row}`;
      if (!guardedFinalFields.has(key)) return [`=${valueRef}`];
      const engineRef = `'配筋调整复核'!${colLetter(reviewIndex.engine_status)}${row}`;
      return [`=IF(${engineRef}<>"OK","",${valueRef})`];
    },
  );
}
const qcCol = colLetter(finalIndex.qc_status);
finalSheet.getRange(`${qcCol}${finalStart}:${qcCol}${finalEndRow}`).formulas = Array.from(
  { length: payload.final_rows.length }, (_, i) => {
    const row = reviewStart + i;
    const engineRef = `'配筋调整复核'!${colLetter(reviewIndex.engine_status)}${row}`;
    const checks = calculatedReviewStatusKeys
      .map((key) => `'配筋调整复核'!${colLetter(reviewIndex[key])}${row}="NG"`)
      .join(",");
    return [`=IF(${engineRef}<>"OK","ENGINE_ERROR",IF(OR(${checks}),"CHECK","OK"))`];
  },
);
finalSheet.tables.add(`A5:${finalEnd}${finalEndRow}`, true, "FinalBeamSchedule").style = "TableStyleMedium9";
finalSheet.freezePanes.freezeRows(5);
finalSheet.freezePanes.freezeColumns(2);
finalSheet.showGridLines = false;
finalSheet.getRange(`A6:${finalEnd}${finalEndRow}`).format.font = { size: 9 };
finalSheet.getRange(`E6:F${finalEndRow}`).format.numberFormat = "0";
addStatusFormats(finalSheet.getRange(`${qcCol}${finalStart}:${qcCol}${finalEndRow}`));
finalSheet.getRange(`A1:B${finalEndRow}`).format.columnWidth = 24;
finalSheet.getRange(`C1:F${finalEndRow}`).format.columnWidth = 11;
finalSheet.getRange(`G1:P${finalEndRow}`).format.columnWidth = 15;
finalSheet.getRange(`Q1:Q${finalEndRow}`).format.columnWidth = 18;
finalSheet.getRange(`R1:R${finalEndRow}`).format.columnWidth = 34;
finalSheet.getRange(`S1:S${finalEndRow}`).format.columnWidth = 20;

const reviewCheck = await workbook.inspect({
  kind: "table",
  range: `配筋调整复核!A5:S${Math.min(reviewEndRow, 12)}`,
  include: "values,formulas",
  maxChars: 6000,
  tableMaxRows: 8,
  tableMaxCols: 19,
});
console.log(reviewCheck.ndjson);
const errorPattern = "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A";
const formulaErrorScans = calculatedReviewStatusKeys.map((key) => {
  const column = colLetter(reviewIndex[key]);
  return [
    "配筋调整复核",
    `${column}${reviewStart}:${column}${reviewEndRow}`,
    `review ${key} formula error scan`,
  ];
});
formulaErrorScans.push([
  "整理梁表",
  `${qcCol}${finalStart}:${qcCol}${finalEndRow}`,
  "final schedule QC formula error scan",
]);
for (const [sheetId, range, summary] of formulaErrorScans) {
  const errors = await workbook.inspect({
    kind: "match",
    sheetId,
    range,
    searchTerm: errorPattern,
    maxChars: 4000,
    options: { useRegex: true, maxResults: 100 },
    summary,
  });
  console.log(errors.ndjson);
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
try {
  await xlsx.save(outputPath);
} finally {
  // Large-workbook inspect may spill a temporary NDJSON file beside the
  // output. It is a QA scratch artifact, not part of the delivery contract.
  await fs.rm(`${outputPath}.inspect.ndjson`, { force: true });
}
console.log(`OUTPUT=${outputPath}`);
