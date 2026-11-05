"""The fake journal_entries and row builders shared by the two snapshot tests."""
OLD = "2026.xlsx"
NEW = "2026_tradelogs.xlsx"


class FakeCon:
    """An in-memory journal_entries, enough for the subset test."""

    def __init__(self, rows):
        self.rows = list(rows)          # (source_file, row_num, raw_json)
        self.deleted = []

    def execute(self, sql, params=None):
        if sql.strip().startswith("SELECT source_file, count(*)"):
            counts = {}
            for f, _n, _j in self.rows:
                counts[f] = counts.get(f, 0) + 1
            return _Rows(list(counts.items()))
        if "SELECT count(*) FROM journal_entries s" in sql:
            src = params[0]
            dup = sum(1 for f, n, j in self.rows
                      if f == src and any(g != src and m == n and k == j
                                          for g, m, k in self.rows))
            return _Rows([(dup,)])
        if sql.startswith("DELETE"):
            src = params[0]
            self.deleted.append(src)
            self.rows = [r for r in self.rows if r[0] != src]
            return _Rows([])
        raise AssertionError(sql)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


def rows_of(src, n):
    return [(src, i, f"row{i}") for i in range(2, 2 + n)]
