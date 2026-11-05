"""Small bounded HTTP interface for consumers outside Python; no SQL endpoint."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def create_server(client, host="127.0.0.1", port=8765, token=None):
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise ValueError("A bearer token is required for a non-loopback listener")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if token and not hmac.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {token}"
            ):
                self.reply(401, {"error": "Unauthorized"})
                return
            url = urlparse(self.path)
            try:
                reader = client.refresh()
                release = reader.release.id if reader.release else None
                if url.path == "/v1/datasets":
                    self.reply(200, {"contract_version": 1, "datasets": reader.datasets(), "release": release})
                    return
                if url.path == "/v1/status":
                    args = parse_qs(url.query, keep_blank_values=True)
                    if set(args) != {"dataset"} or len(args["dataset"]) != 1:
                        raise ValueError("Provide one dataset parameter")
                    self.reply(200, reader.status(args["dataset"][0]))
                    return
                if url.path != "/v1/data":
                    self.reply(404, {"error": "Unknown endpoint"})
                    return
                args = parse_qs(url.query, keep_blank_values=True)
                if set(args) - {"dataset", "symbol", "start", "end", "source", "venue", "limit", "contract_id"}:
                    raise ValueError("Unknown query parameter")
                dataset = args.pop("dataset")[0]
                symbols = args.pop("symbol", None)
                contract_ids = args.pop("contract_id", None)
                limit = int(args.pop("limit", ["10000"])[0])
                rows = reader.read(dataset, symbols=symbols, contract_ids=contract_ids, limit=limit,
                                   **{key: values[0] for key, values in args.items()})
                self.reply(200, {"contract_version": 1, "dataset": dataset, "rows": rows, "release": release,
                                 "limit": limit, "possibly_truncated": len(rows) == limit})
            except PermissionError:
                self.reply(403, {"error": "Private dataset access disabled"})
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})
            except FileNotFoundError:
                self.reply(503, {"error": "Dataset not initialized"})
            except Exception:
                self.reply(503, {"error": "Dataset unavailable; retry after maintenance"})

        def reply(self, status, value):
            body = json.dumps(value, default=str, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass  # URLs can contain private instrument selections.

    return ThreadingHTTPServer((host, port), Handler)
