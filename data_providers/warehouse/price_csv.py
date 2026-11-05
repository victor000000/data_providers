"""Prepare and atomically publish a validated daily-price CSV replacement."""
import math
import os
import tempfile
import pandas as pd


def rewrite_validated(path, df, factor, min_price):
    """Validate a complete replacement before atomically publishing it."""
    scalar = not hasattr(factor, "get")
    factors = [float(factor) if scalar else float(factor.get(idx,1.0)) for idx in df.index]
    if not all(math.isfinite(k) and k>0 for k in factors):
        raise ValueError("Incomplete adjusted history; existing CSV retained")
    lines = []
    for (idx, r), k in zip(df.iterrows(), factors):
        o, h, l, c = r["Open"], r["High"], r["Low"], r["Close"]
        if not all(math.isfinite(v) for v in (o,h,l,c)):
            continue
        v = 0 if pd.isna(r["Volume"]) else int(r["Volume"])
        if min(o*k, h*k, l*k, c*k) < min_price:
            continue
        lines.append(f"{idx.strftime('%d%m%Y')},{o*k:.4f},{h*k:.4f},"
                     f"{l*k:.4f},{c*k:.4f},{v}\n")
    if not lines:
        raise ValueError("No usable replacement bars; existing CSV retained")
    if os.path.exists(path):
        with open(path, encoding="ascii") as saved:
            old_dates = {line.split(",")[0] for line in saved
                         if len(line.split(",")[0])==8 and line.split(",")[0].isdigit()}
        if old_dates - {line.split(",")[0] for line in lines}:
            raise ValueError("Replacement loses stored dates; existing CSV retained")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".refresh-")
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as fh:
            os.fchmod(fh.fileno(), os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644)
            fh.writelines(lines)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

