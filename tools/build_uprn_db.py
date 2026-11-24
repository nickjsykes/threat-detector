"""Build a UPRN lookup from the OS Open UPRN CSV zip (free, Open Government Licence).

    python tools/build_uprn_db.py osopenuprn_YYYYMM_csv.zip [out.sqlite]

Download: https://osdatahub.os.uk/downloads/open/OpenUPRN
All of Great Britain is about 41 million rows and 1.5 GB. Refresh a couple of
times a year; only new addresses change.
"""
import csv
import io
import sqlite3
import sys
import zipfile
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / "open_uprn.sqlite"
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
                     "CREATE TABLE uprn (uprn INTEGER PRIMARY KEY, x REAL, y REAL) WITHOUT ROWID;")
    n = 0
    with zipfile.ZipFile(src) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            batch = []
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                batch.append((int(row["UPRN"]), float(row["X_COORDINATE"]), float(row["Y_COORDINATE"])))
                if len(batch) == 200_000:
                    db.executemany("INSERT OR REPLACE INTO uprn VALUES (?,?,?)", batch)
                    n += len(batch)
                    batch.clear()
                    print(f"\r{n:,} rows", end="", flush=True)
            db.executemany("INSERT OR REPLACE INTO uprn VALUES (?,?,?)", batch)
            n += len(batch)
    db.commit()
    db.close()
    tmp.replace(out)
    print(f"\n{n:,} UPRNs -> {out}")


if __name__ == "__main__":
    main()
