"""A weekly label must not make the newest broker snapshot incomplete."""
from data_providers.jobs.trades.load_schwab_csv import load_positions


def test_six_held_legs_and_closed_weekly_are_distinguished(tmp_path):
    source = tmp_path / 'PositionStatement.csv'
    source.write_text('CRWD\n100 20 NOV 26 225 PUT,-3\n100 20 NOV 26 260 CALL,-3\nEWZ\n100 (Weeklys) 30 OCT 26 46 CALL,-18\n100 20 NOV 26 37 PUT,-50\n100 20 NOV 26 43 CALL,-50\n100 20 NOV 26 50 CALL,54\n100 (Weeklys) 23 OCT 26 45 CALL,0\n')
    rows = load_positions(source, None, True)
    assert {r[5]: r[10] for r in rows} == {
        'CRWD261120P00225000': -3, 'CRWD261120C00260000': -3,
        'EWZ261030C00046000': -18, 'EWZ261120P00037000': -50,
        'EWZ261120C00043000': -50, 'EWZ261120C00050000': 54,
    }
    assert len({r[0] for r in rows}) == 1
