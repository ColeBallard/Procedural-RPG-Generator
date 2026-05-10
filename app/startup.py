"""Startup hooks invoked once per process from ``createApp()``.

Two responsibilities:

  1. ``ensure_schema_extras`` — idempotently apply schema changes that
     ``Base.metadata.create_all`` cannot make on its own (column adds on
     already-existing tables). Safe on every restart.

  2. ``maybe_seed_name_library_async`` — if the ``NameLibrary`` table is
     empty, kick off a background thread that populates it from the
     procedural sources. Runs at most once per fresh deploy; never blocks
     app startup; failures are logged and otherwise ignored.

Both can be disabled via environment variable for ops who want to manage
schema and data manually:

  AUTO_MIGRATE=0     skip ensure_schema_extras
  AUTO_SEED_NAMES=0  skip background seeding
"""
from __future__ import annotations

import logging
import os
import threading

from sqlalchemy import inspect, text

from app.orm import NameLibrary, Seed

log = logging.getLogger(__name__)


def run_startup_tasks(engine, session_factory):
    """Convenience wrapper called from ``createApp``."""
    if os.getenv("AUTO_MIGRATE", "1") != "0":
        ensure_schema_extras(engine)
        rename_columns(engine)
        drop_retired_columns(engine)
    if os.getenv("AUTO_SEED_NAMES", "1") != "0":
        maybe_seed_name_library_async(session_factory)


# --------------------------------------------------------------------- #
# Schema migrations                                                      #
# --------------------------------------------------------------------- #
_EXPECTED_SEED_COLUMNS = {
    "naming_themes": "ALTER TABLE Seeds ADD COLUMN naming_themes TEXT",
}

# Columns that have been removed from the ORM and should be dropped from
# pre-existing databases. Keyed by table; each entry maps the dropped
# column name to the DDL used to remove it. SQLite >= 3.35 and MySQL both
# support ``ALTER TABLE ... DROP COLUMN``.
_RETIRED_COLUMNS = {
    "Settings": {
        "classes": "ALTER TABLE Settings DROP COLUMN classes",
    },
}

# Columns that have been renamed in the ORM. Keyed by table; each entry
# maps old_name -> (new_name, ddl). Both MySQL >= 8.0 and SQLite >= 3.25
# support ``ALTER TABLE ... RENAME COLUMN ... TO ...``.
_RENAMED_COLUMNS = {
    "CharacterRelationships": {
        "relationship": (
            "relationship_type",
            "ALTER TABLE CharacterRelationships "
            "RENAME COLUMN relationship TO relationship_type",
        ),
    },
}


def ensure_schema_extras(engine):
    """Apply column adds that ``create_all`` skips on existing tables."""
    try:
        inspector = inspect(engine)
        existing = {c["name"] for c in inspector.get_columns("Seeds")}
    except Exception as e:
        log.warning("ensure_schema_extras: could not inspect Seeds: %s", e)
        return

    with engine.begin() as conn:
        for column, ddl in _EXPECTED_SEED_COLUMNS.items():
            if column in existing:
                continue
            try:
                conn.execute(text(ddl))
                log.info("ensure_schema_extras: added Seeds.%s", column)
            except Exception as e:
                # Most often a race with a parallel migration or a dialect
                # complaint. Re-inspect and only error if the column is
                # actually missing.
                refreshed = {c["name"] for c in inspect(engine).get_columns("Seeds")}
                if column not in refreshed:
                    log.error("ensure_schema_extras: failed to add Seeds.%s: %s",
                              column, e)


def rename_columns(engine):
    """Rename columns whose ORM name has changed, where the old name is
    still present and the new name is not. Idempotent; safe on every
    restart. Must run before ``drop_retired_columns`` so the rename does
    not race a drop on the same column."""
    for table, columns in _RENAMED_COLUMNS.items():
        try:
            inspector = inspect(engine)
            existing = {c["name"] for c in inspector.get_columns(table)}
        except Exception as e:
            log.warning("rename_columns: could not inspect %s: %s", table, e)
            continue

        with engine.begin() as conn:
            for old_name, (new_name, ddl) in columns.items():
                if old_name not in existing or new_name in existing:
                    continue
                try:
                    conn.execute(text(ddl))
                    log.info("rename_columns: renamed %s.%s -> %s",
                             table, old_name, new_name)
                except Exception as e:
                    refreshed = {c["name"] for c in inspect(engine).get_columns(table)}
                    if new_name not in refreshed:
                        log.error("rename_columns: failed to rename %s.%s -> %s: %s",
                                  table, old_name, new_name, e)


def drop_retired_columns(engine):
    """Drop columns that have been removed from the ORM, where present."""
    for table, columns in _RETIRED_COLUMNS.items():
        try:
            inspector = inspect(engine)
            existing = {c["name"] for c in inspector.get_columns(table)}
        except Exception as e:
            log.warning("drop_retired_columns: could not inspect %s: %s", table, e)
            continue

        with engine.begin() as conn:
            for column, ddl in columns.items():
                if column not in existing:
                    continue
                try:
                    conn.execute(text(ddl))
                    log.info("drop_retired_columns: dropped %s.%s", table, column)
                except Exception as e:
                    refreshed = {c["name"] for c in inspect(engine).get_columns(table)}
                    if column in refreshed:
                        log.error("drop_retired_columns: failed to drop %s.%s: %s",
                                  table, column, e)


# --------------------------------------------------------------------- #
# Background NameLibrary seed                                            #
# --------------------------------------------------------------------- #
def maybe_seed_name_library_async(session_factory):
    """Spawn a daemon thread that seeds NameLibrary if it's empty."""
    def _worker():
        try:
            session = session_factory()
            try:
                count = session.query(NameLibrary).count()
            finally:
                session.close()
            if count > 0:
                log.info("NameLibrary already populated (%d rows); skipping seed.",
                         count)
                return

            log.info("NameLibrary is empty; running background seed...")
            # Lazy import to avoid pulling in optional packages at app boot.
            from scripts.seed_name_library import main as seed_main
            argv = _default_seed_argv()
            seed_main(argv)
            log.info("Background NameLibrary seed complete.")
        except Exception as e:
            log.exception("Background NameLibrary seed failed: %s", e)

    t = threading.Thread(target=_worker, name="namelib-seed", daemon=True)
    t.start()


def _default_seed_argv():
    """Conservative defaults for an in-process startup seed.

    Network-dependent sources (babynames) are excluded by default to
    avoid hangs during boot; ops can enable them by setting
    ``SEED_INCLUDE_BABYNAMES=1`` or running scripts/seed_name_library
    manually.
    """
    per_theme = os.getenv("SEED_PER_THEME", "200")
    sources = ["fantasynames", "pynames", "nomina"]
    if os.getenv("SEED_INCLUDE_BABYNAMES", "0") == "1":
        sources.append("babynames")
    return ["--only", *sources, "--per-theme", per_theme]
