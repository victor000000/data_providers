import argparse
import json
import os
from dataclasses import asdict

from .client import MarketData


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shared market and trading data infrastructure")
    parser.add_argument("--home", help="Runtime storage root (defaults to configuration)")
    parser.add_argument("--private", action="store_true", help="Enable trade/account dataset access")
    parser.add_argument("--backend", choices=["auto", "parquet", "duckdb"], default="auto")
    parser.add_argument("--release", help="Pin a published Parquet release")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize schemas and seed configuration in the runtime directory")
    commands.add_parser("datasets")
    for command in ("schema", "status"):
        p = commands.add_parser(command)
        p.add_argument("dataset")
    for command in ["read", "export"]:
        p = commands.add_parser(command)
        p.add_argument("dataset")
        p.add_argument("--symbol", action="append", dest="symbols")
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--source")
        p.add_argument("--venue")
        if command == "export":
            p.add_argument("destination", help="New immutable export directory")
            p.add_argument("--format", choices=["csv", "parquet"], default="parquet")
        else:
            p.add_argument("--limit", type=int, default=10000)
    p = commands.add_parser("maintain")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--scope", choices=["all", "us", "cn", "trades", "derivatives"], default="all")
    scope.add_argument("--cn-only", dest="scope", action="store_const", const="cn")
    scope.add_argument("--us-only", dest="scope", action="store_const", const="us")
    p.add_argument("--futures-full", action="store_true", help="Backfill every discovered US futures contract")
    p.add_argument("--with-bars", action="store_true")
    p.add_argument("--with-settle", action="store_true")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--publish", action="store_true", help="Publish a verified Parquet release after successful maintenance")
    p.add_argument("--execute", action="store_true", help="Execute the plan (default: show it)")
    p = commands.add_parser("lake", help="Publish and verify immutable Parquet releases")
    p.add_argument("action", choices=["publish", "verify", "status"])
    p.add_argument("--allow-schema-change", action="store_true", help="Permit an explicit breaking schema migration")
    p = commands.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        client = MarketData(args.home, private=args.private, backend=args.backend, release=args.release)
        if args.command == "lake":
            from .lake import current, publish, verify
            if args.action == "publish":
                result = publish(client.home, private=args.private, allow_schema_change=args.allow_schema_change)
            elif args.action == "verify":
                result = verify(client.home, args.release)
            else:
                snapshot = client.release or current(client.home)
                result = snapshot.manifest if snapshot else {"status": "unpublished"}
        elif args.command == "init":
            from .initialize import initialize
            result = {"home": str(initialize(client.home)), "status": "initialized"}
        elif args.command == "datasets":
            result = client.datasets()
        elif args.command in {"schema", "status"}:
            result = getattr(client, args.command)(args.dataset)
        elif args.command in {"read", "export"}:
            filters = {key: getattr(args, key) for key in ["symbols", "start", "end", "source", "venue"]}
            if args.command == "read":
                result = client.read(args.dataset, limit=args.limit, **filters)
            else:
                result = client.export(args.dataset, args.destination, format=args.format, **filters)
        elif args.command == "maintain":
            from .maintenance import execute, plan
            if args.workers < 1:
                raise ValueError("workers must be positive")
            steps = plan(args.scope, with_bars=args.with_bars,
                         with_settle=args.with_settle, workers=args.workers, futures_full=args.futures_full)
            result = execute(steps, home=client.home) if args.execute else [asdict(step) for step in steps]
            if args.execute and args.publish and result["ok"]:
                from .maintenance import publish_result
                result = publish_result(result, client.home)
            print(json.dumps(result, indent=2, default=str))
            return 1 if args.execute and not result["ok"] else 0
        elif args.command == "serve":
            from .server import create_server
            server = create_server(client, args.host, args.port, os.environ.get("DATA_PROVIDERS_TOKEN"))
            print(f"Data API: http://{args.host}:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        parser.exit(1, f"data-providers: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
