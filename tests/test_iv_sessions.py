import json
from datetime import date

import pytest

from data_providers.jobs.audit.iv_sessions import coverage, holiday_dates, verified_sessions


def test_gaps_are_source_local_and_pre_history_is_not_called_missing():
    sessions = [date(2026, 10, d) for d in (8, 9, 12, 13, 14, 15, 16)]
    observations = [(date(2026, 10, 12), .3), (date(2026, 10, 12), .31),
                    (date(2026, 10, 13), float("nan")), (date(2026, 10, 14), 0),
                    (date(2026, 10, 15), -.2), (date(2026, 10, 16), .4),
                    (date(2026, 10, 10), .35)]
    result = coverage(observations, sessions, listing_date=date(2026, 10, 9))
    assert result["usable_sessions_in_calendar_window"] == 2
    assert result["missing_dates_since_first_observation"] == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert result["unobserved_dates_before_first_observation"] == ["2026-10-09"]
    assert result["non_session_dates_in_calendar_window"] == ["2026-10-10"]
    assert result["latest_session_observed"]


def test_empty_and_stale_sources_remain_gaps():
    sessions = [date(2026, 10, d) for d in (15, 16)]
    result = coverage([], sessions)
    assert result["missing_sessions_since_first_observation"] == 0
    assert result["unobserved_sessions_before_first_observation"] == 2
    assert not result["latest_session_observed"]
    assert coverage([(sessions[0], .2)], sessions)["missing_dates_since_first_observation"] == ["2026-10-16"]


def test_html_year_columns_and_incomplete_calendar_fail_closed():
    entries = ["January 1", "January 19", "February 16", "April 3", "May 25", "June 19",
               "July 3", "September 7", "November 26", "December 25"]
    html = "<table><tr><th>Holiday</th><th>2026</th><th>2027</th></tr>" + "".join(
        f"<tr><td>Holiday</td><td>Friday, {text}</td><td>irrelevant</td></tr>" for text in entries) + "</table>"
    assert date(2026, 7, 3) in holiday_dates(html, {2026})
    with pytest.raises(ValueError):
        holiday_dates(html, {2025, 2026})


def test_calendar_receipt_tampering_and_unreviewed_windows_fail(tmp_path):
    (tmp_path / "nyse-holidays.body").write_text("tampered")
    (tmp_path / "nyse-holidays-receipt.json").write_text(json.dumps({"http_status": 200, "sha256": "bad"}))
    with pytest.raises(ValueError, match="receipt"):
        verified_sessions(tmp_path, date(2025, 10, 1), date(2026, 10, 16))
    with pytest.raises(ValueError, match="bounded"):
        verified_sessions(tmp_path, date(2025, 1, 1), date(2026, 10, 16))
