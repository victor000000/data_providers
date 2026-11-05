from types import SimpleNamespace as NS
import pytest
from data_providers.jobs.trades.fetch_tws_ibkr import ACCOUNT, fetch_account_data


def test_paper_connection_rejected_before_execution_or_position_requests():
    ib = NS(managedAccounts=lambda: ['DUR300593'])
    with pytest.raises(RuntimeError, match='live IBKR account is unavailable'):
        fetch_account_data(ib)


def test_shared_connection_only_returns_configured_account():
    live_fill = NS(execution=NS(acctNumber=ACCOUNT))
    paper_fill = NS(execution=NS(acctNumber='DUR300593'))
    live_position = NS(account=ACCOUNT)
    paper_position = NS(account='DUR300593')
    def executions(query):
        assert query.acctCode == ACCOUNT
        return [live_fill, paper_fill]
    ib = NS(managedAccounts=lambda: [ACCOUNT, 'DUR300593'], reqExecutions=executions,
            reqPositions=lambda: [live_position, paper_position])
    assert fetch_account_data(ib) == ([live_fill], [live_position])
