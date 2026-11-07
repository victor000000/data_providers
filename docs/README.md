# Documentation index

Start here for future setup, development, consumer integration and operations.
These guides describe implemented contracts; dated requirements and runtime
receipts establish what was requested and actually delivered. The October 31 reference expansion remains historical. The November 7 documentation
audit checks the implemented development release and the separate publication
baseline; [audit coverage](DOC_REVIEW_20261107.md) records scope and limitations.
[Evidence access](EVIDENCE.md) explains what a public checkout does not contain.

## Choose a task

| Task | Read first | Then consult |
| --- | --- | --- |
| Understand or take over the project | [Handover](HANDOVER.md) | [Architecture](ARCHITECTURE.md), [AGENTS.md](../AGENTS.md) |
| Set up Linux/WSL or resolve the effective runtime | [Native environment](NATIVE.md) | [Configuration](CONFIGURATION.md) |
| Find data and understand filters | [Dataset reference](DATASETS.md) | [Data access](DATA_ACCESS.md), [Data provenance](DATA_PROVENANCE.md) |
| Use commands or HTTP endpoints | [CLI and HTTP reference](CLI_REFERENCE.md) | [Data access](DATA_ACCESS.md) |
| Maintain US/CN data or trading logs | [Maintenance](MAINTENANCE.md) | [Operations](OPERATIONS.md) before execution |
| Inspect progress or recover interrupted work | [Operations](OPERATIONS.md) | [Handover](HANDOVER.md) for evidence boundaries |
| Add a source, dataset or schema change | [Development](DEVELOPMENT.md) | [Provider reference](PROVIDERS.md), [Architecture](ARCHITECTURE.md) |
| Verify a change or delivery | [CI](CI.md) | Clean-checkout workflow, retained results and exact-SHA acceptance |
| [Validation](VALIDATION.md) | [Development](DEVELOPMENT.md), pinned release and inclusion receipts |
| Resolve a consumer requirement or formula | Requirements index (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)) | Relevant dated request and delivery evidence |

Read-only daily coverage: [Readiness assessment](READINESS.md). Next release: [v0.2 design and self-review](V02_DESIGN.md).

## Guide catalog

| Guide | Responsibility |
| --- | --- |
| [Handover](HANDOVER.md) | Read order, evidence checklist, recovery decisions and handoff template |
| [Native environment](NATIVE.md) | Host setup, private Python, broker networking and Theta operational modes |
| [Configuration](CONFIGURATION.md) | Runtime precedence, wrapper defaults, mounted inputs and shared settings |
| [CLI and HTTP reference](CLI_REFERENCE.md) | Arguments, plans, reads, exports, publication, endpoints and errors |
| [Dataset reference](DATASETS.md) | Every logical dataset, physical store/table, time/symbol filter and privacy setting |
| [Provider reference](PROVIDERS.md) | Adapter/job entrypoints, identity resolution, source limits and ingestion lifecycle |
| [Operations](OPERATIONS.md) | Worker/checkpoint locations, broker recovery and publication checks |
| [Maintenance](MAINTENANCE.md) | Supervisor plans, trading scans and source-specific ingestion |
| [Data provenance](DATA_PROVENANCE.md) | All-provider source tags, raw receipt links and separate IV methods |
| [Data access](DATA_ACCESS.md) | SDK, release pinning, exports, HTTP and interpretation |
| [Architecture](ARCHITECTURE.md) | Storage, atomic publication, locks and compatibility |
| [Development](DEVELOPMENT.md) | Source map, isolated checks, change recipes and documentation upkeep |
| [CI](CI.md) | Clean-checkout workflow, retained results and exact-SHA acceptance |
| [Validation](VALIDATION.md) | What tests/publication establish; dated validation history |
| Requirements index (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)) | Current scope and consumer requirements/evidence |
| [Historical options](HISTORICAL_OPTIONS.md) | Dated quote/metadata deliveries and access limits |
| [Daily indicators](DAILY_INDICATORS.md) | Implemented formulas, source basis and warmup |
| [BRAIN values](BRAIN_VALUES.md) | Dated metadata/value delivery and equivalence limits |
| [Migration](MIGRATION.md) | Historical extraction, aliases and runtime relocation |
| [Docker retirement](DOCKER.md) | Native replacement and historical recovery material |
| [October 21 documentation review](DOC_REVIEW_20261021.md) | Findings and checks for that earlier review |
| [October 31 documentation review](DOC_REVIEW_20261031.md) | Reference expansion, source checks and verification scope |

## Authority and freshness

1. Human instructions and [AGENTS.md](../AGENTS.md) define authorization and ownership.
2. Active market scope (private path `docs/requirements/MARKET_DATA_SCOPE_20261019.json`; [access boundaries](EVIDENCE.md#private-evidence)) limits acquisition.
3. Newer scoped requirements supersede the relevant older request, preserving evidence.
4. Live worker receipts and release manifests establish actual progress/delivery.
5. Dated reports and test counts describe their recorded run only.

US and mainland CN underlying PV and listed options are active; HK/HKG/Hong Kong
and all other listing markets are excluded. Historical global inventories do not
expand authorization. Strategies, portfolio risk and execution remain consumer-owned.

Do not copy live counters into general setup pages. Link to authoritative status
files instead. Preserve requirement/archive/raw artifact paths and bytes so hashes
and consumer handoffs remain valid. A saved status file is not proof that a process
still runs, and a published release is not proof of complete source coverage.

## Safe orientation

From `/home/vic/data_providers` on an existing native installation:

```bash
./scripts/native.sh data-providers --help
./scripts/native.sh data-providers datasets
./scripts/native.sh data-providers maintain --scope trades
```

The last command previews a plan. It does not rescan or maintain trading logs
until executed. When the user requests trading-log maintenance, use the canonical
fresh-scan procedure in [Maintenance](MAINTENANCE.md), after checking active work.
For a missing environment, follow setup in Native environment; do not create new
stores merely to make a consumer read succeed.
