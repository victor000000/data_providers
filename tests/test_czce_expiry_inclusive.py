from datetime import date, timedelta

from data_providers.jobs.data_ops.cn_expiry import expiry_for


def test_czce_fifteenth_counts_when_it_is_a_trading_day():
    # CZCE MA/PTA specifications: 15th included, third trading day backwards.
    # December 15 (Tue), 14 (Mon), 11 (Fri), rather than December 10.
    calendar = [date(2026, 12, 1) + timedelta(days=i) for i in range(31)
                if (date(2026, 12, 1) + timedelta(days=i)).weekday() < 5]
    assert expiry_for('CZCE', 2027, 1, calendar) == date(2026, 12, 11)
    assert expiry_for('CZCE', 2027, 1) == date(2026, 12, 11)
    calendar.remove(date(2026, 12, 15))
    assert expiry_for('CZCE', 2027, 1, calendar) == date(2026, 12, 10)
