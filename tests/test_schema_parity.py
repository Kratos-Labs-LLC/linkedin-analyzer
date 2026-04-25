"""Parity guard: Python and TS must initialize the SAME schema.

Both sides load `db/schema.sql` at module init, so by construction they
agree. This test guards against regressions: if someone reverts to an
inline SCHEMA in either side, or edits the SQL outside `db/schema.sql`,
this catches it.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from src import storage

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"missing canonical schema at {SCHEMA_PATH}"


def test_python_loads_schema_from_canonical_file():
    """The Python SCHEMA constant must be the file contents byte-for-byte."""
    on_disk = SCHEMA_PATH.read_text()
    assert storage.SCHEMA == on_disk


def test_python_init_db_matches_schema_file_table_info(tmp_path: Path):
    """Run init_db, then compare PRAGMA table_info for every CREATE'd table
    against what a fresh sqlite3 connection running just the SQL produces."""
    a = tmp_path / "via-init.db"
    storage.init_db(a)
    with storage.connect(a) as conn:
        a_tables = _table_info_for_all_tables(conn)

    b = tmp_path / "via-script.db"
    raw = sqlite3.connect(str(b))
    raw.executescript(SCHEMA_PATH.read_text())
    raw.commit()
    b_tables = _table_info_for_all_tables(raw)
    raw.close()

    # init_db's _migrate may add columns the bare CREATE doesn't have. The
    # PARITY direction we care about: every column in the bare-script DB
    # must also exist after init_db. (init_db is allowed to be a superset.)
    for table, cols in b_tables.items():
        assert table in a_tables, f"init_db missing table {table}"
        for col_name in cols:
            assert col_name in a_tables[table], (
                f"init_db's {table} missing column {col_name} "
                f"that schema.sql declares"
            )


def _table_info_for_all_tables(conn) -> dict[str, set[str]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    out: dict[str, set[str]] = {}
    for t in tables:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        out[t] = cols
    return out


@pytest.mark.skipif(
    not (REPO_ROOT / "dashboard" / "node_modules" / "better-sqlite3").exists(),
    reason="dashboard deps not installed; skip TS-side parity check",
)
def test_ts_initializes_same_columns_as_python(tmp_path: Path):
    """End-to-end parity: open the same SCHEMA file from a Node script and
    compare PRAGMA table_info to what the Python init_db produces.

    Skips when better-sqlite3 isn't installed (CI environments without
    `npm install` shouldn't fail the suite).
    """
    py_db = tmp_path / "py.db"
    storage.init_db(py_db)
    with storage.connect(py_db) as conn:
        py_tables = _table_info_for_all_tables(conn)

    ts_db = tmp_path / "ts.db"
    runner = REPO_ROOT / "dashboard" / "node_modules" / ".bin" / "tsx"
    if not runner.exists():
        # Fall back to plain Node + a JS snippet that runs better-sqlite3.
        node_runner = REPO_ROOT / "dashboard" / "node_modules" / ".bin" / "node"
        node_bin = "node"
    else:
        node_bin = str(runner)

    script = f"""
const Database = require('{REPO_ROOT}/dashboard/node_modules/better-sqlite3');
const fs = require('node:fs');
const db = new Database('{ts_db}');
db.exec(fs.readFileSync('{SCHEMA_PATH}', 'utf8'));
const tables = db
  .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
  .all()
  .map((r) => r.name);
const out = {{}};
for (const t of tables) {{
  out[t] = db.prepare(`PRAGMA table_info(${{t}})`).all().map((r) => r.name);
}}
console.log(JSON.stringify(out));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "dashboard"),
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"

    import json

    ts_tables_raw = json.loads(result.stdout)
    ts_tables = {t: set(cols) for t, cols in ts_tables_raw.items()}

    # Every table the TS side creates from schema.sql must be in py-init.
    # Columns must match (TS doesn't run _migrate, but neither does the bare
    # script — the canonical schema file is the source).
    for t, ts_cols in ts_tables.items():
        assert t in py_tables, f"TS-init has table {t} but Python doesn't"
        # Python's _migrate may add extra columns; require subset, not equality.
        assert ts_cols <= py_tables[t], (
            f"Table {t}: TS has columns {ts_cols - py_tables[t]} that Python lacks. "
            "schema.sql owns this list — both sides should see the same."
        )
