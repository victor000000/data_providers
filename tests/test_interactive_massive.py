from unittest.mock import Mock, patch
from data_providers.providers import interactive_massive as source


def test_exact_standard_contract_and_delayed_classification():
    response=Mock(status_code=200)
    response.json.return_value={'results':[{'details':{'ticker':'O:SPY261120P00600000','shares_per_contract':100},'last_quote':{'bid':1,'ask':2,'timeframe':'DELAYED','last_updated':1792771200000000000}}]}
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    with patch.object(source,'NEXT',0),patch.object(source,'CACHE',{}),patch.object(source,'load_api_key',return_value='test'),patch.object(source.requests,'get',return_value=response):
        row=source.snapshots(['SPY261120P00600000'])['rows'][0]
    assert row['quote']['feed']=='delayed_or_unknown'
    assert not row['quote']['executable_reference']
