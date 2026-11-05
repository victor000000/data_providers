"""Parse retained SZSE contract reports without network or model assumptions."""
from datetime import date
import json
import math
import re

from data_providers.data import symbols

SOURCE = 'szse_current_contract_report_v1'


def parse(records, *, receipt_path, raw_sha256, received_at):
    def value(row, key):
        item = row.get(key)
        return None if item is None or str(item).strip() in {'', '-', '--', 'nan'} else str(item).strip()

    def number(row, key, *, integer=False, required=False):
        text = value(row, key)
        if text is None:
            if required:
                raise ValueError('Missing contract field: ' + key)
            return None
        result = float(text.replace(',', ''))
        if not math.isfinite(result) or result < 0 or (integer and result != int(result)):
            raise ValueError('Invalid contract numeric field: ' + key)
        return int(result) if integer else result

    def day(row, key, required=False):
        text = value(row, key)
        if text is None:
            if required:
                raise ValueError('Missing contract date: ' + key)
            return None
        return date.fromisoformat(text)

    result = []; seen = set()
    for row in records:
        match = re.fullmatch(r'.*[（(](\d{6})[）)]', value(row, '标的证券简称(代码)') or '')
        if not match:
            raise ValueError('Missing native underlying identity')
        native = match.group(1)
        canonical, resolved = symbols.translate(native, 'szse', 'szse')
        if canonical != 'CN:SZSE:' + native or resolved != native:
            raise ValueError('Conflicting SZSE underlying identity')
        identity = value(row, '合约编码'); code = value(row, '合约代码')
        if not identity or not identity.isdigit() or not code or not code.startswith(native):
            raise ValueError('Invalid native option identity')
        report = day(row, '交易日期', True); expiry = day(row, '到期日', True)
        if expiry < report:
            raise ValueError('Expired contract in current report')
        key = (identity, report)
        if key in seen:
            raise ValueError('Duplicate native contract snapshot')
        seen.add(key)
        right = {'认购': 'C', '认沽': 'P'}.get(value(row, '合约类型'))
        strike = number(row, '行权价', required=True)
        multiplier = number(row, '合约单位', required=True, integer=True)
        if right is None or not strike or not multiplier:
            raise ValueError('Invalid option specification')
        result.append(dict(
            venue='SZSE', exchange_contract_id=identity, contract_code=code,
            underlying=canonical, report_date=report, expiry=expiry,
            last_trade_date=day(row, '最后交易日'), exercise_date=day(row, '行权日'),
            delivery_date=day(row, '交收日'), call_put=right, strike=strike,
            multiplier=multiplier, original_contract_code=value(row, '原合约代码'),
            original_strike=number(row, '原行权价格'),
            original_multiplier=number(row, '原合约单位', integer=True),
            adjustment_flag=value(row, '合约调整'),
            previous_settlement=number(row, '前结算价'), previous_settlement_date=None,
            open_interest=number(row, '合约总持仓'), source=SOURCE,
            source_receipt=str(receipt_path), raw_sha256=raw_sha256,
            received_at=received_at, source_event_utc=None, available_at=None,
            native_fields=json.dumps(row, ensure_ascii=False),
            provenance_status='source_receipt_linked'))
    if not result:
        raise ValueError('Empty contract report')
    return result
