from data_providers.jobs.data_ops.deliver_capitalization import capitalization


def test_fund_assets_never_use_company_market_cap():
    assert capitalization('ETF', {'quoteType': 'ETF', 'marketCap': 900},
                          '2026-10-18T00:00:00Z', 'receipt', 'sha') is None
    row = capitalization('ETF', {'quoteType': 'ETF', 'totalAssets': 800, 'marketCap': 900,
                                'currency': 'USD'}, '2026-10-18T00:00:00Z', 'receipt', 'sha')
    assert row[3:8] == ('fund_total_assets', 800, 'USD', None, None)


def test_equity_cap_has_unknown_effective_and_historical_availability():
    row = capitalization('EQ', {'quoteType': 'EQUITY', 'marketCap': 100,
                               'totalAssets': 200}, '2026-10-18T00:00:00Z', 'receipt', 'sha')
    assert row[3:8] == ('equity_market_cap', 100, None, None, None)
    for value in (True, float('inf'), float('nan'), 0, -1, None):
        assert capitalization('EQ', {'quoteType': 'EQUITY', 'marketCap': value},
                              '2026-10-18T00:00:00Z', 'receipt', 'sha') is None


def test_short_refresh_preserves_existing_long_history(tmp_path):
    import hashlib
    import json

    import duckdb

    from data_providers.initialize import initialize
    from data_providers.jobs.data_ops.deliver_capitalization import build

    home = initialize(tmp_path / 'runtime')
    folder = home / 'var/maintenance/probe'
    folder.mkdir(parents=True)
    raw = json.dumps({'quoteType': 'ETF', 'totalAssets': 1000}).encode()
    (folder / 'FUND-info.json').write_bytes(raw)
    (folder / 'yahoo-probe.json').write_text(json.dumps({'FUND': {
        'retrieved_at': '2026-10-18T00:00:00Z', 'info_sha256': hashlib.sha256(raw).hexdigest()}}))
    (folder / 'http.jsonl').write_text('')
    (folder / 'ibkr-iv-probe.json').write_text(json.dumps({'source': 'ibkr_tws30d', 'symbols': {
        'FUND': {'status': 'observed', 'bars': [
            {'BarData': {'date': '2026-10-15', 'close': .9}},
            {'BarData': {'date': '2026-10-16', 'close': .3}}]}}}))
    with duckdb.connect(str(home / 'market_history.duckdb')) as con:
        con.execute("INSERT INTO iv_history VALUES ('2025-01-02','FUND',NULL,.1,NULL,'ibkr_tws30d'),"
                    "('2026-10-15','FUND',NULL,.2,NULL,'ibkr_tws30d')")
    assert build(folder, home)['iv_missing_dates_added'] == [('FUND', 1, '2026-10-16')]
    assert build(folder, home)['iv_missing_dates_added'] == []
    with duckdb.connect(str(home / 'market_history.duckdb'), read_only=True) as con:
        assert con.execute('SELECT atm_iv FROM iv_history ORDER BY day').fetchall() == [(.1,), (.2,), (.3,)]
