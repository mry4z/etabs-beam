# VAL-20260910 · v0.4.2 physical-row coordination

## 2026-09-11 correction — source fix and offline replay evidence

The historical r02/r04 claims below are NOT acceptance evidence. r04 contains an
incorrect bottom effective depth and incomplete geometry/capacity/support validation.
Do not use r04 as an accepted reinforcement-design release. Its artifacts are
retained as failure evidence. A count of 64 `OK` statuses did not establish compliance.

Current source corrections: effective depth is depth minus the centroid distance
for BOTH tension faces; centroid-cover mode translates all rows to the specified
total physical centroid and rejects nonpositive inferred cover. Per-row horizontal
clearance and joint top/bottom envelope clearance are checked. Final output now
rechecks first-row hoop support, minimum/required area, existing transverse and
SMF limits and rebuilds the result audit. The finite solver enumerates complete
physical rows, feeds actual multi-row effective depth back into transverse
selection, and backtracks across support states. The latest user clarification
permits different first-row counts within one span while retaining a common
diameter; each local count is checked against its own hoop legs and demand.
At shared supports, corresponding physical rows keep the same layer count and
diameter, while first-row counts may differ; each side independently satisfies
its own hoop-leg support and final geometry checks. Option2 additionally permits
extra rows on the higher-demand side when they can be placed.

Targeted commands and results:

- `python -m pytest tests/unit/test_row_rebar_v042.py tests/unit/test_detailing.py tests/unit/test_pb_stirrup_preferences.py tests/unit/test_sb_top_preferences.py -q --no-cov -p no:cacheprovider`: **90 passed**.
- `python -m pytest tests/unit/test_rebar_workbook.py -q --no-cov -p no:cacheprovider`: **19 passed**.
- `python -m pytest tests/unit/test_render_rebar_workbook_xlsx.py tests/unit/test_render_secondary_beam_xlsx.py -q --no-cov -p no:cacheprovider`: **18 passed**.
- Offline replay after support-count correction: `build/output/validation/20260910-v042-row-rebar/replay-20260911-count-relax/rebar_workbook_payload_20260910_153416.json`; `status=OK`, schema `2.2`, `row_count=64`, `raw/review/final=64`, `coordination=STABLE 64`, `engine=OK 64`, `model_writeback=false`.
- Python static renderer replay in the same directory: PB `rebar_schedule_pb.xlsx`; SB `secondary_schedule_sb.xlsx` with 32 schedule rows and 0 exceptions.

The source payload was not overwritten. The latest replay output contains
**64/64 OK** rows; all 64 have six physical-face audits, complete rows, and
row-x coordinates. This is offline evidence only; no ETABS process was attached.

The review correction is covered by `tests/unit/test_row_rebar_v042.py`: the
support-state fixture checks low/high/reverse segments, same physical-row
diameters, independently supported first-row counts, and the Option2 real-input
path is idempotent. The row contract test file reports 14 passed after this
correction; the detailing and workbook narrow regressions report 53 and 19
passed respectively.

Remaining release gates: run the repository-approved full offline suite in an isolated
coverage/XML location; perform the requested 0.4.2 portable package build and
self-check. ETABS 22/23 real-model GUI acceptance remains open. The prior r02/r04
packages are not accepted releases.

## Historical record (superseded; retained for traceability)

## Scope

This record covers the offline 0.4.2 longitudinal-row coordination implementation. No ETABS process was attached and no model was written.

Input: `F:\2026\Research Study\test temp\run_20260910_153410\rebar_workbook_payload_20260910_153416.json` (64 spans, schema 2.1). The source file was read as bytes and verified unchanged after replay.

## Implementation evidence

- `LongitudinalDesign.rows` is the lossless physical layout: count, diameter, role and credited area.
- A physical row is validated as at least two bars of one diameter; distinct rows may use distinct diameters.
- Workbook JSON schema is 2.2 and records `longitudinal_rows`, row coordination audit, support/seismic context, and final coordinated rows.
- Beam-line coordination groups by story, beam type, beam line and resolved detailing profile. Different profiles on one physical line are validated independently instead of being erased as a profile-mismatch failure.
- Frozen workflow supports `--replay-payload`; it is read-only and does not attach to ETABS.

## Results

Source status: 48 `OK`, 16 historical `BEAM_LINE_COORDINATION_BLOCKED` caused by cross-profile coordination.

v0.4.2 offline replay: 64 `OK`; 64/64 rows contain complete physical-row payloads. The standalone frozen EXE replay returned `status=OK`, `schema_version=2.2`, `row_count=64`, `model_writeback=false`.

Targeted tests: 30 passed (row contract, workbook replay, manifest schema).

The initial r02 review found real construction defects and is superseded by this record: support coordination could drop an additional row, profile grouping hid true adjacent supports, first-row continuity was absent, and geometry was empty. Those defects are now covered by regression checks.

Current affected tests: 24 passed (row contract, actual four-leg examples, support-area regression, SMF feedback, workbook replay). Full offline suite: 375 passed, 1 skipped, 8 failed. The 8 failures are legacy expectations for pre-row multilayer/hanger display and pre-row midspan/SMF preference behavior; they require explicit rule-level test migration and are not represented as passing evidence here.

Corrected package: `build/etabs-plugin/v0.4.2-v2-etabs23-loadable-20260911-r04.zip`. Contract validation passed (`0.4.2.0`), frozen runtime self-test passed, and frozen payload replay returned `OK`, schema 2.2, 64 rows, no model writeback. ETABS 22/23 real-model menu/GUI acceptance remains open.
