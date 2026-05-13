"""Dice + skill check engine (D&D 5e flavoured).

Pure functions only: every call is deterministic given a ``Random``
instance, which makes the unit tests trivial and keeps the rest of the
codebase from having to mock the standard library. The DM-adjudication
loop in ``app/routes.py`` calls ``perform_check`` after the LLM picks
the ability and DC; combat, scenarios, and travel hook in the same way
when they need a check rather than a flat outcome.

Vocabulary mirrors the player-facing rules so test names and transcript
strings read like a rulebook:

  * ``ability``       -- one of ``ABILITIES`` (the six classic stats).
  * ``modifier(score)`` -- D&D 5e ability modifier ((score - 10) // 2).
  * ``proficiency_bonus(level)`` -- +2 at level 1, scaling per the SRD.
  * ``roll_d20`` / ``roll_dice`` -- raw dice with optional advantage.
  * ``perform_check`` -- full ability/skill check vs a difficulty class.
  * ``saving_throw`` -- alias of ``perform_check`` with no proficiency
    by default; callers may pass ``proficient=True``.

The advantage / disadvantage rules cancel out (matching 5e): if both
``advantage`` and ``disadvantage`` are set the roll is a flat d20.
"""
from __future__ import annotations

import random as _random
from dataclasses import dataclass, field
from typing import List, Optional


ABILITIES = (
    'strength', 'speed', 'agility', 'intelligence', 'wisdom', 'charisma',
)

# Difficulty class ladder borrowed from the DMG. The DM prompt is told
# to pick from this list so the LLM doesn't drift into wild numbers.
DC_LADDER = {
    'trivial': 5,
    'easy': 10,
    'medium': 15,
    'hard': 20,
    'very_hard': 25,
    'nearly_impossible': 30,
}

# Standard 5e proficiency bonus by character level.
def proficiency_bonus(level):
    lvl = max(1, int(level or 1))
    return 2 + (lvl - 1) // 4


def modifier(score):
    """D&D 5e ability modifier: ``(score - 10) // 2``.

    Floor division so a 9 returns -1 and an 11 returns 0, matching the
    published table. ``None`` / non-numeric scores are treated as 10
    (modifier 0) so unscored characters don't blow up a check.
    """
    try:
        return (int(score) - 10) // 2
    except (TypeError, ValueError):
        return 0


def _rng(rng):
    return rng if rng is not None else _random


@dataclass
class DiceRoll:
    """Outcome of a single dice expression evaluation."""
    expression: str
    rolls: List[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0


def roll_dice(count, sides, *, modifier=0, rng=None):
    """Roll ``count`` dice of ``sides`` faces, sum + flat modifier."""
    rng = _rng(rng)
    count = max(1, int(count))
    sides = max(2, int(sides))
    rolls = [rng.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + int(modifier)
    expr = f"{count}d{sides}{_fmt_mod(modifier)}"
    return DiceRoll(expression=expr, rolls=rolls, modifier=int(modifier),
                    total=total)


def roll_d20(*, advantage=False, disadvantage=False, rng=None):
    """Roll a d20, applying advantage/disadvantage (they cancel)."""
    rng = _rng(rng)
    if advantage and disadvantage:
        advantage = disadvantage = False
    a = rng.randint(1, 20)
    if not (advantage or disadvantage):
        return DiceRoll(expression='1d20', rolls=[a], total=a)
    b = rng.randint(1, 20)
    pick = max(a, b) if advantage else min(a, b)
    expr = '1d20kh1' if advantage else '1d20kl1'
    return DiceRoll(expression=expr, rolls=[a, b], total=pick)


@dataclass
class CheckResult:
    """Full breakdown of an ability/skill check vs a DC."""
    ability: str
    dc: int
    raw_d20: int
    advantage: bool
    disadvantage: bool
    ability_modifier: int
    proficiency_bonus: int
    other_modifier: int
    total: int
    success: bool
    critical_success: bool
    critical_failure: bool
    rolls: List[int]
    description: str = ''

    def to_meta(self):
        """Compact dict shape suitable for the transcript ``meta`` column."""
        return {
            'kind': 'dice_check',
            'ability': self.ability,
            'dc': self.dc,
            'd20': self.raw_d20,
            'rolls': list(self.rolls),
            'advantage': self.advantage,
            'disadvantage': self.disadvantage,
            'ability_modifier': self.ability_modifier,
            'proficiency_bonus': self.proficiency_bonus,
            'other_modifier': self.other_modifier,
            'total': self.total,
            'success': self.success,
            'critical_success': self.critical_success,
            'critical_failure': self.critical_failure,
            'description': self.description or '',
        }


def perform_check(character, ability, dc, *, proficient=False,
                  other_modifier=0, advantage=False, disadvantage=False,
                  rng=None, description=''):
    """Resolve a single ability check against ``dc`` and return a result.

    ``character`` may be an ORM ``Character`` row or anything with the
    six ability attributes; level is read from ``.level`` for the
    proficiency bonus when ``proficient`` is set. Critical success on a
    natural 20, critical failure on a natural 1 -- both flagged but the
    success/failure flag still respects the DC math (a nat 20 beats any
    DC, a nat 1 fails any DC, mirroring 5e house-rule conventions).
    """
    ability = (ability or '').strip().lower()
    if ability not in ABILITIES:
        ability = 'strength'
    score = getattr(character, ability, 10) if character is not None else 10
    abil_mod = modifier(score)
    prof = proficiency_bonus(getattr(character, 'level', 1)) if proficient else 0
    d20 = roll_d20(advantage=advantage, disadvantage=disadvantage, rng=rng)
    raw = d20.total
    total = raw + abil_mod + prof + int(other_modifier)
    crit_success = (raw == 20)
    crit_failure = (raw == 1)
    if crit_success:
        success = True
    elif crit_failure:
        success = False
    else:
        success = total >= int(dc)
    return CheckResult(
        ability=ability, dc=int(dc), raw_d20=raw, rolls=list(d20.rolls),
        advantage=advantage and not disadvantage,
        disadvantage=disadvantage and not advantage,
        ability_modifier=abil_mod, proficiency_bonus=prof,
        other_modifier=int(other_modifier), total=total, success=success,
        critical_success=crit_success, critical_failure=crit_failure,
        description=description or '',
    )


def saving_throw(character, ability, dc, *, proficient=False, rng=None,
                 description=''):
    """Alias of ``perform_check`` for read-clarity at call sites."""
    return perform_check(character, ability, dc, proficient=proficient,
                         rng=rng, description=description)


def format_check(result):
    """One-line transcript line summarising a ``CheckResult``."""
    bits = [f"d20={result.raw_d20}"]
    if result.ability_modifier:
        bits.append(f"{result.ability[:3].upper()} {_fmt_mod(result.ability_modifier)}")
    if result.proficiency_bonus:
        bits.append(f"prof {_fmt_mod(result.proficiency_bonus)}")
    if result.other_modifier:
        bits.append(f"mod {_fmt_mod(result.other_modifier)}")
    verdict = 'SUCCESS' if result.success else 'FAILURE'
    if result.critical_success:
        verdict = 'CRITICAL SUCCESS'
    elif result.critical_failure:
        verdict = 'CRITICAL FAILURE'
    return (f"[{result.ability.title()} check vs DC {result.dc}] "
            f"{' + '.join(bits)} = {result.total} -> {verdict}")


def _fmt_mod(n):
    n = int(n)
    if n == 0:
        return ''
    return f"+{n}" if n > 0 else f"{n}"
