from data_providers.providers.ibkr_history_errors import (
    historical_farm_available, historical_farm_states,
)


def test_apac_on_demand_with_period_suffix_is_available_for_real_work():
    events = [dict(code=2107, message=(
        'HMDS data farm connection is inactive but should be available upon '
        'demand.apachmds'))]
    assert historical_farm_states(events) == {'apachmds': 2107}
    assert historical_farm_available(events)


def test_latest_event_recovers_a_named_farm_without_region_whitelist():
    events = [dict(code=2105, message='HMDS is disconnected:ushmds2a'),
              dict(code=2106, message='HMDS is connected:ushmds2a')]
    assert historical_farm_available(events)
    assert not historical_farm_available(list(reversed(events)))


def test_unknown_or_other_disconnected_farm_fails_closed():
    assert not historical_farm_available([])
    assert not historical_farm_available([dict(code=2107, message='unparsed')])
    assert not historical_farm_available([
        dict(code=2107, message='Available upon demand:apachmds'),
        dict(code=2105, message='Disconnected:ushmds'),
    ])
