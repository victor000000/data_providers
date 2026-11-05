"""cn_iv — the China option-board pipeline: IV series in, IVR board out.

Split into small modules on 2026-10-07 because this repo enforces a 100-line limit per file
with a ratchet (tests/test_line_limit.py): the venue builder and the solver were 269 and 505
lines as single scripts, and the ratchet fails a NEW file over the limit.

  frames.py    one exchange day-frame -> one ATM IV (SHFE series-level, GFEX contract-level)
  venue.py     the day-walk over the venues that publish their own IV, with the give-up rule
  calendar.py  CN trading days, delivery-month codes, and the expiry T is measured to
  chains.py    Sina chain tables and per-contract histories (DCE, CFFEX)
  solve.py     strike selection and the ATM IV solve
  product.py   one product's months solved and stitched into a daily series
  dump.py      fetch-to-JSON and import-from-JSON, so a fetch can run beside another writer
  board.py     rank every product in cn_product_map by IVR and render the table
  windows.py   the same latest IV ranked inside its own 1Y/2Y/5Y/10Y trailing window
  render.py    the report text, split out of board.py when the horizon columns landed
  fetch.py     request timeouts, and the unlisted-vs-unanswered split
"""
from data_providers.jobs.history.cn_iv import fetch

# akshare passes no timeout, so one dropped SYN parks a worker on the kernel's connect
# timeout -- and Sina rate-limits by dropping SYNs. Bound it for the whole package.
fetch.bounded()
