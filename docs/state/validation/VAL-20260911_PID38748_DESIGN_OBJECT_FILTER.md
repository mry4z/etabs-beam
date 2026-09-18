# VAL-20260911 · PID 38748 design-object filter and full-model run

## Scope

This validation uses ETABS 22.7.0, PID `38748`, the v2 working tree, and the
model reported by ETABS as:

`E:\\051 SAP_ETABS MODEL\\IRAQ-HOSPITAL\\20260907\\EMHS-L2.v6.5.8.4.EDB`

The run used the public ETABS OAPI and did not perform geometry/name/Group
writeback or save the model. Because the model initially had no concrete design
results, `DesignConcrete.StartDesign()` was called once with return code `0`;
results then became available. The model remained locked and the display units
remained `N_mm_C`.

## Design-object filtering

Beam recognition now requires an object-level `GetSummaryResultsBeam_2` result
when model-level concrete design results are available. Objects with
`NO_RESULTS`, `SKIPPED`, or an API read failure are excluded before topology
grouping, span construction, Tower-selected beam processing, and PB/SB table
generation. `PARTIAL` results remain in the workflow and retain their audit
status. Exclusions are written to `excluded_non_design_objects.json`.

In this model, 112 objects were excluded. All 112 use section `RIB 150X370`
and have no concrete design stations. Source object `6303` is included in this
set and no longer appears in the topology span CSV or either rebar payload.

## Full read-only result

Output directory:

`build/output/validation/etabs-pid-38748-20260911-full-filtered`

- 3,933 design-object Frames retained;
- 3,078 spans: PB 1,579, SB 1,499;
- rebar payload alignment: raw/review/final 3,078/3,078/3,078;
- all 3,078 retained rows have an ETABS design result;
- topology blocking diagnostics: 0;
- SB schedule rows: 1,484; SB exceptions: 15.

## Automatic-rebar review summary

The 28 hard engine failures are:

| Type | Count | Meaning |
|---|---:|---|
| `MIXED_SECTION_REVIEW_REQUIRED` | 19 | One physical span contains multiple section properties; a single automatic layout is not accepted. |
| `MIXED_FRAME_TYPE_REVIEW_REQUIRED` | 4 | One physical span mixes explicit/Program Determined FrameType values; the detailing profile is ambiguous. |
| `NO_ALLOWED_SPACING` | 5 | No permitted stirrup/spacing candidate satisfied the local demand and geometry constraints. |

No `FRAME_TYPE_UNRESOLVED` rows remain after excluding the non-design RIB
objects. The five no-spacing rows are `GF-FG-PB9-13`, `T1-L1-PB4-13`,
`T1-L2-PB4-14`, `T1-L3-PB4-14`, and `T1-LR-PB5-13`.

The remaining `constructability_status` reviews are not the same as engine
failure:

- 1,663 rows retain the current-phase audit items
  `ANCHORAGE_UNVERIFIED`, `SPLICE_UNVERIFIED`, and
  `TORSION_PERIMETER_LAYOUT_UNVERIFIED`; these remain outside the current
  project validation scope.
- 1,387 rows, mostly Program Determined PB rows, retain
  `CAPACITY_SHEAR_SOURCE_UNVERIFIED`, `SMF_DESIGN_SOURCE_UNVERIFIED`, and
  `SMF_SUPPORT_FACE_UNRESOLVED`. Their longitudinal/SMF checks may pass, but
  the support-face/capacity source is not accepted as fully verified for a
  construction drawing.

## Tower cross-check

The separate read-only Tower preflight used the same PID and model. It used the
public-table path with zero Tower switches and zero selection writes. It checked
4,102 Tower beams, passed 4,099, and retained 3 review beams (5 issue records):
7251, 7252, 58, point 12436, and point 9677. Their reasons are mixed endpoint
Tower assignment or point Tower mismatch; no Group was created.

## Offline regression

The design-object filter regression and affected source/CLI/Tower/snapshot tests
passed: **55 passed**. Python compilation and `git diff --check` also passed.

## Engine review and offline correction — 2026-09-11

Two engine/replay defects were reproduced and corrected after this run:

- Initial hoop selection assumed the largest enabled longitudinal diameter and
  rejected spacing before trying smaller bars. With explicit 40 mm clear cover,
  a 500 x 460 mm SMF section failed with assumed T25 (d/4 = 99.375 mm), although
  the actual 3T20 layout has d = 400 mm and accepts T10@100 end hoops. Initial
  selection now tries the remaining enabled diameters on spacing failure;
  the existing final row, spacing and SMF checks still apply.
- Offline replay retained previous `NO_ALLOWED_SPACING` and row-catalogue
  failures without evaluating the current engine. These solver outcomes are
  now retried. Mixed-section, mixed-frame-type and partial-source review gates
  remain intact. Spacing errors now record the trial bar diameter, governing
  limit and allowed spacing catalogue.

Focused regression: **8 passed** (smaller-bar initialization, no-spacing
fail-closed behavior, solver retry and source-gate preservation).

All 28 failed source rows were replayed offline after the corrections. Results
remain **19 mixed-section, 4 mixed-frame-type, 5 no-spacing**. The latter are
genuine conflicts with DET-R012's allowed spacings of 100/150/200 mm; all enabled
initial longitudinal diameters were tried. For the T25 initial trial, the
300 mm-deep beams report a 59.375 mm limit and the 400 mm-deep beams report
84.375 mm. These are trial limits, not newly approved detailing spacings.
The source uses explicit 40 mm clear cover; its separate ETABS centroid-cover
field of 50 mm is not the active layout basis.

Evidence: [28-row replay](../../../build/output/validation/etabs-pid-38748-engine-review/failed_rows_replayed.json).
The original full-model output was not overwritten. This follow-up did not
connect to ETABS, start design, change the project spacing catalogue, or build
a package. Resolving the five catalogue conflicts requires an explicit project
spacing-policy decision; resolving the other 23 rows requires source/profile
review rather than automatic weakening of the input gates.
