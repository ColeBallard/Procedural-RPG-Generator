"""One-time NameLibrary seeder.

Pulls names from four upstream sources and bulk-inserts them into the
``NameLibrary`` table. Each source is independent: missing dependencies or
network failures only skip that source, never the whole run.

Usage:
    python -m scripts.seed_name_library                # all sources
    python -m scripts.seed_name_library --only fantasynames pynames
    python -m scripts.seed_name_library --per-theme 500
    python -m scripts.seed_name_library --wipe         # clear table first

Sources:
    fantasynames   pip install fantasynames
    pynames        pip install pynames
    nomina         JSON files cloned from munichjake/nomina-names into
                   ./data/nomina-names/  (any *.json under that path)
    babynames      REST API at https://babynames.netstudy.in/api
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.orm import Base, NameLibrary, engine
from scripts.name_sources import (
    iter_babynames,
    iter_fantasynames,
    iter_nomina_names,
    iter_pynames,
)

ALL_SOURCES = ("fantasynames", "pynames", "nomina", "babynames")
BATCH_SIZE = 1000


def _ensure_schema():
    """Create the table if a fresh DB hasn't been migrated yet."""
    Base.metadata.create_all(engine, tables=[NameLibrary.__table__])


def _bulk_insert(session, rows):
    if not rows:
        return 0
    session.bulk_insert_mappings(NameLibrary, rows)
    session.commit()
    return len(rows)


def _dedupe_key(row):
    return (row['source'], row['theme'], row['gender'], row['category'], row['name'])


def _seed_from(name, iterator, session, seen):
    """Drain ``iterator``, dedupe against ``seen``, batch-insert, report."""
    print(f"\n[{name}] starting...")
    started = time.time()
    buffer, total_new = [], 0
    try:
        for row in iterator:
            row.setdefault('created_at', datetime.now())
            key = _dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            buffer.append(row)
            if len(buffer) >= BATCH_SIZE:
                total_new += _bulk_insert(session, buffer)
                buffer = []
        total_new += _bulk_insert(session, buffer)
    except Exception as e:
        session.rollback()
        print(f"[{name}] aborted after {total_new} rows: {e}")
        return 0
    print(f"[{name}] inserted {total_new} new rows in "
          f"{time.time() - started:.1f}s")
    return total_new


def _load_existing_keys(session):
    """Pre-load keys already in the DB so re-runs are idempotent."""
    keys = set()
    q = session.query(NameLibrary.source, NameLibrary.theme,
                      NameLibrary.gender, NameLibrary.category, NameLibrary.name)
    for row in q.yield_per(5000):
        keys.add(tuple(row))
    return keys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs='+', choices=ALL_SOURCES, default=ALL_SOURCES,
                        help="Subset of sources to seed (default: all)")
    parser.add_argument("--per-theme", type=int, default=300,
                        help="Names to generate per (theme, gender) for "
                             "procedural sources (fantasynames, pynames). "
                             "Default: 300")
    parser.add_argument("--nomina-path", type=Path,
                        default=Path("data/nomina-names"),
                        help="Local path to munichjake/nomina-names JSON files")
    parser.add_argument("--babynames-base", type=str,
                        default="https://babynames.netstudy.in/api",
                        help="BabyNames API base URL")
    parser.add_argument("--wipe", action="store_true",
                        help="Delete every existing NameLibrary row first")
    args = parser.parse_args(argv)

    _ensure_schema()
    Session = sessionmaker(bind=engine)
    session = Session()

    if args.wipe:
        deleted = session.query(NameLibrary).delete()
        session.commit()
        print(f"Wiped {deleted} existing NameLibrary rows.")

    seen = _load_existing_keys(session)
    print(f"Loaded {len(seen)} existing keys for dedupe.")

    grand_total = 0
    if "fantasynames" in args.only:
        grand_total += _seed_from(
            "fantasynames",
            iter_fantasynames(per_theme=args.per_theme),
            session, seen)
    if "pynames" in args.only:
        grand_total += _seed_from(
            "pynames",
            iter_pynames(per_theme=args.per_theme),
            session, seen)
    if "nomina" in args.only:
        grand_total += _seed_from(
            "nomina",
            iter_nomina_names(args.nomina_path),
            session, seen)
    if "babynames" in args.only:
        grand_total += _seed_from(
            "babynames",
            iter_babynames(args.babynames_base),
            session, seen)

    print(f"\nDone. Inserted {grand_total} new rows total. "
          f"Library now has {session.query(NameLibrary).count()} rows.")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
