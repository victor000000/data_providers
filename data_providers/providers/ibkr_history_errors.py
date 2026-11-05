"""Classify HMDS messages by meaning; code 162 is an envelope, not a denial."""

import re


def historical_farm_states(events):
    """Latest state per native farm, including colon and period message suffixes."""
    states = {}
    for event in events:
        code = event.get('code')
        if code not in {2105, 2106, 2107}:
            continue
        match = re.search(r'([a-z0-9_-]*hmds[a-z0-9_-]*)\s*$',
                          event.get('message', ''), re.IGNORECASE)
        if match:
            states[match.group(1).lower()] = code
    return states


def historical_farm_available(events):
    """Admit real work on connected/on-demand HMDS, not an entitlement proof.

    Native farm names describe broker routing, not listing-market permission.
    Unknown status or any still-disconnected farm fails closed.
    """
    states = historical_farm_states(events)
    return bool(states) and 2105 not in states.values() and any(
        code in {2106, 2107} for code in states.values())

def classify_history_error(error):
    code = error.get('code')
    message = error.get('message', '').lower()
    if any(term in message for term in (
        'no market data permission', 'not subscribed', 'additional subscription',
        'requires subscription', 'permission denied', 'not allowed',
    )) or code in {354, 10167, 10168, 10186}:
        return 'permission_denied'
    if code in {200, 321}:
        return 'contract_or_request_invalid'
    if 'cancel' in message:
        return 'cancelled'
    if 'timed out' in message or 'timeout' in message:
        return 'service_timeout'
    if 'no data' in message:
        return 'no_data'
    if 'query failed' in message:
        return 'service_query_failed'
    if code in {2105, 2106, 2107}:
        return 'farm_status'
    if code in {162, 165}:
        return 'unclassified_hmds_error'
    return 'other'


def is_history_denial(error):
    return classify_history_error(error) in {
        'permission_denied', 'contract_or_request_invalid',
    }
