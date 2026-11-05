# Docker retirement

This project runs directly on Linux/WSL. Docker Desktop, a Docker daemon and
container images are not required for ingestion, maintenance, publication, tests
or package builds. Use [Native environment](NATIVE.md) for setup and commands.

```bash
make setup
make test
make build
./scripts/native.sh data-providers datasets
```

The former Docker configuration archive was deleted at the user's request on
2026-10-23. Use the native setup above.

The old `data_providers` container used bind mounts for this checkout and Windows
trading exports. The live data remains in `/home/vic/data_providers/runtime`;
removing the stopped container and its `data-providers:local` image does not remove
those host files. Preserve native environments, private Python, raw receipts,
staging, frozen queues and checkpoints. Historical `/root/data_providers` paths
in receipts remain unchanged.

Cleanup must inspect container writable changes and preserve any needed logs
before removal. Do not delete volumes or other projects' resources as part of
this project's cleanup. Docker Desktop is shared software; removing it requires
checking the needs and recovery material of those projects too. See
[Operations](OPERATIONS.md) before interrupting any worker.

## Cleanup verification — 2026-10-23

Native `make setup`, `make test` (490 passed, 28 subtests) and `make build`
completed. CI now uses the same targets. Native watcher/download processes and
their locks remained active during retirement. No maintenance/download cycle
was launched for validation.

The user authorized removing all Docker resources and uninstalling Docker Desktop.
The screener and BRAIN containers/images were removed concurrently by other work
before this invocation could archive them. Recovery copies of the remaining data
container filesystem, metadata and all three `multi-stack` volumes were saved in
`/home/vic/docker-retirement-20261023` with SHA-256 checksums and owner-only access.
Container exports exclude bind-mounted host data, which remains in its original
location. These archives were subsequently deleted at the user's request, together with
`/home/vic/qc_work/.local/docker-cleanup-20261023`, the obsolete `.venv-container`
and archived Docker configuration. No Docker rollback backups are retained.
Native environments and live data remain in place.

Docker Desktop's installer and Windows uninstall entry were absent at the final
inspection, and its engine was unavailable. Remaining Desktop install and Local/Roaming application/cache directories were
removed; each reported absent afterward. No Docker process or service was found.

The distro-installed `/usr/bin/docker` (`docker.io`) and root-owned Desktop CLI
symlinks remain on Linux. Removing these requires administrator authentication;
`sudo -n` reported that interactive authentication is required. They do not
provide a running Desktop engine or affect native project commands.
