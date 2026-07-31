import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, previewKey, outputPath] = process.argv.slice(2);
if (!inputPath || !previewKey || !outputPath) {
  throw new Error(
    "usage: node render_rebar_workbook_preview.mjs <input.xlsx> <preview-key> <output.png>",
  );
}

const previews = {
  raw_left: ["原始分跨数据", "A1:V14"],
  raw_demands: ["原始分跨数据", "W1:AV14"],
  raw_trace: ["原始分跨数据", "AW1:BM14"],
  review_base: ["配筋调整复核", "A1:S14"],
  review_rebar: ["配筋调整复核", "T1:BL14"],
  review_controls: ["配筋调整复核", "BM1:CU14"],
  final_schedule: ["整理梁表", "A1:S18"],
};
if (!(previewKey in previews)) {
  throw new Error(`unknown preview key: ${previewKey}`);
}

const [sheetName, range] = previews[previewKey];
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const image = await workbook.render({
  sheetName,
  range,
  scale: 1.2,
  format: "png",
});
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, new Uint8Array(await image.arrayBuffer()));
console.log(`PREVIEW=${outputPath}`);
