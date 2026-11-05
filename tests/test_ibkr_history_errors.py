from data_providers.providers.ibkr_history_errors import classify_history_error, is_history_denial


def test_same_hmds_code_has_distinct_operational_meanings():
    examples = [
        ('No market data permissions for NYSE STK', 'permission_denied', True),
        ('Request Timed Out', 'service_timeout', False),
        ('API historical data query cancelled: 295', 'cancelled', False),
        ('HMDS query returned no data: ORCX@SMART Option_Implied_Volatility', 'no_data', False),
        ('Query failed', 'service_query_failed', False),
        ('Unrecognized broker message', 'unclassified_hmds_error', False),
    ]
    for message, kind, denial in examples:
        event = {'code': 162, 'message': message}
        assert classify_history_error(event) == kind
        assert is_history_denial(event) is denial


def test_security_and_entitlement_failures_remain_blocked():
    for code in [200, 321, 354, 10167, 10168, 10186]:
        assert is_history_denial({'code': code, 'message': ''})
    assert classify_history_error({'code': 2105, 'message': 'HMDS disconnected'}) == 'farm_status'
