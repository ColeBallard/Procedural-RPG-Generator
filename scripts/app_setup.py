"""Synchronous, release-phase setup for production deploys.

Wired into the Heroku-style ``Procfile`` as::

    release: python -m scripts.app_setup

Runs once per deploy, blocks the deploy on failure, and is fully
idempotent so re-runs are safe.

Steps:
  1. ``Base.metadata.create_all`` so any newly-defined tables exist.
  2. Idempotent column adds on already-existing tables (delegated to
     ``app/startup.ensure_schema_extras``).
  3. Best-effort install of ``fantasynames`` (the post_compile hook
     already installs it in the slug; this is a fallback for ad-hoc
     environments that ran the script directly).
  4. Seed ``NameLibrary`` synchronously when the table is empty.

Environment variables (all optional):
  AUTO_MIGRATE=0           skip step 2
  AUTO_SEED_NAMES=0        skip step 4
  SEED_PER_THEME=N         names per (theme, gender) for procedural sources
  SEED_INCLUDE_BABYNAMES=1 also fetch from the BabyNames REST API
"""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("app_setup")


def main():
    # Imported lazily so import failures from app/__init__'s side effects
    # don't prevent the script from at least logging a clear error.
    from sqlalchemy.orm import sessionmaker

    from app.orm import Base, NameLibrary, engine
    from app.startup import _default_seed_argv, ensure_schema_extras

    log.info("Step 1/4: Base.metadata.create_all")
    Base.metadata.create_all(engine)

    if os.getenv("AUTO_MIGRATE", "1") != "0":
        log.info("Step 2/4: ensure_schema_extras")
        ensure_schema_extras(engine)
    else:
        log.info("Step 2/4: skipped (AUTO_MIGRATE=0)")

    log.info("Step 3/4: ensure fantasynames is importable")
    _ensure_fantasynames()

    if os.getenv("AUTO_SEED_NAMES", "1") != "0":
        log.info("Step 4/4: seed NameLibrary if empty")
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            count = session.query(NameLibrary).count()
        finally:
            session.close()

        if count > 0:
            log.info("NameLibrary already populated (%d rows); skipping seed.",
                     count)
        else:
            from scripts.seed_name_library import main as seed_main
            argv = _default_seed_argv()
            log.info("Running seed_name_library with argv=%s", argv)
            seed_main(argv)
    else:
        log.info("Step 4/4: skipped (AUTO_SEED_NAMES=0)")

    log.info("app_setup complete.")
    return 0


def _ensure_fantasynames():
    """Best-effort fantasynames install. Failure is non-fatal because the
    seed script gracefully skips missing optional sources."""
    try:
        import fantasynames  # noqa: F401
        log.info("fantasynames is importable.")
        return
    except ImportError:
        log.info("fantasynames not importable; attempting --no-deps install.")

    try:
        from scripts.install_fantasynames import install
        rc = install()
        if rc != 0:
            log.warning("fantasynames install returned rc=%s; continuing.", rc)
    except Exception as e:
        log.warning("fantasynames install raised %s; continuing.", e)


if __name__ == "__main__":
    sys.exit(main())
