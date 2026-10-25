"""Step 3: rules and risk roll-ups."""

from tests.conftest import needs_source

pytestmark = needs_source


def test_documentation_gaps(ro):
    items, visits = ro.execute(
        "SELECT COUNT(*), COUNT(DISTINCT record_id) FROM record_issues WHERE issue = 'completed_without_comment'"
    ).fetchone()
    assert (items, visits) == (13, 7)


def test_status_contradiction_flags_visit_293(ro):
    rows = ro.execute("SELECT record_id FROM record_issues WHERE issue = 'status_contradiction'").fetchall()
    assert [r[0] for r in rows] == [293]


def test_assessments_not_reviewed_after_serious_events(ro):
    people = {r[0] for r in ro.execute(
        "SELECT DISTINCT service_user_id FROM record_issues WHERE issue = 'assessment_not_reviewed'")}
    assert people == {6, 7, 8, 9, 10}


def test_trend_alert_fires_before_the_first_high_severity_event(ro):
    """Alfred (6) and Doris (7): the trend alert fires on 13 Nov, two days before
    the first high-severity note (15 Nov)."""
    for su in (6, 7):
        first_trend = ro.execute(
            "SELECT MIN(day) FROM su_daily_risk WHERE service_user_id = ? AND alert IN ('trend', 'immediate')", (su,)
        ).fetchone()[0]
        first_high = ro.execute(
            "SELECT MIN(day) FROM visit_signals WHERE service_user_id = ? AND severity = 'high'", (su,)
        ).fetchone()[0]
        assert first_trend == "2025-11-13"
        assert first_high == "2025-11-15"


def test_single_events_trigger_immediate_alerts(ro):
    rows = ro.execute(
        "SELECT service_user_id, day FROM su_daily_risk WHERE alert = 'immediate' AND service_user_id IN (8, 9, 10)"
    ).fetchall()
    assert {(r[0], r[1]) for r in rows} == {(8, "2025-11-06"), (9, "2025-11-09"), (10, "2025-11-12")}


def test_quiet_people_stay_quiet(ro):
    assert ro.execute(
        "SELECT MAX(rolling_7d) FROM su_daily_risk WHERE service_user_id IN (1, 2, 3, 4, 5)"
    ).fetchone()[0] == 0
