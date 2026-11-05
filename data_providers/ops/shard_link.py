"""shard_link.py — is the IBKR gateway reachable AT ALL? Asked of the network, not the log.

WHY THIS EXISTS. On 2026-10-11 the gateway died at 05:14 and the sweep ran until 07:53
looking healthy: two shards retried a refused socket for 2.5 hours while the controller
printed `processed=2 errors=0 ratio=0.0%`. Both numbers came from the logs, and both lied.
A shard that cannot connect still emits the occasional "no bars" — a request against a dead
session returns empty rather than raising — and `counts()` reads that as work done, so the
stall counter never advanced. Meanwhile ratio 0.0% reads as HEADROOM.

A TCP connect is the one signal a dead gateway cannot imitate: it needs no log format, no
timestamp, and no cooperation from the downloader. It is also the same door the downloaders
knock on, because the ports come from datasources/tws — a probe with its own copy of
4001/7496 would be free to drift from what they actually dial.
"""
import socket

from data_providers.providers import tws


def link_up(timeout=3.0, ports=None):
    """-> the port that accepted a connection, or None if none did.

    Cheap either way: the configured port answers at once when the gateway is up, and a
    refused connection comes back immediately when it is not. A hostname that does not
    resolve counts as down too — the container reaches the Windows host by name, and a
    resolver with no route maps every port onto nothing.
    """
    for port in (ports or (tws.PORT, 7496, 7497, 4001, 4002)):
        try:
            socket.create_connection((tws.HOST, port), timeout=timeout).close()
            return port
        except OSError:
            continue
    return None
