"""Keep broker combo summaries as raw receipts, separate from their economic legs."""
import dataclasses
import json

from data_providers.runtime import ROOT


def save_ibkr(fills, positions, stamp):
    root = ROOT / "var" / "raw" / "broker_receipts"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_utc": stamp,
        "executions": [{"execution": dataclasses.asdict(f.execution),
                        "contract": dataclasses.asdict(f.contract),
                        "commission_report": dataclasses.asdict(f.commissionReport)
                        if f.commissionReport is not None else None} for f in fills],
        "positions": [{"account": p.account, "contract": dataclasses.asdict(p.contract),
                       "qty": float(p.position), "avg_cost": float(p.avgCost)}
                      for p in positions],
    }
    path = root / f"ibkr_{stamp:%Y%m%dT%H%M%S%f}.json"
    path.write_text(json.dumps(payload, default=str, indent=2))
