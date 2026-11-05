from unittest.mock import Mock, patch
from data_providers.providers import interactive_reference as source


def test_yahoo_uses_completed_positive_volume_bar_not_inflight():
    response=Mock(status_code=200)
    response.json.return_value={'chart':{'result':[{'timestamp':[1700000000,1700000060,9999999999], 'indicators':{'quote':[{'close':[100,101,999],'volume':[1,0,10]}]}}]}}
    session=Mock();session.get.return_value=response
    session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
    with patch.object(source,'create_session',return_value=session):
        row=source.yahoo('SPY')
    assert row['price']==100
    assert row['adjustment']=='unverified'
