from datetime import date

import duckdb
import pytest

from data_providers.provenance import ensure, source_descriptor, tag_row, receipt_envelope


def test_unknown_legacy_is_explicit_and_source_iv_is_not_compared():
    original = {'day': date(2026, 10, 29), 'atm_iv': .3, 'source': 'ibkr_tws30d'}
    descriptor = source_descriptor('history', 'iv_history', list(original))
    tagged = tag_row(original, descriptor=descriptor, release='pinned')
    assert tagged['datasource_tag'] == 'ibkr_tws30d'
    assert tagged['consistency_tag'] == 'cross_source_inconsistent'
    assert tagged['comparison_status'] == 'not_compared'
    assert tagged['provenance_status'] == 'recorded_source_unlinked'
    assert 'datasource_tag' not in original
    unknown = tag_row({'price': 1}, descriptor=source_descriptor('universe', 'prices', ['price']))
    assert unknown['datasource_tag'] == 'legacy_unverified:universe.prices'


def test_new_iv_cannot_lose_source_or_claim_comparison():
    with duckdb.connect() as con:
        ensure(con)
        for source in ['', None]:
            with pytest.raises(duckdb.ConstraintException):
                con.execute('''INSERT INTO iv_observations_v2
                    (instrument_id,stored_underlying,market,day,source,datasource_tag,
                     method_id,provenance_status) VALUES ('SPY','SPY','US','2026-10-29',?,?,
                     'vendor_v1','raw_checksum_verified')''', [source, source])


def test_raw_envelope_requires_source_and_keeps_checksums(tmp_path):
    receipt = tmp_path / 'receipt.json'
    raw = tmp_path / 'raw.csv'
    receipt.write_text('{}')
    raw.write_text('native bytes')
    with pytest.raises(ValueError):
        receipt_envelope(datasource_tag='', provider='vendor', receipt_path=receipt)
    item = receipt_envelope(datasource_tag='vendor.feed.v1', provider='vendor',
                            receipt_path=receipt, raw_path=raw)
    assert len(item['raw_sha256']) == 64
    assert len(item['original_receipt_sha256']) == 64
    assert raw.read_text() == 'native bytes'


@pytest.mark.parametrize('reference_column', ['receipt_reference', 'evidence_reference'])
def test_native_receipt_reference_survives_tagging(reference_column):
    row = {'price': 1, 'source': 'exchange_native', reference_column: '/raw/native.json'}
    tagged = tag_row(row, descriptor=source_descriptor('cn', 'daily', list(row)))
    assert tagged['source_receipt_reference'] == '/raw/native.json'
    assert tagged['source_receipt_reference_column'] == reference_column
