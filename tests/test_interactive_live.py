from unittest.mock import patch, Mock
from data_providers.providers import interactive_options as options, interactive_underlying as underlying


def test_futu_option_code_and_unknown_book_clock():
    symbol='SPY261120P00600000'
    assert options.futu_code(symbol)=='US.SPY261120P600000'
    with patch.object(options,'futu_snapshot',return_value=[dict(code='US.SPY261120P600000',bid_price=1,ask_price=2,update_time='2026-10-26 08:00:00')]):
        q=options.futu([symbol])['rows'][0]['quote']
    assert q['quote_time'] is None
    assert not q['executable_reference']


def test_theta_exact_contract_and_eastern_clock():
    response=Mock(status_code=200,content=b'csv')
    response.text='symbol,expiration,strike,right,timestamp,bid,ask,bid_size,ask_size\nSPY,2026-11-20,600,put,2026-10-26T08:00:00,1,2,4,5\nSPY,2026-11-20,601,put,2026-10-26T08:00:00,9,10,1,1\n'
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    with patch.object(options,'ThetaDataClient',return_value=Mock(api_key=None,base_url='http://local')),patch.object(options.requests,'get',return_value=response):
        rows=options.theta(['SPY261120P00600000'])['rows']
    assert len(rows)==1
    assert rows[0]['quote']['quote_time']=='2026-10-26T12:00:00+00:00'


def test_overnight_reference_uses_boats_raw_bars():
    requests=[]
    def request(path, params):
        requests.append((path,params))
        if 'trades' in path:
            return {'trades':{'SPY':{'p':600,'t':'2026-10-23T01:00:00Z'}}}
        return {'bars':{'SPY':[{'t':'2026-10-22T00:59:00Z','c':599,'v':10}]}}
    with patch.object(underlying,'request',side_effect=request):
        row=underlying.alpaca('SPY','overnight')
    assert row['bars'][0]['close']==599
    assert requests[1][1]['feed']=='boats'
    assert requests[1][1]['adjustment']=='raw'
