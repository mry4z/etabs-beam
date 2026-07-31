import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewDir) {
  throw new Error(
    "usage: node build_secondary_beam_workbook.mjs <payload.json> <output.xlsx> <preview-dir>",
  );
}

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
for (const key of ["metadata", "schedule_rows", "exception_rows"]) {
  if (!(key in payload)) throw new Error(`payload missing ${key}`);
}

const naturalCompare = (left, right) =>
  String(left ?? "").localeCompare(String(right ?? ""), undefined, {
    numeric: true,
    sensitivity: "base",
  });
const scheduleRows = [...payload.schedule_rows].sort(
  (left, right) =>
    naturalCompare(left.story, right.story) ||
    naturalCompare(left.beam_line, right.beam_line) ||
    Number(left.span_no ?? 0) - Number(right.span_no ?? 0),
);
const exceptionRows = [...payload.exception_rows].sort(
  (left, right) =>
    naturalCompare(left.story, right.story) ||
    naturalCompare(left.beam_line, right.beam_line) ||
    Number(left.span_no ?? 0) - Number(right.span_no ?? 0),
);

for (const row of scheduleRows) {
  if (String(row.beam_type ?? "").toUpperCase() !== "SB") {
    throw new Error(`non-SB row in secondary schedule: ${row.beam_no}`);
  }
  if (!row.stirrups || row.stirrups !== row.s1 || row.stirrups !== row.s2) {
    throw new Error(`SB stirrup is not span-unified: ${row.beam_no}`);
  }
}

const workbook = Workbook.create();
const scheduleSheet = workbook.worksheets.add("SB梁表");
const exceptionSheet = workbook.worksheets.add("复核异常");
scheduleSheet.showGridLines = false;
exceptionSheet.showGridLines = false;

const magenta = "#FF00FF";
const palePink = "#FFF2F8";
const paleBlue = "#DDEBF7";
const navy = "#17365D";
const lightBorder = "#B4C6E7";
const stories = [...new Set(scheduleRows.map((row) => String(row.story ?? "")))];
let cursor = 1;

if (!stories.length) {
  scheduleSheet.mergeCells("A1:M1");
  scheduleSheet.getRange("A1").values = [["SECONDARY BEAM REINFORCEMENT SCHEDULE"]];
  scheduleSheet.getRange("A1:M1").format = {
    fill: navy,
    font: { bold: true, size: 12, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  scheduleSheet.mergeCells("A2:M3");
  scheduleSheet.getRange("A2").values = [[
    `无可交付 SB 行；${exceptionRows.length} 行已保留在“复核异常”。` +
    " 未确认保护层、骨料或其他工程输入时，不生成零值配筋。",
  ]];
  scheduleSheet.getRange("A2:M3").format = {
    fill: paleBlue,
    font: { color: navy, size: 10 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: lightBorder },
  };
  cursor = 5;
}

for (const story of stories) {
  const rows = scheduleRows.filter((row) => String(row.story ?? "") === story);
  const titleRow = cursor;
  const groupRow = cursor + 1;
  const subheaderRow = cursor + 2;
  const dataStart = cursor + 3;
  const dataEnd = dataStart + rows.length - 1;

  scheduleSheet.mergeCells(`A${titleRow}:M${titleRow}`);
  scheduleSheet.getRange(`A${titleRow}`).values = [
    [`SECONDARY BEAM REINFORCEMENT SCHEDULE (${story} LEVEL)`],
  ];

  for (const range of [
    `A${groupRow}:B${subheaderRow}`,
    `C${groupRow}:D${groupRow}`,
    `E${groupRow}:G${groupRow}`,
    `H${groupRow}:J${groupRow}`,
    `K${groupRow}:K${subheaderRow}`,
    `L${groupRow}:L${subheaderRow}`,
  ]) {
    scheduleSheet.mergeCells(range);
  }
  scheduleSheet.getRange(`A${groupRow}`).values = [["BEAM NO."]];
  scheduleSheet.getRange(`C${groupRow}`).values = [["BEAM SIZE/mm"]];
  scheduleSheet.getRange(`E${groupRow}`).values = [["TOP REBARS"]];
  scheduleSheet.getRange(`H${groupRow}`).values = [["BOTTOM REBARS"]];
  scheduleSheet.getRange(`K${groupRow}`).values = [["STIRRUPS"]];
  scheduleSheet.getRange(`L${groupRow}`).values = [["NO. OF LEGGED\nSTIRRUPS"]];
  scheduleSheet.getRange(`M${groupRow}`).values = [["FACE REBAR"]];
  scheduleSheet.getRange(`C${subheaderRow}:J${subheaderRow}`).values = [
    ["WIDTH", "DEPTH", "ET1", "T1", "ET2", "EB1", "B1", "EB2"],
  ];
  scheduleSheet.getRange(`M${subheaderRow}`).values = [["EACH SIDE"]];

  scheduleSheet.getRange(`A${dataStart}:M${dataEnd}`).values = rows.map((row) => [
    row.beam_line ?? "",
    row.beam_no ?? "",
    Number(row.width_mm ?? 0),
    Number(row.depth_mm ?? 0),
    row.et1 ?? "",
    row.t1 ?? "",
    row.et2 ?? "",
    row.eb1 ?? "",
    row.b1 ?? "",
    row.eb2 ?? "",
    row.stirrups ?? "",
    Number(row.legs ?? 0),
    row.face ?? "",
  ]);

  let groupStart = dataStart;
  for (let index = 1; index <= rows.length; index += 1) {
    const changed =
      index === rows.length ||
      String(rows[index].beam_line ?? "") !==
        String(rows[index - 1].beam_line ?? "");
    if (!changed) continue;
    const groupEnd = dataStart + index - 1;
    if (groupEnd > groupStart) {
      scheduleSheet.mergeCells(`A${groupStart}:A${groupEnd}`);
    }
    groupStart = groupEnd + 1;
  }

  scheduleSheet.getRange(`A${titleRow}:M${titleRow}`).format = {
    font: { bold: true, size: 12, color: "#000000" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: magenta },
  };
  scheduleSheet.getRange(`A${groupRow}:M${subheaderRow}`).format = {
    fill: "#FFFFFF",
    font: { bold: true, size: 9, color: "#000000" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: magenta },
  };
  scheduleSheet.getRange(`A${dataStart}:M${dataEnd}`).format = {
    font: { size: 9, color: "#000000" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: magenta },
  };
  scheduleSheet.getRange(`C${dataStart}:D${dataEnd}`).format.numberFormat =
    "0";
  scheduleSheet.getRange(`L${dataStart}:L${dataEnd}`).format.numberFormat =
    "0";
  scheduleSheet.getRange(`A${dataStart}:B${dataEnd}`).format = {
    fill: palePink,
    horizontalAlignment: "left",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: magenta },
  };
  scheduleSheet.getRange(`A${titleRow}:M${titleRow}`).format.rowHeight = 24;
  scheduleSheet.getRange(`A${groupRow}:M${subheaderRow}`).format.rowHeight = 24;
  scheduleSheet.getRange(`A${dataStart}:M${dataEnd}`).format.rowHeight = 20;
  cursor = dataEnd + 2;
}

const scheduleLastRow = Math.max(1, cursor - 1);
scheduleSheet.getRange(`A1:M${scheduleLastRow}`).format.columnWidth = 12;
scheduleSheet.getRange(`A1:B${scheduleLastRow}`).format.columnWidth = 18;
scheduleSheet.getRange(`C1:D${scheduleLastRow}`).format.columnWidth = 10;
scheduleSheet.getRange(`E1:J${scheduleLastRow}`).format.columnWidth = 14;
scheduleSheet.getRange(`K1:K${scheduleLastRow}`).format.columnWidth = 16;
scheduleSheet.getRange(`L1:L${scheduleLastRow}`).format.columnWidth = 15;
scheduleSheet.getRange(`M1:M${scheduleLastRow}`).format.columnWidth = 14;
scheduleSheet.freezePanes.freezeRows(3);
scheduleSheet.freezePanes.freezeColumns(2);

const exceptionColumns = [
  ["story", "楼层"],
  ["beam_line", "梁线编号"],
  ["beam_no", "梁编号"],
  ["section", "截面"],
  ["engine_status", "阻断/复核原因"],
  ["source_objects", "源 Object"],
];
exceptionSheet.getRange("A1:F1").merge();
exceptionSheet.getRange("A1").values = [["SB 梁表复核异常"]];
exceptionSheet.getRange("A2:F2").values = [
  exceptionColumns.map(([, header]) => header),
];
if (exceptionRows.length) {
  exceptionSheet.getRange(`A3:F${exceptionRows.length + 2}`).values =
    exceptionRows.map((row) =>
      exceptionColumns.map(([key]) => row[key] ?? ""),
    );
}
exceptionSheet.getRange("A1:F1").format = {
  fill: navy,
  font: { bold: true, size: 12, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
exceptionSheet.getRange("A2:F2").format = {
  fill: paleBlue,
  font: { bold: true, color: navy },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: lightBorder },
};
if (exceptionRows.length) {
  exceptionSheet.getRange(`A3:F${exceptionRows.length + 2}`).format = {
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: lightBorder },
  };
}
const exceptionLastRow = Math.max(2, exceptionRows.length + 2);
exceptionSheet.getRange(`A1:B${exceptionLastRow}`).format.columnWidth = 18;
exceptionSheet.getRange(`C1:C${exceptionLastRow}`).format.columnWidth = 24;
exceptionSheet.getRange(`D1:D${exceptionLastRow}`).format.columnWidth = 16;
exceptionSheet.getRange(`E1:E${exceptionLastRow}`).format.columnWidth = 56;
exceptionSheet.getRange(`F1:F${exceptionLastRow}`).format.columnWidth = 40;
exceptionSheet.freezePanes.freezeRows(2);

const scheduleInspect = await workbook.inspect({
  kind: "table",
  sheetId: "SB梁表",
  range: "A1:M20",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 13,
  maxChars: 12000,
});
console.log(scheduleInspect.ndjson);
const errorInspect = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "secondary schedule formula error scan",
});
console.log(errorInspect.ndjson);

await fs.mkdir(previewDir, { recursive: true });
const schedulePreview = await workbook.render({
  sheetName: "SB梁表",
  range: "A1:M25",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(
  path.join(previewDir, "secondary_schedule.png"),
  new Uint8Array(await schedulePreview.arrayBuffer()),
);
const exceptionPreview = await workbook.render({
  sheetName: "复核异常",
  range: `A1:F${Math.min(exceptionRows.length + 2, 20)}`,
  scale: 1.5,
  format: "png",
});
await fs.writeFile(
  path.join(previewDir, "secondary_exceptions.png"),
  new Uint8Array(await exceptionPreview.arrayBuffer()),
);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`OUTPUT=${outputPath}`);
