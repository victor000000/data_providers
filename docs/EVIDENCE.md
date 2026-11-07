# Evidence access and software boundaries

## Private evidence

The live installation retains docs/requirements/, runtime/ and .local/ source
receipts, acquisition requirements, frozen queues and operator handoffs. A public
sanitized checkout deliberately omits these private artifacts. Plain paths or
references routed here identify retained local evidence, not files available in
this public repository. Missing private evidence is not permission to regenerate
it, replay a request, acquire an excluded market or invent coverage.

Read [AGENTS.md](../AGENTS.md) for binding market scope: US and mainland CN PV and
listed options, actual futures, supporting calendars/actions/specifications.
Independent broker journals are separate. Consumer strategy/risk/execution remains
outside this project's ownership. Private evidence must be accessed through its
authorized local owner; do not paste account data or secrets into public issues.

## Three independent versions

- Software version identifies code and API contracts (currently 0.2.0.dev0).
- Data-lake release ID pins immutable manifests and files, not a software build.
- A dated consumer requirement defines universe, dates, methods and acceptance.

A source checkout and passing CI do not ship a market warehouse. A published data
release does not imply complete upstream coverage, freshness or consumer acceptance.
Readiness v1 checks daily presence only; see [Readiness](READINESS.md).

## Historical evidence and live state

Dated requirement/report files retain their original dates and bytes. Later scoped
instructions supersede relevant earlier work; old process IDs, status=running,
source-health observations and queued jobs are not current authorization or proof.
Follow [Operations](OPERATIONS.md) before maintenance. No live worker or source
health was tested by the November 7 documentation audit.

The release design distinguishes implemented code from proposed future stages.
Local test/build evidence and hosted exact-SHA CI are separate. The publication
coordinator owns serialized publication; documentation edits do not authorize a
second browser publisher or concurrent staging session.
