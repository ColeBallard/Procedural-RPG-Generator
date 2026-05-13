# elevenlabs_service.py
"""Thin wrapper over the ElevenLabs HTTP API used by the TTS feature.

Two responsibilities:

  1. Voice search at world-building time. ``find_voice_for_character`` maps
     a character's traits (gender, race, age) to one of the voices in the
     caller's ElevenLabs library via ``GET /v2/voices``. Returns the chosen
     voice id or ``None`` when nothing matches / the call fails.

  2. Text-to-speech at playback time. ``synthesize`` returns the raw mp3
     bytes for a piece of text rendered with a given voice id, with an
     on-disk cache keyed by (voice_id, text-hash) so the same line is never
     re-billed against the user's quota.

Every public function is forgiving: missing keys, network errors, and
malformed responses all return ``None`` rather than raising, because TTS
is a UX layer and must not break gameplay.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Fixed narrator voice id. Resolved at TTS time when the transcript entry's
# speaker is the narrator (or has no speaker set), so it never needs to be
# persisted on a Character row.
NARRATOR_VOICE_ID = "L1aJrPa7pLJEyYlh3Ilq"

_API_BASE = "https://api.elevenlabs.io"
_VOICES_URL = f"{_API_BASE}/v2/voices"
_TTS_URL_TMPL = f"{_API_BASE}/v1/text-to-speech/{{voice_id}}"
_DEFAULT_MODEL = "eleven_multilingual_v2"
_DEFAULT_TIMEOUT = 30

# On-disk cache caps for ``synthesize``. The cache is unbounded by default
# in scope (one mp3 per (voice_id, text-hash) tuple), so a long-running
# server eventually accumulates an arbitrary amount of audio. These caps
# trigger a mtime-based LRU sweep after every successful write so the
# directory cannot grow without bound. Both ceilings are enforced; the
# stricter one wins on any given sweep.
_DEFAULT_CACHE_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB
_DEFAULT_CACHE_MAX_FILES = 1000


def _gender_label(gender):
    # Character.gender is the legacy boolean (True=male, False=female,
    # None=unknown). ElevenLabs accepts the string labels below.
    if gender is True:
        return "male"
    if gender is False:
        return "female"
    return None


def _age_bucket(date_of_birth, current_dt=None):
    """Translate a DOB into one of ElevenLabs' age buckets, when possible."""
    if not isinstance(date_of_birth, datetime):
        return None
    ref = current_dt if isinstance(current_dt, datetime) else datetime.now()
    years = (ref - date_of_birth).days // 365
    if years < 0:
        return None
    if years < 30:
        return "young"
    if years < 55:
        return "middle_aged"
    return "old"


def _fetch_library_voices(api_key, *, max_pages=3, page_size=100):
    """Pull the caller's voice library from /v2/voices, following pagination.

    Returns the list of voice payloads on success, or ``None`` on any
    transport / HTTP / decode failure so callers can distinguish "library
    is empty" (``[]``) from "request failed" (``None``).
    """
    voices = []
    next_page_token = None
    for _ in range(max_pages):
        params = {"page_size": page_size, "include_total_count": "false"}
        if next_page_token:
            params["next_page_token"] = next_page_token
        try:
            r = requests.get(
                _VOICES_URL,
                params=params,
                headers={"xi-api-key": api_key, "accept": "application/json"},
                timeout=_DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            log.info("ElevenLabs voice search failed: %s", e)
            return None
        if not r.ok:
            log.info("ElevenLabs voice search returned %s", r.status_code)
            return None
        try:
            payload = r.json() or {}
        except ValueError:
            return None
        voices.extend(payload.get("voices") or [])
        if not payload.get("has_more"):
            break
        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break
    return voices


def _normalize_age_label(value):
    """Collapse the assorted age strings ElevenLabs ships into our buckets."""
    if not value:
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if "young" in v or v in {"teen", "child"}:
        return "young"
    if "middle" in v:
        return "middle_aged"
    if "old" in v or "senior" in v or "elder" in v:
        return "old"
    return v or None


def _tokenize(text):
    """Split a free-text hint into lowercase keyword tokens for scoring."""
    if not text:
        return []
    out = []
    cur = []
    for ch in str(text).lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    # Drop trivial stopwords so a verbose description doesn't drown out
    # the few words that actually carry voice character.
    stop = {"a", "an", "the", "with", "and", "of", "in", "on", "to", "is",
            "for", "very", "quite", "rather", "but", "or"}
    return [t for t in out if t and t not in stop]


def _score_voice(voice, *, want_gender, want_age, terms):
    """Rank a library voice against the desired traits.

    Returns a non-negative score, or ``-1`` when ``want_gender`` is set
    and the voice's gender label disagrees (hard disqualification so a
    male character never gets a female voice when the library has
    enough labelled options).
    """
    labels = voice.get("labels") or {}
    label_gender = (labels.get("gender") or "").strip().lower() or None
    label_age = _normalize_age_label(labels.get("age"))

    score = 0
    if want_gender:
        if label_gender is None:
            score -= 5
        elif label_gender == want_gender:
            score += 100
        else:
            return -1
    if want_age:
        if label_age == want_age:
            score += 40
        elif label_age:
            score -= 5

    if terms:
        haystack_parts = [
            voice.get("name") or "",
            voice.get("description") or "",
            labels.get("descriptive") or "",
            labels.get("description") or "",
            labels.get("use_case") or "",
            labels.get("accent") or "",
            labels.get("language") or "",
        ]
        haystack = " ".join(haystack_parts).lower()
        for term in terms:
            if term and term in haystack:
                score += 10
    return score


def find_voice_for_character(api_key, *, gender=None, date_of_birth=None,
                             current_dt=None, race=None, search_text=None):
    """Pick a voice id from the caller's ElevenLabs library by character traits.

    The /v2/voices endpoint silently ignores ``gender``/``age`` query
    params (those only work on /v1/shared-voices), and its ``search``
    parameter only matches voice name / description / labels. So we pull
    the library once and score each voice client-side against the
    character's gender, age bucket, race, and any free-text hint. The
    narrator voice is excluded so dialogue never collapses onto it.
    Returns the best-scoring voice id or ``None`` on any failure.
    """
    if not api_key:
        return None

    voices = _fetch_library_voices(api_key)
    if voices is None:
        return None
    voices = [v for v in voices
              if v.get("voice_id") and v.get("voice_id") != NARRATOR_VOICE_ID]
    if not voices:
        return None

    want_gender = _gender_label(gender)
    want_age = _age_bucket(date_of_birth, current_dt)
    terms = _tokenize(race) + _tokenize(search_text)

    # Progressively relax: full traits, then drop free text, then drop
    # age, then drop gender. The first attempt that yields any candidate
    # wins so a verbose hint never leaves a character voiceless.
    attempts = [
        (want_gender, want_age, terms),
        (want_gender, want_age, []),
        (want_gender, None, []),
        (None, None, []),
    ]
    for w_gender, w_age, w_terms in attempts:
        scored = []
        for v in voices:
            s = _score_voice(v, want_gender=w_gender, want_age=w_age,
                             terms=w_terms)
            if s >= 0:
                scored.append((s, v))
        if not scored:
            continue
        scored.sort(key=lambda t: t[0], reverse=True)
        top_score = scored[0][0]
        # Tie-break across the top tier so two characters with identical
        # traits don't all collapse onto the same voice. The seed mixes
        # every input so different characters land on different voices.
        top = [v for s, v in scored if s >= top_score - 5]
        seed_key = "|".join([
            want_gender or "",
            want_age or "",
            date_of_birth.isoformat() if isinstance(date_of_birth, datetime) else "",
            (race or "").lower(),
            (search_text or "").lower(),
        ])
        idx = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest(), 16) % len(top)
        return top[idx].get("voice_id")
    return None


def _cache_path(cache_dir, voice_id, text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    safe_voice = "".join(c for c in (voice_id or "default") if c.isalnum() or c in "-_")
    return os.path.join(cache_dir, f"{safe_voice}_{digest}.mp3")


def _evict_cache_if_needed(cache_dir, max_bytes, max_files):
    """LRU-evict mp3 files in ``cache_dir`` until both caps are satisfied.

    Sweep is best-effort: filesystem races (a file being deleted between
    the listdir and the unlink) are swallowed because the cache is
    advisory. Only entries with a ``.mp3`` extension are considered so a
    sibling file in the same directory cannot be wiped by mistake.
    """
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return
    entries = []
    total = 0
    for name in names:
        if not name.endswith('.mp3'):
            continue
        path = os.path.join(cache_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        entries.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    count = len(entries)
    if total <= max_bytes and count <= max_files:
        return
    entries.sort(key=lambda e: e[0])  # oldest mtime first
    for _, size, path in entries:
        if total <= max_bytes and count <= max_files:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        count -= 1


def synthesize(api_key, voice_id, text, *, cache_dir=None,
               model_id=_DEFAULT_MODEL,
               cache_max_bytes=_DEFAULT_CACHE_MAX_BYTES,
               cache_max_files=_DEFAULT_CACHE_MAX_FILES):
    """Render ``text`` to mp3 bytes via ElevenLabs TTS, with on-disk cache.

    Returns the audio bytes on success or ``None`` on any failure (missing
    key, network error, non-2xx response). When ``cache_dir`` is provided a
    successful synthesis is written to disk and subsequent calls with the
    same (voice_id, text) tuple return the cached bytes without hitting
    the API. The cache is bounded by ``cache_max_bytes`` and
    ``cache_max_files``; oldest-by-mtime entries are evicted after every
    successful write that pushes the directory over either limit.
    """
    if not text:
        return None
    voice_id = voice_id or NARRATOR_VOICE_ID

    if cache_dir:
        path = _cache_path(cache_dir, voice_id, text)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                # Touch on hit so a frequently-replayed line doesn't get
                # evicted by a burst of new lines.
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                return data
            except OSError:
                pass

    if not api_key:
        return None

    try:
        r = requests.post(
            _TTS_URL_TMPL.format(voice_id=voice_id),
            json={"text": text, "model_id": model_id},
            headers={
                "xi-api-key": api_key,
                "accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            timeout=_DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        log.info("ElevenLabs TTS request failed: %s", e)
        return None
    if not r.ok:
        log.info("ElevenLabs TTS returned %s", r.status_code)
        return None

    audio = r.content
    if cache_dir and audio:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(_cache_path(cache_dir, voice_id, text), "wb") as fh:
                fh.write(audio)
            _evict_cache_if_needed(cache_dir, cache_max_bytes, cache_max_files)
        except OSError as e:
            log.info("ElevenLabs TTS cache write failed: %s", e)
    return audio
