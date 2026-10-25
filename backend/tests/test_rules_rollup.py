"""Step 3: rules and risk roll-ups, checked against an independent implementation
of the scoring spec applied to the ground truth."""

from collections import defaultdict
from datetime import date, timedelta

from app import config
from tests.conftest import person

W = config.SEVERITY_WEIGHTS


def expected_alerts(truth):
    """Spec: daily score = sum of weights; rolling = last 7 days; immediate if any high
    that day, trend if rolling >= threshold. Returns {person: {day: alert}}."""
    daily = defaultdict(lambda: defaultdict(list))
    for s in truth.signals:
        daily[s.service_user_id][s.day].append(s)
    end = date.fromisoformat(truth.as_of[:10])
    out = {}
    for su, days in daily.items():
        alerts = {}
        d = date.fromisoformat(min(days))
        while d <= end:
            today = days.get(d.isoformat(), [])
            window = [s for i in range(7) for s in days.get((d - timedelta(days=i)).isoformat(), [])]
            rolling = sum(W[s.severity] for s in window)
            if any(s.severity == "high" for s in today):
                alerts[d.isoformat()] = "immediate"
            elif rolling >= config.TREND_ALERT_THRESHOLD:
                alerts[d.isoformat()] = "trend"
            d += timedelta(days=1)
        out[su] = alerts
    return out


def test_alerts_match_the_spec(ro, truth):
    got = defaultdict(dict)
    for r in ro.execute("SELECT service_user_id, day, alert FROM su_daily_risk WHERE alert <> 'none'"):
        got[r[0]][r[1]] = r[2]
    assert dict(got) == {su: a for su, a in expected_alerts(truth).items() if a}


def test_gradual_declines_alert_before_their_first_high_severity_event(ro, truth):
    for su, p in truth.people.items():
        if p["scenario"] != "gradual_decline":
            continue
        first_alert = ro.execute("SELECT MIN(day) FROM su_daily_risk WHERE service_user_id = ? AND alert <> 'none'",
                                 (su,)).fetchone()[0]
        first_high = min(s.day for s in truth.signals_for(su) if s.severity == "high")
        assert first_alert < first_high


def test_quiet_people_stay_quiet(ro, truth):
    for kind in ("routine", "negation_traps", "documentation_gaps"):
        assert ro.execute("SELECT MAX(rolling_7d) FROM su_daily_risk WHERE service_user_id = ?",
                          (person(truth, kind),)).fetchone()[0] == 0


def test_documentation_gaps(ro, truth):
    rows = {(r[0], r[1]) for r in ro.execute(
        "SELECT record_id, detail FROM record_issues WHERE issue = 'completed_without_comment'")}
    assert rows == {tuple(x) for x in truth.completed_without_comment}


def test_status_contradictions(ro, truth):
    assert [r[0] for r in ro.execute("SELECT record_id FROM record_issues WHERE issue = 'status_contradiction'")] \
        == truth.contradictions


def test_assessments_not_reviewed_after_high_severity_events(ro, truth):
    expected = {s.service_user_id for s in truth.signals if s.severity == "high"}
    got = {r[0] for r in ro.execute(
        "SELECT DISTINCT service_user_id FROM record_issues WHERE issue = 'assessment_not_reviewed'")}
    assert got == expected
