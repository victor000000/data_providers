from unittest.mock import patch, Mock
from data_providers.web_ops.health import probe
from data_providers.web_ops.health_sources import alpaca, http_check


def test_exception_never_leaks_secret_or_url():
    with patch('data_providers.web_ops.health.http_check',side_effect=ValueError('https://secret:pass@host')):
        result=probe('Yahoo')
    assert 'secret' not in str(result) and result['transport']=='unavailable'


def test_http_denial_is_reachable_not_entitled():
    response=Mock(status_code=403)
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    with patch('data_providers.web_ops.health_sources.requests.get',return_value=response):
        result=http_check('https://example.test')
    assert result['transport']=='reachable' and result['access']=='not verified'
    assert result['quote_status']=='not checked'


def test_opra_access_not_assumed_from_connection():
    with patch('data_providers.providers.us_option_data.headers',return_value={}), patch(
            'data_providers.web_ops.health_sources.http_check',return_value={'http_status':403}):
        assert alpaca(True)['access']=='denied'


def test_live_monitor_does_not_repeat_unchanged_entitlement_denials():
    from data_providers.web_ops.health import cached_probe
    from data_providers.web_ops.state import now
    row=dict(source='Alpaca paper',access='denied',checked_at=now())
    with patch('data_providers.web_ops.health.probe') as request:
        result=cached_probe('Alpaca paper',{'Alpaca paper':row})
    request.assert_not_called()
    assert result['checked_at']==row['checked_at'] and 'cache_note' in result
