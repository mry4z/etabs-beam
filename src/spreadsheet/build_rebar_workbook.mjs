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
if (
  payload.raw_rows.length !== payload.review_rows.length ||
  payload.raw_rows.length !== payload.final_rows.length
) {
  throw new Error("raw_rows, review_rows and final_rows must have the same length");
}

const metadata = payload.metadata ?? {};
const explicitRole = String(metadata.beam_type_filter ?? "").trim().toUpperCase();
const scheduleRole = String(metadata.schedule_kind ?? "").toUpperCase().includes("SECONDARY")
  ? "SB"
  : "";
const rowRoles = new Set(
  payload.final_rows
    .map((row) => String(row.beam_type ?? "").trim().toUpperCase())
    .filter(Boolean),
);
const beamType = explicitRole || scheduleRole || (rowRoles.size > 0 && [...rowRoles].every((role) => role === "SB") ? "SB" : "PB");
if (!new Set(["PB", "SB"]).has(beamType)) {
  throw new Error(`unknown beam type: ${beamType}`);
}

// These checks remain in the JSON/audit payload, but are outside the current
// workbook colour gate.  The project has deferred anchorage, lap splice and
// torsion-perimeter verification to a future phase, and has already confirmed
// the ETABS Frame Design design-force source for the current phase.
const NON_CURRENT_REVIEW_CODES = new Set([
  "ANCHORAGE_UNVERIFIED",
  "SPLICE_UNVERIFIED",
  "TORSION_PERIMETER_LAYOUT_UNVERIFIED",
  "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
  "SMF_DESIGN_SOURCE_UNVERIFIED",
  "SMF_SUPPORT_FACE_UNRESOLVED",
  "MIXED_SECTION_REVIEW_REQUIRED",
]);

const columns = beamType === "PB"
  ? [
      ["beam_line", "BEAM LINE"], ["beam_no", "SPAN"],
      ["width_mm", "WIDTH"], ["depth_mm", "DEPTH"],
      ["et1", "ET1"], ["t1", "T1"], ["et2", "ET2"],
      ["b1", "B1"], ["s2", "NEAR SUPPORT (S2)"],
      ["s1", "CONTINUOUS (S1)"], ["legs", "LEGS"],
      ["face", "EACH SIDE"],
    ]
  : [
      ["beam_line", "BEAM LINE"], ["beam_no", "SPAN"],
      ["width_mm", "WIDTH"], ["depth_mm", "DEPTH"],
      ["et1", "ET1"], ["t1", "T1"], ["et2", "ET2"],
      ["b1", "B1"], ["stirrups", "STIRRUPS"], ["legs", "LEGS"],
      ["face", "EACH SIDE"],
    ];
const lastColumn = colLetter(columns.length - 1);

function colLetter(index) {
  let number = index + 1;
  let result = "";
  while (number > 0) {
    const remainder = (number - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    number = Math.floor((number - 1) / 26);
  }
  return result;
}

function naturalKey(value) {
  return String(value ?? "")
    .trim()
    .split(/(\d+)/)
    .filter((part) => part !== "")
    .map((part) => (/^\d+$/.test(part) ? [0, Number(part)] : [1, part.toLowerCase()]));
}

function compareNatural(left, right) {
  const a = naturalKey(left);
  const b = naturalKey(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if (index >= a.length) return -1;
    if (index >= b.length) return 1;
    const [aType, aValue] = a[index];
    const [bType, bValue] = b[index];
    if (aType !== bType) return aType - bType;
    if (aValue < bValue) return -1;
    if (aValue > bValue) return 1;
  }
  return 0;
}

function spanCompare(left, right) {
  const leftNumber = Number(left.span_no);
  const rightNumber = Number(right.span_no);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    return leftNumber - rightNumber;
  }
  return compareNatural(left.beam_no, right.beam_no);
}

function asNumber(value) {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Number.isInteger(number) ? number : number;
}

function displayFace(value) {
  const text = String(value ?? "").trim();
  return !text || ["n.a.", "n/a", "na", "none", "null"].includes(text.toLowerCase()) ? "—" : text;
}

function displayStirrup(value) {
  const text = String(value ?? "").trim();
  return text && text.includes("@") && !/c\s*\/\s*c\s*$/i.test(text) ? `${text} c/c` : text;
}

function exceptionReason(...rows) {
  const values = Object.assign({}, ...rows);
  const reasons = [];
  const status = String(values.engine_status ?? "").trim();
  if (status && status.toUpperCase() !== "OK" && !NON_CURRENT_REVIEW_CODES.has(status.toUpperCase())) reasons.push(status);
  const blocking = String(values.constructability_blocking_statuses ?? "")
    .split(/[;,|]/)
    .map((token) => token.trim())
    .filter((token) => token && !NON_CURRENT_REVIEW_CODES.has(token.toUpperCase()))
    .join("; ");
  if (blocking) reasons.push(blocking);
  if (values.bottom_reinforcement_strategy !== "FULL_SPAN_UNIFORM_PER_SPAN") {
    reasons.push("LEGACY_REDETAIL_REQUIRED");
  }
  if (values.bottom_uniform_regions_match === false) {
    reasons.push("BOTTOM_LAYOUT_NOT_UNIFORM_WITHIN_SPAN");
  }
  let warningText = ["warnings", "engine_warnings", "review_note", "capacity_shear_status"]
    .map((key) => String(values[key] ?? "").toLowerCase())
    .join(" ");
  for (const code of NON_CURRENT_REVIEW_CODES) warningText = warningText.split(code.toLowerCase()).join("");
  for (const token of ["review", "conflict", "failed", "fail", "unverified", "error", "block", "missing", "no_results", "not_unified", "requires", "需复核", "阻断"]) {
    if (warningText.includes(token)) {
      reasons.push(`WARNING:${token}`);
      break;
    }
  }
  return [...new Set(reasons)].join("; ");
}

function rowValues(row) {
  const bottom = row.bottom_uniform_text ?? row.b1 ?? row.eb1 ?? "";
  const values = {
    beam_line: String(row.beam_line ?? ""),
    beam_no: String(row.beam_no ?? ""),
    width_mm: asNumber(row.width_mm),
    depth_mm: asNumber(row.depth_mm),
    et1: String(row.et1 ?? ""),
    t1: String(row.t1 ?? ""),
    et2: String(row.et2 ?? ""),
    b1: String(bottom),
    legs: asNumber(row.legs),
    face: displayFace(row.face),
  };
  if (beamType === "PB") {
    values.s2 = displayStirrup(row.s2 ?? row.s2_text ?? "");
    values.s1 = displayStirrup(row.s1 ?? row.s1_text ?? "");
  } else {
    values.stirrups = displayStirrup(row.stirrups ?? row.s1 ?? row.s2 ?? row.s1_text ?? row.s2_text ?? "");
  }
  return values;
}

const entries = [];
for (let index = 0; index < payload.final_rows.length; index += 1) {
  const final = payload.final_rows[index];
  if (String(final.beam_type ?? "").trim().toUpperCase() !== beamType) continue;
  const raw = payload.raw_rows[index] ?? {};
  const review = payload.review_rows[index] ?? {};
  const source = Object.assign({}, raw, review, final);
  entries.push({ values: rowValues(source), source, reason: exceptionReason(raw, review, final) });
}
entries.sort((left, right) => {
  const story = compareNatural(left.source.story, right.source.story);
  if (story) return story;
  const line = compareNatural(left.source.beam_line, right.source.beam_line);
  if (line) return line;
  return spanCompare(left.source, right.source);
});

const colors = {
  navy: "#17365D",
  blue: "#1F4E78",
  lightBlue: "#DDEBF7",
  paleBlue: "#EEF5FB",
  gray: "#F3F6F8",
  white: "#FFFFFF",
  red: "#C00000",
  paleRed: "#FCE4D6",
  border: "#B7C9D6",
  text: "#000000",
};
const border = { preset: "all", style: "thin", color: colors.border };
const center = {
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: border,
};

function styleMergedLabel(sheet, row, start, end, value, format) {
  const range = `${colLetter(start - 1)}${row}:${colLetter(end - 1)}${row}`;
  sheet.getRange(range).format = { ...format, borders: border };
  if (end > start) sheet.mergeCells(range);
  sheet.getRange(`${colLetter(start - 1)}${row}`).values = [[value]];
}

function writeHeaders(sheet, row) {
  const groups = beamType === "PB"
    ? [[1, 2, "BEAM NO."], [3, 4, "BEAM SIZE/mm"], [5, 7, "TOP REBARS"], [8, 8, "BOTTOM REBARS"], [9, 10, "STIRRUPS"], [11, 11, "No. OF LEGGED STIRRUPS"], [12, 12, "FACE REBAR EACH SIDE"]]
    : [[1, 2, "BEAM NO."], [3, 4, "BEAM SIZE/mm"], [5, 7, "TOP REBARS"], [8, 8, "BOTTOM REBARS"], [9, 9, "STIRRUPS"], [10, 10, "No. OF LEGGED STIRRUPS"], [11, 11, "FACE REBAR EACH SIDE"]];
  for (const [start, end, label] of groups) {
    styleMergedLabel(sheet, row, start, end, label, {
      fill: colors.blue,
      font: { bold: true, color: colors.white, size: 9 },
      ...center,
    });
  }
  sheet.getRange(`${colLetter(0)}${row + 1}:${lastColumn}${row + 1}`).values = [columns.map(([, label]) => label)];
  sheet.getRange(`${colLetter(0)}${row + 1}:${lastColumn}${row + 1}`).format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 9 },
    ...center,
  };
  sheet.getRange(`${colLetter(0)}${row}:${lastColumn}${row + 1}`).format.rowHeight = 34;
}

function exceptionComment(entry) {
  const source = entry.source;
  return `REVIEW REQUIRED: ${entry.reason}\nSource objects: ${source.source_objects ?? ""}\nBeam line/span: ${source.beam_line ?? ""}/${source.beam_no ?? ""}\nEngine status: ${source.engine_status ?? ""}\nConstructability: ${source.constructability_blocking_statuses ?? ""}\nReview note: ${source.review_note ?? ""}`;
}

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("整理梁表");
workbook.comments.setSelf({ displayName: "Codex" });
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(6);
sheet.freezePanes.freezeColumns(2);

styleMergedLabel(sheet, 1, 1, columns.length, `${beamType === "PB" ? "PRIMARY" : "SECONDARY"} BEAM REINFORCEMENT SCHEDULE`, {
  fill: colors.navy,
  font: { bold: true, color: colors.white, size: 15 },
  horizontalAlignment: "left",
  verticalAlignment: "center",
});
sheet.getRange("A1").format.rowHeight = 28;
styleMergedLabel(sheet, 2, 1, columns.length,
  `MODEL: ${metadata.model ?? ""} | UNITS: ${metadata.units ?? ""} | ROLE: ${beamType} | LAYOUT: ${metadata.rebar_workbook_layout_version ?? "single-sheet-static-v1"} | BOTTOM: ${metadata.bottom_reinforcement_strategy ?? "FULL_SPAN_UNIFORM_PER_SPAN"} | ADJACENT BOTTOM: ${metadata.adjacent_span_bottom_strategy ?? "INDEPENDENT_PER_SPAN"} | STATIC VALUES ONLY; calculation and audit evidence remain in the JSON payload.`,
  { fill: colors.lightBlue, font: { color: colors.navy, size: 9 }, wrapText: true, verticalAlignment: "center" });
sheet.getRange("A2").format.rowHeight = 32;
styleMergedLabel(sheet, 3, 1, columns.length,
  "All displayed cells are static values. Red rows require current-phase review; deferred checks remain in the backend JSON audit.",
  { fill: colors.gray, font: { color: "#595959", italic: true, size: 9 }, wrapText: true, verticalAlignment: "center" });
sheet.getRange("A3").format.rowHeight = 28;

const stories = new Map();
for (const entry of entries) {
  const story = String(entry.source.story ?? "");
  if (!stories.has(story)) stories.set(story, []);
  stories.get(story).push(entry);
}
const sortedStories = [...stories.entries()].sort((left, right) => compareNatural(left[0], right[0]));
const beamLineMerges = [];
let currentRow = 4;
if (sortedStories.length === 0) {
  styleMergedLabel(sheet, currentRow, 1, columns.length, "NO ROWS FOR THIS BEAM ROLE", {
    fill: colors.paleBlue,
    font: { color: colors.navy, bold: true, size: 10 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  });
} else {
  for (let storyIndex = 0; storyIndex < sortedStories.length; storyIndex += 1) {
    const [story, storyEntries] = sortedStories[storyIndex];
    styleMergedLabel(sheet, currentRow, 1, columns.length, `FLOOR: ${story}`, {
      fill: colors.paleBlue,
      font: { color: colors.navy, bold: true, size: 10 },
      horizontalAlignment: "left",
      verticalAlignment: "center",
    });
    sheet.getRange(`A${currentRow}`).format.rowHeight = 22;
    writeHeaders(sheet, currentRow + 1);
    const dataStart = currentRow + 3;
    const dataEnd = dataStart + storyEntries.length - 1;
    sheet.getRange(`A${dataStart}:${lastColumn}${dataEnd}`).values = storyEntries.map((entry) =>
      columns.map(([key]) => entry.values[key] ?? null));
    for (let offset = 0; offset < storyEntries.length; offset += 1) {
      const entry = storyEntries[offset];
      const row = dataStart + offset;
      const range = sheet.getRange(`A${row}:${lastColumn}${row}`);
      range.format = {
        fill: entry.reason ? colors.paleRed : colors.white,
        font: { color: entry.reason ? colors.red : colors.text, bold: Boolean(entry.reason), size: 9 },
        ...center,
      };
      range.format.rowHeight = 24;
      if (entry.reason) {
        workbook.comments.addThread({ cell: sheet.getRange(`B${row}`) }, exceptionComment(entry));
        workbook.comments.addThread({ cell: sheet.getRange(`H${row}`) }, `Bottom layout candidate: ${entry.values.b1}\nReason: ${entry.reason}`);
      }
    }
    let lineStart = dataStart;
    let previousLine = null;
    for (let offset = 0; offset < storyEntries.length; offset += 1) {
      const row = dataStart + offset;
      const line = String(storyEntries[offset].source.beam_line ?? "");
      if (previousLine === null) {
        previousLine = line;
        lineStart = row;
      } else if (line !== previousLine) {
        if (row - lineStart > 1) beamLineMerges.push(`A${lineStart}:A${row - 1}`);
        previousLine = line;
        lineStart = row;
      }
    }
    if (previousLine !== null && dataEnd - lineStart >= 1) {
      beamLineMerges.push(`A${lineStart}:A${dataEnd}`);
    }
    currentRow = dataEnd + 2;
    if (storyIndex !== sortedStories.length - 1) sheet.getRange(`A${dataEnd + 1}:${lastColumn}${dataEnd + 1}`).format.rowHeight = 8;
  }
}

const maxRow = Math.max(currentRow, 12);
const widths = beamType === "PB"
  ? [17, 16, 10, 10, 16, 16, 16, 18, 19, 19, 12, 19]
  : [17, 16, 10, 10, 16, 16, 16, 18, 19, 12, 19];
for (let index = 0; index < widths.length; index += 1) {
  sheet.getRange(`${colLetter(index)}1:${colLetter(index)}${maxRow}`).format.columnWidth = widths[index];
}

// Render from the live artifact workbook before exporting it.  Importing a
// large openpyxl workbook back into artifact-tool can mis-render the first
// column when it contains many vertical beam-line merges, even though the
// exported XLSX merge records are valid.  The preview is captured before
// those vertical merges are applied so the inspection image remains legible;
// the final XLSX receives the exact merge records immediately afterwards.
await fs.mkdir(previewDir, { recursive: true });
const preview = await workbook.render({
  sheetName: "整理梁表",
  range: `A1:${lastColumn}${Math.min(maxRow, 35)}`,
  scale: 1.2,
  format: "png",
});
await fs.writeFile(
  path.join(previewDir, "schedule.png"),
  new Uint8Array(await preview.arrayBuffer()),
);
console.log(`PREVIEW=${path.join(previewDir, "schedule.png")}`);

for (const merge of beamLineMerges) sheet.mergeCells(merge);

const inspect = await workbook.inspect({
  kind: "region",
  sheetId: "整理梁表",
  range: `A1:${lastColumn}${Math.min(maxRow, 25)}`,
  maxChars: 6000,
});
console.log(inspect.ndjson);
const formulaCheck = await workbook.inspect({
  kind: "formula",
  sheetId: "整理梁表",
  range: `A1:${lastColumn}${maxRow}`,
  maxChars: 2000,
  options: { maxResults: 20 },
});
console.log(formulaCheck.ndjson);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
try {
  await xlsx.save(outputPath);
} finally {
  await fs.rm(`${outputPath}.inspect.ndjson`, { force: true });
}
console.log(`OUTPUT=${outputPath}`);
