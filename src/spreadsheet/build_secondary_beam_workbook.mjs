// Compatibility entrypoint for the secondary workbook.
//
// Secondary payloads contain raw_rows/review_rows/final_rows filtered to SB
// beams. Reuse the canonical artifact-tool renderer so Node and portable
// Python outputs share the same static single-sheet contract.
await import("./build_rebar_workbook.mjs");
