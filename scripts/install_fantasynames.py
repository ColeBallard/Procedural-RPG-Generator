"""Install fantasynames into the active Python environment with --no-deps.

The fantasynames PyPI package incorrectly lists dev tooling (mypy<0.813,
black<21.0, flake8<4.0.0, pytest<7.0.0) under install_requires. mypy<0.813
in particular pulls typed-ast, whose C extension fails to build on
Python 3.10+. Using --no-deps gives us the runtime code without the
broken transitive chain.

This helper is invoked by:
  * bin/post_compile         (Heroku buildpack hook)
  * scripts/app_setup.py     (manual / release-phase setup)
  * docs / README             (suggested for local dev)
"""
from __future__ import annotations

import subprocess
import sys

VERSION = "0.1.2"


def install():
    try:
        import fantasynames  # noqa: F401
        print(f"fantasynames already installed.")
        return 0
    except ImportError:
        pass

    print(f"Installing fantasynames=={VERSION} (--no-deps)...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps",
         f"fantasynames=={VERSION}"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(install())
