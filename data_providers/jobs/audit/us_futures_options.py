"""Publish honest futures-option coverage from existing evidence, without inventing IV."""
import json
import math
from datetime import UTC, datetime

from data_providers import MarketData
from data_providers.jobs.history.futures_store import merge, open_stage, upsert
from data_providers.jobs.history.maintain_us_futures import universe
from data_providers.lake import digest
from data_providers.runtime import data_home


def legacy_iv_rows(rows, configured, release):
    labels = {root + ':' + venue: (root, venue) for root, venue in configured}
    result = []
    for label, source, day, value in rows:
        if label not in labels or source != 'ibkr_tws30d':
            continue
        if value is None or not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid legacy futures IV; preserve and investigate before migration')
        root, venue = labels[label]
        result.append((source, venue, root, venue + ':' + root + ':ibkr_contfuture_legacy', day, value,
                       30.0, 'IBKR_reported_moneyness_unspecified', 'IBKR_ContFuture_roll_unspecified',
                       'IBKR_OPTION_IMPLIED_VOLATILITY_model_unspecified', 'legacy_contfuture_iv_v1',
                       None, '[]', None, None, 'lake:' + release + ':iv_history:' + label + ':' + str(day)))
    return result


def build(home=None):
    m = MarketData(home, backend="parquet")
    stamp = datetime.now(UTC)
    run = stamp.strftime("futures-option-coverage-%Y%m%dT%H%M%S%fZ")
    path, stage = open_stage(m.home, run)
    try:
        stage.execute('BEGIN')
        with m._connection("us_futures_option_chains") as (con, _):
            # Attach only views from the same pinned release; never open live ingestion stores.
            for table in ("us_futures_contracts", "us_futures_option_daily", "us_futures_option_settlements"):
                con.execute("CREATE VIEW " + table + " AS " + m.release.query("history", table))
            if "us_futures_iv" in m.release.manifest["stores"]["history"]["tables"]:
                con.execute("CREATE TABLE us_futures_iv AS " + m.release.query("history", "us_futures_iv"))
                # CTAS drops keys; recreate the table with its canonical constraints.
                con.execute("ALTER TABLE us_futures_iv ADD PRIMARY KEY(source,venue,root,series_id,day)")
            else:
                from data_providers.data.schema.futures import DDL
                con.execute("CREATE TABLE us_futures_iv (" + DDL["us_futures_iv"] + ")")
            con.execute("CREATE VIEW iv_history AS " + m.release.query("history", "iv_history"))
            configured = universe(m.home / 'plans/ibkr_iv_universe_scoped.txt')
            legacy = legacy_iv_rows(con.execute("SELECT underlying,source,day,atm_iv FROM iv_history "
                                               "WHERE source='ibkr_tws30d' AND contains(underlying,':')").fetchall(),
                                    configured, m.release.id)
            upsert(stage, 'us_futures_iv', legacy)
            upsert(con, 'us_futures_iv', legacy)
            roots = con.execute("""SELECT DISTINCT venue,root FROM (
                SELECT venue,root FROM us_futures_option_chains UNION ALL
                SELECT venue,root FROM us_futures_option_daily UNION ALL
                SELECT venue,root FROM us_futures_option_settlements UNION ALL
                SELECT venue,root FROM us_futures_contracts WHERE sec_type='FOP' UNION ALL
                SELECT venue,root FROM us_futures_iv) ORDER BY ALL""").fetchall()
            rows = []
            for venue, root in roots:
                counts = {}
                for name in ("us_futures_option_chains", "us_futures_option_daily", "us_futures_option_settlements"):
                    counts[name] = con.execute(f"SELECT count(*),max(day) FROM {name} WHERE venue=? AND root=?",
                                               [venue, root]).fetchone()
                confirmed = con.execute("""SELECT count(DISTINCT contract_id) FROM (
                    SELECT contract_id FROM us_futures_contracts WHERE venue=? AND root=? AND sec_type='FOP'
                    UNION ALL SELECT contract_id FROM us_futures_option_daily WHERE venue=? AND root=?
                    UNION ALL SELECT contract_id FROM us_futures_option_settlements WHERE venue=? AND root=?)""",
                    [venue, root] * 3).fetchone()[0]
                series = con.execute("""SELECT source,series_id,method_version,tenor_days,roll_method,
                    count(DISTINCT day) sessions,min(day),max(day),min(atm_iv),max(atm_iv)
                    FROM us_futures_iv WHERE venue=? AND root=? GROUP BY ALL ORDER BY ALL""", [venue, root]).fetchall()
                best = max((s[5] for s in series), default=0)
                status = "missing" if not best else "partial" if best < 252 else "available_252"
                if best and all(s[8] == s[9] for s in series):
                    status = "flat"
                dates = [counts[t][1] for t in ("us_futures_option_daily", "us_futures_option_settlements") if counts[t][1]]
                detail = {"input_release": m.release.id, "iv_series_source_method_separated": series,
                          "chain_ladders_are_not_confirmed_cartesian_contracts": True,
                          "confirmed_contract_count_includes_expired_not_current_inventory": True,
                          "rankability": "not_established; calendar, freshness and method validation required",
                          "asset_class": "futures_option", "equity_ticker_alias_permitted": False,
                          "input_sources": {t: con.execute(f"SELECT source,count(*) FROM {t} WHERE venue=? AND root=? GROUP BY source ORDER BY source", [venue,root]).fetchall() for t in counts},
                          "remaining_inputs": ["verified linked forward/expiry/style/multiplier and discount inputs",
                                               "documented comparable IV tenor and roll methodology",
                                               "actual quote/settlement source coverage and historical availability"],
                          "market_completeness": "Observed configured inventory only; missing venues and direct bond options remain unknown"}
                rows.append(("provider_coverage_audit", venue, root, stamp.date(), status, best, confirmed,
                             counts["us_futures_option_chains"][0], counts["us_futures_option_daily"][0],
                             counts["us_futures_option_settlements"][0], max(dates) if dates else None,
                             "contracts", json.dumps(detail, default=str), stamp))
            upsert(stage, "us_futures_option_coverage", rows)
            stage.execute('COMMIT')
    finally:
        stage.close()
    proof = merge(m.home, path)
    folder = m.home / "var/maintenance" / run
    folder.mkdir()
    report = {"input_release": m.release.id, "roots": len(rows), "merge": proof,
              "iv_values_created": 0, "legacy_iv_values_preserved": len(legacy),
              "legacy_label_parser": "data_providers.jobs.history.fetch_iv_history_ibkr.parse_line",
              "configured_universe_sha256": digest(m.home / 'plans/ibkr_iv_universe_scoped.txt'),
              "legacy_iv_partitions": m.release.table('history', 'iv_history')['partitions'],
              "status": "coverage_and_legacy_iv_delivered_model_inputs_pending",
              "market_wide_optionability_master": "pending; observed roots are not an authoritative all-market inventory"}
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return folder, report


if __name__ == "__main__":
    folder, report = build(data_home())
    print(json.dumps({"path": str(folder), **report}))
