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
  schedule: ["整理梁表", "A1:L35"],
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
