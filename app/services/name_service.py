# name_service.py
"""Theme-aware name selection backed by the ``NameLibrary`` table.

The service is responsible for three things:

  1. Discovering which (source, theme) pairs are actually available in the DB.
  2. Asking the LLM, once per seed, to pick the subset of those pairs that
     best fits the world. The decision is persisted on ``Seed.naming_themes``
     and used for every subsequent character drawn during this seed's lifetime.
  3. Pulling a random name from the chosen subset, optionally filtered by
     gender and category (``first`` / ``last`` / ``full``).

If the table is empty, or the LLM returns no usable themes, every lookup
returns ``None`` and callers fall back to whatever name the LLM produced.
"""
from __future__ import annotations

import json
import random
from typing import List, Optional

from sqlalchemy import func

from app.orm import NameLibrary, Seed
from app.prompt_templates import WORLD_BUILDING
from app.world_building.schemas import NamingThemeSelectionOut


class NameService:
    # Kept small so the prompt stays readable; each entry is one line.
    _MAX_THEMES_IN_PROMPT = 200

    def __init__(self, session, gpt_service=None):
        self.session = session
        self.gpt_service = gpt_service

    # ------------------------------------------------------------------ #
    # Theme discovery + selection                                         #
    # ------------------------------------------------------------------ #
    def list_available_themes(self) -> List[dict]:
        """Return every (source, theme) pair present in NameLibrary."""
        rows = (
            self.session.query(NameLibrary.source, NameLibrary.theme)
            .distinct()
            .order_by(NameLibrary.source, NameLibrary.theme)
            .all()
        )
        return [{"source": s, "theme": t} for s, t in rows]

    def select_themes_for_seed(self, seed_data) -> List[dict]:
        """Ask the LLM to pick 1-3 themes that fit ``seed_data``.

        Returns the chosen list (possibly empty if no themes are available
        or the LLM call fails).
        """
        available = self.list_available_themes()
        if not available:
            return []
        if self.gpt_service is None:
            return []

        catalog_lines = [f"- {p['source']}/{p['theme']}"
                         for p in available[: self._MAX_THEMES_IN_PROMPT]]
        catalog = "\n".join(catalog_lines)

        payload = self.gpt_service.get_structured(
            WORLD_BUILDING['NAMING_THEME_SELECTION'].format(seed_data, catalog),
            NamingThemeSelectionOut,
            max_attempts=2,
            temperature=0.4,
        )
        if payload is None or not payload.themes:
            return []

        # Drop any (source, theme) the LLM hallucinated that isn't in the DB.
        available_set = {(p['source'], p['theme']) for p in available}
        chosen = [
            {"source": c.source, "theme": c.theme}
            for c in payload.themes
            if (c.source, c.theme) in available_set
        ]
        return chosen[:3]

    # ------------------------------------------------------------------ #
    # Persistence on the Seed row                                         #
    # ------------------------------------------------------------------ #
    def assign_themes_to_seed(self, seed_id, themes: List[dict]) -> None:
        seed = self.session.query(Seed).filter(Seed.id == seed_id).one()
        seed.naming_themes = json.dumps(themes) if themes else None
        self.session.commit()

    def get_themes_for_seed(self, seed_id) -> List[dict]:
        seed = self.session.query(Seed).filter(Seed.id == seed_id).first()
        if seed is None or not seed.naming_themes:
            return []
        try:
            data = json.loads(seed.naming_themes)
        except (TypeError, ValueError):
            return []
        return [t for t in data if isinstance(t, dict)
                and 'source' in t and 'theme' in t]

    # ------------------------------------------------------------------ #
    # Lookup                                                              #
    # ------------------------------------------------------------------ #
    def random_name(
        self,
        themes: List[dict],
        gender: Optional[str] = None,
        category: str = 'first',
    ) -> Optional[str]:
        """Return a random name matching ``themes`` (and optionally gender).

        Falls back progressively: drops the gender filter, then the category
        filter, before giving up and returning ``None``.
        """
        if not themes:
            return None

        gender_norm = self._normalize_gender(gender)

        for current_gender, current_category in (
            (gender_norm, category),
            ('any', category),
            (gender_norm, 'any'),
            ('any', 'any'),
            (None, None),
        ):
            row = self._query_random(themes, current_gender, current_category)
            if row is not None:
                return row.name
        return None

    def _query_random(self, themes, gender, category):
        from sqlalchemy import tuple_

        q = self.session.query(NameLibrary).filter(
            tuple_(NameLibrary.source, NameLibrary.theme).in_(
                [(t['source'], t['theme']) for t in themes]))

        if gender is not None:
            q = q.filter(NameLibrary.gender.in_([gender, 'any']))
        if category is not None:
            q = q.filter(NameLibrary.category.in_([category, 'any']))

        # Portable random row: pick a random offset within the filtered set.
        # Avoids dialect-specific RAND()/RANDOM() functions.
        total = q.with_entities(func.count(NameLibrary.id)).scalar()
        if not total:
            return None
        return q.offset(random.randint(0, total - 1)).first()

    @staticmethod
    def _normalize_gender(gender) -> str:
        if isinstance(gender, bool):
            return 'male' if gender else 'female'
        if isinstance(gender, str) and gender.lower() in ('male', 'female'):
            return gender.lower()
        return 'any'
