"""Per-source iterators used by ``scripts/seed_name_library.py``.

Each ``iter_*`` function yields ``dict`` rows shaped like:
    {
        "source":   "fantasynames",
        "theme":    "elf",
        "gender":   "male" | "female" | "any",
        "category": "first" | "last" | "full",
        "name":     "Aerendil Starwhisper",
        "meaning":  None,        # optional
        "origin":   None,        # optional
    }

Every iterator is defensive: missing optional dependencies, missing files,
or upstream API failures cause the iterator to yield nothing rather than
crashing the whole seed run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------------- #
# fantasynames                                                           #
# --------------------------------------------------------------------- #
_FANTASYNAMES_RACES = ("elf", "dwarf", "human", "hobbit", "french", "anglo")


def iter_fantasynames(per_theme: int = 300) -> Iterable[dict]:
    try:
        import fantasynames as fn
    except ImportError:
        print("[fantasynames] package not installed; skipping.")
        return

    for race in _FANTASYNAMES_RACES:
        gen = getattr(fn, race, None)
        if gen is None:
            continue
        for gender in ("male", "female"):
            seen = set()
            attempts = 0
            while len(seen) < per_theme and attempts < per_theme * 3:
                attempts += 1
                try:
                    name = gen(gender)
                except Exception:
                    break
                if name and name not in seen:
                    seen.add(name)
                    yield {
                        "source": "fantasynames",
                        "theme": race,
                        "gender": gender,
                        "category": "full",
                        "name": name,
                    }


# --------------------------------------------------------------------- #
# pynames                                                                #
# --------------------------------------------------------------------- #
# (theme_name, "module.path", "ClassName") — kept in code rather than
# discovered dynamically so renames upstream don't silently change themes.
_PYNAMES_GENERATORS = (
    ("scandinavian", "pynames.generators.scandinavian", "ScandinavianNamesGenerator"),
    ("korean",       "pynames.generators.korean",       "KoreanNamesGenerator"),
    ("mongolian",    "pynames.generators.mongolian",    "MongolianNamesGenerator"),
    ("elven_dnd",    "pynames.generators.elven",        "DnDNamesGenerator"),
    ("elven_warhammer", "pynames.generators.elven",     "WarhammerNamesGenerator"),
    ("goblin",       "pynames.generators.goblin",       "GoblinGenerator"),
    ("orc",          "pynames.generators.orc",          "OrcNamesGenerator"),
)


def iter_pynames(per_theme: int = 300) -> Iterable[dict]:
    try:
        import importlib
        from pynames import GENDER
    except ImportError:
        print("[pynames] package not installed; skipping.")
        return

    gender_map = {"male": GENDER.MALE, "female": GENDER.FEMALE}

    for theme, mod_path, cls_name in _PYNAMES_GENERATORS:
        try:
            cls = getattr(importlib.import_module(mod_path), cls_name)
            gen = cls()
        except Exception as e:
            print(f"[pynames] {theme} generator unavailable: {e}")
            continue

        for gender_str, gender_const in gender_map.items():
            seen = set()
            attempts = 0
            while len(seen) < per_theme and attempts < per_theme * 3:
                attempts += 1
                try:
                    name = gen.get_name_simple(gender_const)
                except Exception:
                    break
                if name and name not in seen:
                    seen.add(name)
                    yield {
                        "source": "pynames",
                        "theme": theme,
                        "gender": gender_str,
                        "category": "first",
                        "name": name,
                    }


# --------------------------------------------------------------------- #
# nomina-names (JSON files cloned from munichjake/nomina-names)          #
# --------------------------------------------------------------------- #
def iter_nomina_names(root: Path) -> Iterable[dict]:
    if not root.exists():
        print(f"[nomina] no data at {root}; clone "
              "https://github.com/munichjake/nomina-names there to enable.")
        return

    for json_file in sorted(root.rglob("*.json")):
        theme = json_file.stem.lower()
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[nomina] could not parse {json_file}: {e}")
            continue
        yield from _walk_nomina(data, theme)


def _walk_nomina(node, theme, gender="any", category="first"):
    """Walk arbitrary nested JSON, emitting any string we find as a name.

    Detects ``male`` / ``female`` / ``surname`` / ``first`` keys to refine
    gender/category metadata as it descends.
    """
    if isinstance(node, str):
        yield {"source": "nomina", "theme": theme, "gender": gender,
               "category": category, "name": node}
    elif isinstance(node, list):
        for item in node:
            yield from _walk_nomina(item, theme, gender, category)
    elif isinstance(node, dict):
        # If this dict looks like a single record with "name", emit it.
        if "name" in node and isinstance(node["name"], str):
            yield {
                "source": "nomina", "theme": theme,
                "gender": str(node.get("gender", gender)).lower() or gender,
                "category": str(node.get("category", category)).lower() or category,
                "name": node["name"],
                "meaning": node.get("meaning"),
                "origin": node.get("origin"),
            }
            return
        for key, value in node.items():
            klow = str(key).lower()
            sub_gender = gender
            sub_category = category
            if klow in ("male", "female"):
                sub_gender = klow
            elif klow in ("surname", "surnames", "last", "family"):
                sub_category = "last"
            elif klow in ("first", "firstname", "given"):
                sub_category = "first"
            yield from _walk_nomina(value, theme, sub_gender, sub_category)


# --------------------------------------------------------------------- #
# BabyNames API                                                          #
# --------------------------------------------------------------------- #
_BABYNAMES_ORIGINS = (
    "arabic", "celtic", "norse", "greek", "latin", "hebrew",
    "japanese", "chinese", "korean", "persian", "indian", "african",
    "german", "french", "italian", "spanish", "english", "russian",
)


def iter_babynames(base_url: str) -> Iterable[dict]:
    try:
        import requests
    except ImportError:
        print("[babynames] requests not installed; skipping.")
        return

    for origin in _BABYNAMES_ORIGINS:
        try:
            resp = requests.get(
                f"{base_url.rstrip('/')}/origin/{origin}", timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[babynames] origin={origin} fetch failed: {e}")
            continue

        records = data if isinstance(data, list) else data.get("data", [])
        for record in records or []:
            if not isinstance(record, dict):
                continue
            name = record.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            gender = (record.get("gender") or "any").lower()
            if gender in ("m", "boy"):
                gender = "male"
            elif gender in ("f", "girl"):
                gender = "female"
            elif gender not in ("male", "female"):
                gender = "any"
            yield {
                "source": "babynames",
                "theme": origin,
                "gender": gender,
                "category": "first",
                "name": name.strip(),
                "meaning": record.get("meaning"),
                "origin": origin,
            }
