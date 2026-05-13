"""Tests for the deterministic dice / skill check engine.

The service is intentionally pure: every roll takes a ``Random`` instance
so the suite can pin every face. We never hit the system RNG.
"""
import random

from app.services import dice_service as d


def test_modifier_5e_table():
    # Spot-check a handful of points along the 5e ability modifier ladder.
    assert d.modifier(1) == -5
    assert d.modifier(8) == -1
    assert d.modifier(10) == 0
    assert d.modifier(11) == 0
    assert d.modifier(15) == 2
    assert d.modifier(20) == 5


def test_modifier_handles_garbage_inputs():
    assert d.modifier(None) == 0
    assert d.modifier('abc') == 0


def test_proficiency_bonus_scales_per_srd():
    assert d.proficiency_bonus(1) == 2
    assert d.proficiency_bonus(4) == 2
    assert d.proficiency_bonus(5) == 3
    assert d.proficiency_bonus(9) == 4
    assert d.proficiency_bonus(17) == 6
    # Floor at level 1 so a missing / zero level doesn't go below +2.
    assert d.proficiency_bonus(0) == 2
    assert d.proficiency_bonus(None) == 2


def test_roll_dice_uses_provided_rng():
    rng = random.Random(42)
    out = d.roll_dice(3, 6, modifier=2, rng=rng)
    assert out.expression == '3d6+2'
    assert len(out.rolls) == 3
    assert all(1 <= r <= 6 for r in out.rolls)
    assert out.total == sum(out.rolls) + 2


def test_roll_d20_advantage_picks_higher():
    # A pinned RNG so advantage demonstrably picks max(a, b).
    class StubRNG:
        def __init__(self):
            self.values = [3, 18]
        def randint(self, lo, hi):
            return self.values.pop(0)
    out = d.roll_d20(advantage=True, rng=StubRNG())
    assert out.rolls == [3, 18]
    assert out.total == 18
    assert out.expression == '1d20kh1'


def test_roll_d20_disadvantage_picks_lower():
    class StubRNG:
        def __init__(self):
            self.values = [17, 4]
        def randint(self, lo, hi):
            return self.values.pop(0)
    out = d.roll_d20(disadvantage=True, rng=StubRNG())
    assert out.total == 4
    assert out.expression == '1d20kl1'


def test_roll_d20_advantage_and_disadvantage_cancel():
    # Per 5e, simultaneous advantage + disadvantage = flat d20 (one roll).
    class StubRNG:
        def __init__(self):
            self.calls = 0
        def randint(self, lo, hi):
            self.calls += 1
            return 11
    rng = StubRNG()
    out = d.roll_d20(advantage=True, disadvantage=True, rng=rng)
    assert rng.calls == 1
    assert out.total == 11
    assert out.expression == '1d20'


class _Char:
    """Minimal stand-in for an ORM Character for check resolution."""
    def __init__(self, **scores):
        self.level = scores.pop('level', 1)
        for k, v in scores.items():
            setattr(self, k, v)


def test_perform_check_total_includes_ability_and_proficiency():
    # Pinned d20 face via stub so the breakdown math is exact regardless
    # of RNG implementation; the service is the unit under test, not the
    # std-library RNG.
    class Stub:
        def randint(self, lo, hi):
            return 12
    char = _Char(strength=16, level=5)  # str mod +3, prof +3 at level 5
    result = d.perform_check(char, 'strength', dc=15,
                              proficient=True, rng=Stub())
    assert result.raw_d20 == 12
    assert result.ability_modifier == 3
    assert result.proficiency_bonus == 3
    assert result.total == 12 + 3 + 3
    assert result.success is True
    assert result.critical_success is False
    assert result.critical_failure is False


def test_perform_check_critical_success_overrides_dc():
    class Stub:
        def randint(self, lo, hi):
            return 20
    char = _Char(charisma=8)  # negative mod, but nat 20 must succeed
    result = d.perform_check(char, 'charisma', dc=30, rng=Stub())
    assert result.raw_d20 == 20
    assert result.critical_success is True
    assert result.success is True


def test_perform_check_critical_failure_overrides_dc():
    class Stub:
        def randint(self, lo, hi):
            return 1
    char = _Char(strength=20)  # huge mod, but nat 1 must fail
    result = d.perform_check(char, 'strength', dc=5, rng=Stub())
    assert result.raw_d20 == 1
    assert result.critical_failure is True
    assert result.success is False


def test_perform_check_unknown_ability_falls_back_to_strength():
    class Stub:
        def randint(self, lo, hi):
            return 10
    char = _Char(strength=14)
    result = d.perform_check(char, 'mystery_stat', dc=10, rng=Stub())
    assert result.ability == 'strength'
    assert result.ability_modifier == d.modifier(14)


def test_format_check_renders_breakdown_and_verdict():
    char = _Char(strength=16, level=5)
    class Stub:
        def randint(self, lo, hi):
            return 12
    res = d.perform_check(char, 'strength', dc=15, proficient=True, rng=Stub())
    line = d.format_check(res)
    assert 'd20=12' in line
    assert 'STR +3' in line
    assert 'prof +3' in line
    assert '= 18' in line
    assert 'SUCCESS' in line


def test_to_meta_round_trip_keys():
    res = d.perform_check(_Char(strength=10), 'strength', dc=10,
                           rng=random.Random(0))
    meta = res.to_meta()
    for key in ('kind', 'd20', 'ability', 'dc', 'total', 'success',
                'critical_success', 'critical_failure', 'rolls'):
        assert key in meta
    assert meta['kind'] == 'dice_check'
