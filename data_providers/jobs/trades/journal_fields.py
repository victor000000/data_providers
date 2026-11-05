"""journal_fields.py — which spreadsheet column is which schema column.

BY HEADER NAME, NEVER BY POSITION. `journal_entries` had no `major_plan` column, and the
loader mapped the first eight cells positionally onto the eight narrative columns -- so
everything from the sheet's fifth column onward was shifted by one, on every row ever
loaded. `alt_assumptions` held MAJOR PLANS; `confidence` held the alternative
assumptions; `plan_fixed_time` held the confidence; `order_thinking` held the plan fixed
time. Nothing failed and no column was empty: each was full of the wrong text, which is
why it survived. The user writes the plan in `major plan` -- it is the most important
field in the journal and it had nowhere to go but raw_json.

Headers are matched by PREFIX because the real ones are long phrases: the sheet's sixth
header is literally "alternative assumptions（2-3)" and its seventh is "degree of
confidence of major assumption".

A schema column whose header the sheet no longer has resolves to None and is written
NULL -- it must not silently inherit its neighbour, which is the whole failure mode here.
"""

#: (schema column, prefix of the header it comes from), in the order the table wants.
FIELDS = (
    ("ts", "date"),
    ("broker", "broker"),
    ("plan_time", "plan time"),
    ("major_assumption", "major assumption"),
    ("major_plan", "major plan"),
    ("alt_assumptions", "alternative assumption"),
    ("confidence", "degree of confidence"),
    ("plan_fixed_time", "plan fixed time"),
    ("order_thinking", "order thinking"),
)

COLUMNS = ["row_num"] + [c for c, _ in FIELDS] + ["raw_json", "source_file"]


def index_of(header):
    """{schema column: header position}, with None where the sheet has no such header.

    "major assumption" and "major plan" share a prefix, so a first-wins scan could pair
    either with the wrong one. The match is on the WHOLE prefix and the headers are
    distinct phrases, so startswith is exact here -- but it is worth knowing that this is
    why the prefixes are spelled out in full rather than abbreviated to "major".
    """
    return {col: next((j for j, h in enumerate(header) if h.startswith(pre)), None)
            for col, pre in FIELDS}


def values(header, row_num, row, cells, source_file):
    """One `journal_entries` row, in COLUMNS order."""
    idx = index_of(header)

    def cell(j):
        if j is None or j >= len(row) or row[j] is None:
            return None
        return str(row[j]).strip()

    import json
    return ([row_num] + [cell(idx[c]) for c, _ in FIELDS]
            + [json.dumps(cells, ensure_ascii=False), source_file])
