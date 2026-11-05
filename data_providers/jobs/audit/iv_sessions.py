"""Source-separated IV coverage against a receipted, cross-checked US calendar."""
import argparse
import calendar
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import duckdb

from data_providers import MarketData
from data_providers.lake import connect_store, digest, literal

NYSE_2025_PDF_SHA256 = "6d719aaaf827c909f362725293c8e932f1b879687e4705eeb8a16f7201fe3355"


class HolidayTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def holiday_dates(html, years):
    parser = HolidayTable()
    parser.feed(html)
    columns, result = {}, set()
    for row in parser.rows:
        if row[0].upper() == "HOLIDAY" and any(text.isdigit() for text in row[1:]):
            columns = {i: int(text) for i, text in enumerate(row) if text.isdigit() and int(text) in years}
            continue
        for i, year in columns.items():
            if i >= len(row):
                raise ValueError("Incomplete NYSE holiday row")
            found = re.search(r"(" + "|".join(calendar.month_name[1:]) + r")\s+(\d{1,2})", row[i])
            if found:
                result.add(date(year, list(calendar.month_name).index(found[1]), int(found[2])))
    if any(sum(d.year == y for d in result) != 10 for y in years):
        raise ValueError("NYSE holiday table does not contain ten named closures per requested year")
    return result


def verified_sessions(folder, start, end):
    # This release deliberately excludes the earlier extraordinary January 2025 closure.
    if start < date(2025, 10, 1) or end > date(2026, 10, 16) or start > end:
        raise ValueError("Verified calendar audit is bounded to 2025-10-01 through 2026-10-16")
    receipts = {}
    for name in ("nyse-holidays", "nyse-pdf", "alpaca-calendar"):
        receipt = json.loads((folder / (name + "-receipt.json")).read_text())
        body = folder / (name + ".body")
        if receipt["http_status"] != 200 or digest(body) != receipt["sha256"]:
            raise ValueError("Invalid calendar source receipt: " + name)
        receipts[name] = receipt
    holidays = holiday_dates((folder / "nyse-holidays.body").read_text(), {2026})
    if receipts["nyse-pdf"]["sha256"] != NYSE_2025_PDF_SHA256:
        raise ValueError("2025 PDF differs from the reviewed source; revalidate its holiday transcription")
    # Manually reviewed page 1 of the pinned NYSE 2024-11-08 release lists these
    # two full closures in Oct–Dec 2025. Nov 28 / Dec 24 early closes stay sessions.
    holidays.update({date(2025, 11, 27), date(2025, 12, 25)})
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5 and start + timedelta(days=i) not in holidays}
    rows = json.loads((folder / "alpaca-calendar.body").read_text())
    actual = [date.fromisoformat(row["date"]) for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("NYSE schedule and Alpaca calendar disagree or window is incomplete")
    # Early closes are full observation sessions; their exact times remain in source rows.
    return sorted(expected), rows, receipts


def coverage(observations, sessions, *, listing_date=None):
    valid = [(day, value) for day, value in observations
             if value is not None and math.isfinite(value) and value > 0]
    days = {day for day, _ in valid}
    calendar_days = set(sessions)
    usable = days & calendar_days
    first = min(days) if days else None
    span = {day for day in sessions if first is not None and day >= first}
    eligible = {day for day in sessions[-252:] if listing_date is None or day >= listing_date}
    missing = sorted(span - days)
    before = sorted(day for day in eligible - days if first is None or day < first)
    return {"first_valid_observation": str(first) if first else None,
            "last_valid_observation": str(max(days)) if days else None,
            "valid_distinct_dates_in_store": len(days), "usable_sessions_in_calendar_window": len(usable),
            "last_252_calendar_sessions_observed": len(days & eligible),
            "eligible_sessions_in_trailing_window": len(eligible),
            "missing_sessions_since_first_observation": len(missing),
            "missing_dates_since_first_observation": list(map(str, missing)),
            "unobserved_sessions_before_first_observation": len(before),
            "unobserved_dates_before_first_observation": list(map(str, before)),
            "non_session_dates_in_calendar_window": list(map(str, sorted(
                day for day in days - calendar_days if sessions[0] <= day <= sessions[-1]))),
            "latest_expected_session": str(sessions[-1]), "latest_session_observed": sessions[-1] in days,
            "underlying_listing_date": str(listing_date) if listing_date else None,
            "calendar_window_history_status": "complete_252" if len(usable) >= 252 else
                                              "partial_60_251" if len(usable) >= 60 else "insufficient_under_60",
            "option_listing_and_earlier_iv_availability": "unknown; underlying listing is not option listing"}


def build(folder, *, home=None, release=None):
    folder = Path(folder)
    sessions, _calendar_rows, receipts = verified_sessions(folder, date(2025, 10, 1), date(2026, 10, 16))
    m = MarketData(home, backend="parquet", release=release)
    listing_path = m.home / "var/maintenance/ivr-request-20261018/coverage-report.json"
    listing = json.loads(listing_path.read_text())["us"]
    rows = []
    with connect_store(m.home, "history", release=m.release.id) as con:
        for symbol in ("DRAM", "MUU", "SPCX", "MDLN"):
            data = con.execute("SELECT source,day,atm_iv FROM iv_history WHERE underlying=? AND day<=?",
                               [symbol, sessions[-1]]).fetchall()
            for source in sorted({r[0] for r in data}):
                result = coverage([(day, value) for src, day, value in data if src == source], sessions,
                                  listing_date=date.fromisoformat(listing[symbol]["listing_reference"]["list_date"]))
                rows.append({"symbol": symbol, "source": source, **result})
    # Write to new versioned output folders, retaining previous audits and failed outputs.
    output = folder / datetime.now(UTC).strftime("audit-%Y%m%dT%H%M%S%fZ")
    output.mkdir()
    (output / "coverage.json").write_text(json.dumps(rows, indent=2) + "\n")
    with duckdb.connect(str(output / "coverage.duckdb")) as con:
        con.execute("CREATE TABLE coverage AS SELECT * FROM read_json_auto(?)", [str(output / "coverage.json")])
        con.execute("CREATE TABLE sessions AS SELECT * FROM read_json_auto(?)", [str(folder / "alpaca-calendar.body")])
        for table in ("coverage", "sessions"):
            path = output / (table + ".parquet")
            con.execute(f"COPY {table} TO {literal(path)} (FORMAT PARQUET)")
            for left, right in ((table, f"read_parquet({literal(path)})"),
                                (f"read_parquet({literal(path)})", table)):
                if con.execute(f"SELECT count(*) FROM (SELECT * FROM {left} EXCEPT ALL SELECT * FROM {right})").fetchone()[0]:
                    raise ValueError("Parquet coverage verification failed")
    report = {"status": "verified_bounded_calendar_comparison", "source_release": m.release.id,
              "calendar_start": str(sessions[0]), "calendar_end": str(sessions[-1]),
              "calendar_sessions": len(sessions), "calendar_receipts": receipts,
              "listing_report_sha256": digest(listing_path), "coverage": rows,
              "calendar_method": "Parsed NYSE 2026 HTML; reviewed pinned PDF 2025 Q4 closures; exact Alpaca date-set cross-check",
              "limitations": ["No claim of option listing or historical IV availability before first observation",
                              "Calendar scope is US cash equities only; CN and futures calendars remain separate",
                              "Tenor continuity and point-in-time availability remain unverified",
                              "This audit does not fill gaps, splice sources, alter IV values or rerun screening"],
              "sha256": {p.name: digest(p) for p in output.iterdir() if p.is_file()}}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--release")
    args = parser.parse_args()
    output, report = build(args.receipts, release=args.release)
    print(json.dumps({"path": str(output), "sessions": report["calendar_sessions"], "rows": len(report["coverage"])}))


if __name__ == "__main__":
    main()
