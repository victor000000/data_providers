from types import SimpleNamespace as NS
from data_providers.jobs.trades.fetch_tws_executions import execution_fee


def fill(exec_id='', currency='', commission=0):
    return NS(execution=NS(execId='execution-1'), contract=NS(currency='USD'),
              commissionReport=NS(execId=exec_id,currency=currency,commission=commission))


def test_placeholder_commission_is_unknown():
    assert execution_fee(fill()) is None
    assert execution_fee(fill('other','USD',1)) is None
    assert execution_fee(fill('execution-1','EUR',1)) is None


def test_matching_zero_and_actual_fee_are_distinct_from_unknown():
    assert execution_fee(fill('execution-1','USD',0)) == 0
    assert execution_fee(fill('execution-1','USD',1.25)) == 1.25
    for value in [None,float('nan'),float('inf'),1.7976931348623157e308]:
        assert execution_fee(fill('execution-1','USD',value)) is None
