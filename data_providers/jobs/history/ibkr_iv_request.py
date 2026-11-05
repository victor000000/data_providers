"""Keep failed historical IV requests distinct from a vendor's empty series."""


def historical_iv_bars(ib, contract, duration):
    """The caller sets IB.RequestTimeout; disable the SDK's empty-on-timeout layer."""
    errors = []

    def on_error(req_id, code, message, event_contract=None):
        if event_contract is not None and event_contract != contract:
            return
        transient = code in (1100, 1300, 502, 504) or (
            code == 162 and any(word in message.lower() for word in
                               ('cancelled', 'canceled', 'pacing', 'timeout', 'timed out')))
        if transient:
            errors.append(f'IBKR {code}: {message}')

    ib.errorEvent += on_error
    try:
        bars = ib.reqHistoricalData(
            contract, endDateTime='', durationStr=duration, barSizeSetting='1 day',
            whatToShow='OPTION_IMPLIED_VOLATILITY', useRTH=True, formatDate=1,
            timeout=0)
        if errors:
            raise RuntimeError(errors[-1])
        return bars or []
    finally:
        ib.errorEvent -= on_error
