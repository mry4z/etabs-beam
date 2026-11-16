from types import SimpleNamespace

from src.design.constructability import ParentLongitudinalRun, parent_longitudinal_checks


def face(area, depth=700):
    return SimpleNamespace(provided_area_mm2=area, effective_depth_mm=depth)


def run(name, area, depth=700):
    f = face(area, depth)
    return ParentLongitudinalRun(name, 400, 30, 420, f, f, f, f, f, f)


def test_parent_maximum_detects_weak_run():
    checks = parent_longitudinal_checks(runs=[run('I', 500), run('J', 5000)])
    failed = [c for c in checks if c.status == 'SMF_STRENGTH_RATIO_FAILED']
    assert len(failed) == 6
    assert all(c.source == 'I' and c.limit == .25 for c in failed)
    assert sum(c.rule_id == 'SMF-PARENT-POSITIVE-NEGATIVE' for c in checks) == 2


def test_face_depth_and_missing_depth():
    a = run('I', 1000, 700)
    b = ParentLongitudinalRun('J', 400, 30, 420, face(1000, 300), face(1000, 900),
                              face(1000, 700), face(1000, 700), face(1000, 700), face(1000, 700))
    checks = parent_longitudinal_checks(runs=[a, b])
    left = [c for c in checks if c.source == 'J' and 'left' in c.message and c.limit == .25]
    assert left[0].actual > left[1].actual
    bad = ParentLongitudinalRun('bad', 400, 30, 420, face(1000, None), face(1000), face(1000), face(1000), face(1000), face(1000))
    assert parent_longitudinal_checks(runs=[a, bad])[0].status == 'SMF_PARENT_LONGITUDINAL_UNRESOLVED'
