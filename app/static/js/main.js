import { changeTabViews, setTabClickEvents } from './ui/tabs.js';
import { setLocalStorageItem, getLocalStorageItem } from './utils/storage.js';
import { compileTemplate } from './utils/template.js';
import { renderWorldMap, clearWorldMap } from './ui/worldMap.js';
import {
    initScenarioController, setActiveSeed as setScenarioSeed,
    mountScenario, unmountScenario, isScenarioActive,
} from './game/scenarioController.js';

let narrativeTemplate;

// Narrative panel paginates through the persisted transcript so long sessions
// don't render thousands of <li> nodes at once. narrativeEntries holds the
// canonical in-memory copy (chronological), narrativeVisibleCount is the
// number of most-recent entries currently mounted in #narrativeList. A
// "Show More" button at the top reveals the next older page on demand.
const NARRATIVE_PAGE_SIZE = 25;
let narrativeEntries = [];
let narrativeVisibleCount = 0;

// HTML-escape arbitrary values before interpolating them into a string of
// markup. Uses the standard jQuery text-then-html idiom so callers can keep
// building markup with template literals without introducing XSS sinks.
function escapeHtml(val) {
    return $('<div>').text(val == null ? '' : String(val)).html();
}

// Read the double-submit CSRF cookie set by the server. Returned as-is and
// echoed back in X-CSRF-Token on every state-changing request.
function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
}

// Cached "the server has a Grok key bound to my session" flag. The actual
// key value is never held in the browser anymore: it's pushed once via
// POST /api/grok-key after sign-in and lives in the server's encrypted
// in-memory store keyed by user_id. refreshGrokKeyStatus() re-syncs this
// flag from GET /api/grok-key and is called after login, after Save/Clear
// in the API Keys view, and on page load.
let _grokKeyAvailable = false;

function hasGrokKey() {
    return _grokKeyAvailable;
}

function refreshGrokKeyStatus() {
    return $.ajax({ url: '/api/grok-key', type: 'GET' })
        .then(function (response) {
            _grokKeyAvailable = !!(response && response.present);
            updateGameMenuButtonsState();
            updateApiKeysViewStatus();
            return _grokKeyAvailable;
        })
        .catch(function () {
            _grokKeyAvailable = false;
            updateGameMenuButtonsState();
            updateApiKeysViewStatus();
            return false;
        });
}

// --- ElevenLabs key + TTS playback -----------------------------------------
// Mirrors the Grok pattern: the key value lives only in the server-side
// store; the browser only knows whether one is bound. The TTS toggle state
// is local-only (per-browser) because it's a UX preference, not a secret.
let _elevenlabsKeyAvailable = false;
let _ttsEnabled = false;
const TTS_ENABLED_STORAGE_KEY = 'tts-enabled';
// In-flight playback handles. Object URLs for fetched audio are kept around
// so a second click on the same entry replays without a network round trip.
const _ttsAudioCache = new Map();   // entryId -> ObjectURL string
let _ttsCurrentAudio = null;
let _ttsCurrentEntryId = null;
// FIFO queue of {seedId, entryId} pending autoplay. When a turn produces
// several transcript entries (narration paragraphs + dialogue lines) they
// must play one after another in chronological order rather than racing
// each other. Manual clicks bypass the queue and preempt whatever is
// currently playing.
const _ttsQueue = [];

function hasElevenLabsKey() {
    return _elevenlabsKeyAvailable;
}

function refreshElevenLabsKeyStatus() {
    return $.ajax({ url: '/api/elevenlabs-key', type: 'GET' })
        .then(function (response) {
            _elevenlabsKeyAvailable = !!(response && response.present);
            updateElevenLabsKeysViewStatus();
            updateTtsToggleAvailability();
            return _elevenlabsKeyAvailable;
        })
        .catch(function () {
            _elevenlabsKeyAvailable = false;
            updateElevenLabsKeysViewStatus();
            updateTtsToggleAvailability();
            return false;
        });
}

function updateElevenLabsKeysViewStatus() {
    const present = hasElevenLabsKey();
    $('#elevenlabs-api-key-status')
        .text(present ? '✅ Key configured server-side' : '⚠️ No key configured')
        .toggleClass('grok-key-status-ok', present)
        .toggleClass('grok-key-status-missing', !present);
    $('#clear-elevenlabs-key-btn').prop('disabled', !present);
}

// The TTS toggle and per-entry play buttons are visually disabled when no
// key is configured so the user gets immediate feedback rather than a
// failed request after clicking. The toggle itself stays clickable so the
// user can pre-set their preference before saving a key.
function updateTtsToggleAvailability() {
    const enabled = hasElevenLabsKey();
    $('.narrative-tts-btn').toggleClass('is-disabled', !enabled);
}

function getTtsEnabled() {
    return _ttsEnabled;
}

function setTtsEnabled(enabled) {
    _ttsEnabled = !!enabled;
    try {
        localStorage.setItem(TTS_ENABLED_STORAGE_KEY, _ttsEnabled ? '1' : '0');
    } catch (_) { /* ignore storage errors */ }
    $('#tts-toggle').prop('checked', _ttsEnabled);
    if (!_ttsEnabled) {
        stopCurrentTtsPlayback();
    }
}

function loadTtsEnabledFromStorage() {
    try {
        _ttsEnabled = localStorage.getItem(TTS_ENABLED_STORAGE_KEY) === '1';
    } catch (_) {
        _ttsEnabled = false;
    }
    $('#tts-toggle').prop('checked', _ttsEnabled);
}

function stopCurrentTtsPlayback() {
    if (_ttsCurrentAudio) {
        try { _ttsCurrentAudio.pause(); } catch (_) {}
        _ttsCurrentAudio = null;
    }
    if (_ttsCurrentEntryId != null) {
        $(`.narrative-tts-btn[data-entry-id="${_ttsCurrentEntryId}"]`)
            .removeClass('is-playing is-loading');
        _ttsCurrentEntryId = null;
    }
    // Drop any pending autoplay so a hard stop (logout / TTS toggle off /
    // manual click) doesn't leave queued entries waiting in the wings.
    _ttsQueue.length = 0;
}

// Append an entry to the autoplay queue. New transcript entries surfaced
// by ``updateNarrativeList`` flow through here so a turn that produces
// several paragraphs of narration plus a dialogue line plays them one
// after another in chronological order rather than racing each other or
// having later requests preempt earlier ones.
function enqueueTtsForEntry(seedId, entryId) {
    if (!seedId || entryId == null) return;
    if (_ttsCurrentEntryId === entryId) return;
    if (_ttsQueue.some(item => item.entryId === entryId)) return;
    _ttsQueue.push({ seedId: seedId, entryId: entryId });
    _maybeStartNextInQueue();
}

// Pop the next queued entry and start playing it, but only when nothing
// is currently in flight. Called both right after enqueueing and when the
// previously playing entry finishes / errors out.
function _maybeStartNextInQueue() {
    if (_ttsCurrentAudio || _ttsCurrentEntryId != null) return;
    if (_ttsQueue.length === 0) return;
    const next = _ttsQueue.shift();
    _playTtsForEntryNow(next.seedId, next.entryId, null);
}

// Manual click handler: a re-click on the playing entry stops it and lets
// the queue resume; clicking a different entry preempts whatever is
// playing and clears the autoplay queue so the user's explicit choice
// wins.
function playTtsForEntry(seedId, entryId, $btn) {
    if (!seedId || entryId == null) return;
    if (_ttsCurrentEntryId === entryId) {
        stopCurrentTtsPlayback();
        return;
    }
    stopCurrentTtsPlayback();
    _playTtsForEntryNow(seedId, entryId, $btn);
}

// Internal: actually fetch and play the entry's mp3. Caller is responsible
// for ensuring no other audio is currently in flight. The $btn argument
// reflects loading / playing state so the user can see what's happening;
// when omitted we look it up from the DOM (queue path doesn't have one).
function _playTtsForEntryNow(seedId, entryId, $btn) {
    _ttsCurrentEntryId = entryId;
    if (!$btn || !$btn.length) {
        $btn = $(`.narrative-tts-btn[data-entry-id="${entryId}"]`);
    }
    $btn.addClass('is-loading');

    const cached = _ttsAudioCache.get(entryId);
    if (cached) {
        startAudio(cached, $btn, entryId);
        return;
    }

    fetch(`/api/tts/${seedId}/${entryId}`, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': getCsrfToken() },
    })
        .then(function (resp) {
            if (!resp.ok) throw new Error(`tts ${resp.status}`);
            return resp.blob();
        })
        .then(function (blob) {
            const url = URL.createObjectURL(blob);
            _ttsAudioCache.set(entryId, url);
            startAudio(url, $btn, entryId);
        })
        .catch(function () {
            $btn.removeClass('is-loading is-playing');
            if (_ttsCurrentEntryId === entryId) {
                _ttsCurrentEntryId = null;
                _ttsCurrentAudio = null;
            }
            // A failed fetch shouldn't strand the rest of the queue; move
            // on to the next entry so the player still hears later lines.
            _maybeStartNextInQueue();
        });
}

function startAudio(url, $btn, entryId) {
    const audio = new Audio(url);
    _ttsCurrentAudio = audio;
    $btn.removeClass('is-loading').addClass('is-playing');
    audio.addEventListener('ended', function () {
        if (_ttsCurrentEntryId === entryId) {
            $btn.removeClass('is-playing');
            _ttsCurrentAudio = null;
            _ttsCurrentEntryId = null;
        }
        _maybeStartNextInQueue();
    });
    audio.play().catch(function () {
        $btn.removeClass('is-loading is-playing');
        if (_ttsCurrentEntryId === entryId) {
            _ttsCurrentAudio = null;
            _ttsCurrentEntryId = null;
        }
        _maybeStartNextInQueue();
    });
}

// Inject the CSRF header on every jQuery ajax request. The Grok key is no
// longer attached to outbound requests: gated routes pull it from the
// server-side, session-scoped store. The server still ignores CSRF for
// safe methods.
$.ajaxSetup({
    beforeSend: function (xhr) {
        const token = getCsrfToken();
        if (token) {
            xhr.setRequestHeader('X-CSRF-Token', token);
        }
    }
});

// Compiled templates for each entity type rendered in the game-view
// accordions. Populated by loadFrontendTemplates() once the user is
// authenticated, since /templates/* is gated by @login_required and an
// anonymous fetch would 401.
const entityTemplates = {};

// Guard so the post-login template fetch only fires once per page load
// even though showApp() can be reached from the auth-check path, the
// sign-in handler, and the sign-up handler.
let _templatesLoaded = false;

// Fetch the Handlebars templates served from /templates/*. Run after
// authentication because the route is gated by @login_required; calling
// it on document-ready (before sign-in) produced the 401 cascade and
// left narrativeTemplate / entityTemplates empty for the rest of the
// session. Per-fetch failures reset the guard so a later showApp() can
// retry the whole batch.
function loadFrontendTemplates() {
    if (_templatesLoaded) return;
    _templatesLoaded = true;

    $.get('/templates/narrativeItem.hbs', function (template) {
        narrativeTemplate = compileTemplate(template);
    }).fail(function (jqXHR, textStatus, errorThrown) {
        _templatesLoaded = false;
        console.error('Error fetching narrative item template:', textStatus, errorThrown);
    });

    // Each entity template lives at /templates/<name>.html and is wrapped
    // in a <script type="text/x-handlebars-template"> tag. Strip the
    // wrapper before compiling so the inner markup is the template source.
    const names = ['location', 'event', 'interactingCharacter', 'quest', 'item'];
    names.forEach(function (name) {
        $.get(`/templates/${name}.html`)
            .done(function (html) {
                const match = html.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
                const source = match ? match[1] : html;
                entityTemplates[name] = compileTemplate(source);
            })
            .fail(function (jqXHR, textStatus) {
                _templatesLoaded = false;
                console.error(`Error fetching template ${name}.html:`, textStatus);
            });
    });
}

// Mapping of payload key -> { templateName, accordionId, filter? }.
// Stats, skills and statuses are no longer rendered as accordions;
// renderCharacterProfile() owns them alongside the main character's identity.
// The optional filter narrows the payload before rendering: characters the
// MC has never met (acquaintance_level === 'unknown') are dropped from the
// Characters accordion so the panel only lists NPCs the player actually
// knows. The /api/world response still includes them for other consumers.
const ENTITY_RENDER_MAP = {
    locations:  { template: 'location',             accordion: 'locationsAccordion' },
    events:     { template: 'event',                accordion: 'eventsAccordion' },
    characters: {
        template: 'interactingCharacter',
        accordion: 'charactersAccordion',
        filter: (c) => c && c.acquaintance_level !== 'unknown',
    },
    quests:     { template: 'quest',                accordion: 'questsAccordion' },
    items:      { template: 'item',                 accordion: 'itemsAccordion' },
};

// Stat id -> short label + icon used by the character profile grid.
const PROFILE_STAT_META = {
    strength:     { icon: '💪', label: 'STR' },
    speed:        { icon: '⚡', label: 'SPD' },
    agility:      { icon: '🤸', label: 'AGI' },
    intelligence: { icon: '🧠', label: 'INT' },
    wisdom:       { icon: '📚', label: 'WIS' },
    charisma:     { icon: '💬', label: 'CHA' },
};

// === Header auto-collapse state ===
let _headerHoverStart = null;    // timestamp (ms) when cursor entered the header
let _headerCollapseTimer = null; // pending setTimeout id

// === World clock state ===
// Last clock payload rendered into #world-clock; tracking it lets the
// renderer trigger a brief tick animation only when the displayed time
// actually moved forward, instead of every refresh.
let _lastClockDisplay = '';

// Time-of-day buckets emitted by time_service.time_of_day(). The icon
// map gives the badge a quick visual cue and the label map normalises
// the lower-case backend value into a properly-cased display string so
// the badge no longer leans on font small-caps for capitalisation.
const WORLD_CLOCK_TOD_ICONS = {
    'dawn': '🌅',
    'morning': '☀️',
    'noon': '🌞',
    'afternoon': '🌤️',
    'evening': '🌆',
    'night': '🌙',
    'late night': '🌌',
};
const WORLD_CLOCK_TOD_LABELS = {
    'dawn': 'Dawn',
    'morning': 'Morning',
    'noon': 'Noon',
    'afternoon': 'Afternoon',
    'evening': 'Evening',
    'night': 'Night',
    'late night': 'Late Night',
};
const WORLD_CLOCK_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Render or hide the in-world clock badge from a payload of the shape
// returned by time_service.serialize_clock(). Falsy / empty payloads
// hide the badge so the world-build phase doesn't show a stale time.
// Date/time are derived from clock.iso rather than clock.display so the
// time-of-day suffix that format_clock() bakes in for prompt context
// doesn't end up duplicated next to the standalone TOD badge.
function renderClock(clock) {
    const $clock = $('#world-clock');
    if (!$clock.length) return;
    const iso = clock && clock.iso;
    const isoMatch = iso && iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!isoMatch) {
        $clock.attr('hidden', true).removeAttr('data-tod');
        _lastClockDisplay = '';
        return;
    }
    const [, yr, mo, day, hh, mm] = isoMatch;
    const monthLabel = WORLD_CLOCK_MONTHS[parseInt(mo, 10) - 1] || mo;
    const dateStr = `${monthLabel} ${parseInt(day, 10)}, ${yr}`;
    const timeStr = `${hh}:${mm}`;
    const tod = (clock.time_of_day || 'unknown').toLowerCase();
    const todLabel = WORLD_CLOCK_TOD_LABELS[tod] || (clock.time_of_day || '');
    const todIcon = WORLD_CLOCK_TOD_ICONS[tod] || '🕰️';

    $clock.removeAttr('hidden');
    $clock.attr('data-tod', tod);
    $clock.attr('title', `${dateStr} · ${timeStr}${todLabel ? ' · ' + todLabel : ''}`);
    $clock.find('.world-clock-icon').text(todIcon);
    $clock.find('.world-clock-date').text(dateStr);
    $clock.find('.world-clock-time').text(timeStr);
    $clock.find('.world-clock-tod').text(todLabel);
    const fingerprint = `${dateStr} ${timeStr}`;
    if (fingerprint !== _lastClockDisplay && _lastClockDisplay !== '') {
        // Brief pulse so a meaningful jump (arbiter-adjudicated long action,
        // travel, rest) catches the player's eye without being noisy.
        $clock.removeClass('world-clock-tick');
        // Force reflow so re-adding the class restarts the animation.
        void $clock[0].offsetWidth;
        $clock.addClass('world-clock-tick');
    }
    _lastClockDisplay = fingerprint;
}

// Phase 6: dice roll overlay. Briefly flashes a centered d20 card with
// the rolled face, breakdown, and verdict every time a 'dice' transcript
// entry lands live. Pulls everything from the structured ``meta`` payload
// the backend forwards (see CheckResult.to_meta in dice_service); no
// re-parsing of the human-readable line is needed.
let _diceOverlayTimer = null;
function flashDiceOverlay(meta) {
    if (!meta || meta.kind !== 'dice_check') return;
    const $overlay = $('#dice-roll-overlay');
    if (!$overlay.length) return;
    const face = meta.d20 != null ? meta.d20 : '?';
    const ability = meta.ability ? String(meta.ability) : 'check';
    const dc = meta.dc != null ? meta.dc : '?';
    const total = meta.total != null ? meta.total : '?';
    let verdict = meta.success ? 'Success' : 'Failure';
    if (meta.critical_success) verdict = 'Critical Success';
    else if (meta.critical_failure) verdict = 'Critical Failure';
    $overlay.find('.dice-roll-d20').attr('data-dice-face', face);
    $overlay.find('.dice-roll-face').text(face);
    $overlay.find('.dice-roll-label')
        .text(`${ability.charAt(0).toUpperCase()}${ability.slice(1)} check`);
    $overlay.find('.dice-roll-detail').text(`vs DC ${dc}  •  total ${total}`);
    $overlay.find('.dice-roll-verdict').text(verdict);
    // Reset prior state classes before applying the new outcome so a
    // success right after a critical failure doesn't inherit red glow.
    $overlay.removeClass('visible success failure crit-success crit-fail');
    if (meta.success) $overlay.addClass('success');
    else $overlay.addClass('failure');
    if (meta.critical_success) $overlay.addClass('crit-success');
    if (meta.critical_failure) $overlay.addClass('crit-fail');
    // Restart the spin animation by clone-replacing the d20 element so
    // back-to-back rolls re-trigger the keyframes.
    const $d20 = $overlay.find('.dice-roll-d20');
    $d20.replaceWith($d20.clone());
    void $overlay[0].offsetWidth;
    $overlay.addClass('visible');
    if (_diceOverlayTimer) clearTimeout(_diceOverlayTimer);
    _diceOverlayTimer = setTimeout(function () {
        $overlay.removeClass('visible');
    }, 1400);
}

// === Action panel state ===
// The active seed id is captured here so the destination buttons can
// POST to /travel without re-deriving it from the URL on every click.
let _travelActiveSeedId = null;

// Travel destinations and suggested actions share one panel below the
// narrative; only one is visible at a time. ``_activePanel`` tracks
// which list is currently rendered (or null for "neither"); the boolean
// flags record whether each side has anything to show. setActivePanel()
// applies the state to the DOM; refreshActionPanels() re-evaluates the
// default after data updates.
let _activePanel = null;
let _travelHasContent = false;
let _suggestionsHasContent = false;

function setActivePanel(name) {
    if (name === 'travel' && !_travelHasContent) name = null;
    if (name === 'suggestions' && !_suggestionsHasContent) name = null;
    _activePanel = name;
    $('#travel-label')
        .toggleClass('active', name === 'travel')
        .attr('aria-expanded', name === 'travel' ? 'true' : 'false');
    $('#suggestions-label')
        .toggleClass('active', name === 'suggestions')
        .attr('aria-expanded', name === 'suggestions' ? 'true' : 'false');
    $('#travel-destinations').toggle(name === 'travel');
    $('#optionsList').toggle(name === 'suggestions');
    $('#action-panel').toggle(!!name);
}

function refreshActionPanels() {
    $('#travel-label').toggle(_travelHasContent);
    $('#suggestions-label').toggle(_suggestionsHasContent);
    $('#action-tabs').toggle(_travelHasContent || _suggestionsHasContent);
    let next = _activePanel;
    if (next === 'travel' && !_travelHasContent) next = null;
    if (next === 'suggestions' && !_suggestionsHasContent) next = null;
    if (!next) {
        if (_suggestionsHasContent) next = 'suggestions';
        else if (_travelHasContent) next = 'travel';
    }
    setActivePanel(next);
}

// Render the list of reachable destinations from the MC's current
// location. ``current`` is the {id, name, ...} payload from the world
// response (or the /travel response after a successful trip);
// ``destinations`` is a list of {id, name, type, terrain, minutes}.
// Hides the tab when there's nothing to show so the world-build
// phase doesn't surface an empty entry.
function renderTravelPanel(current, destinations) {
    if (!$('#travel-label').length) return;
    if (!current || !destinations || !destinations.length) {
        _travelHasContent = false;
        $('#travel-destinations').empty();
        refreshActionPanels();
        return;
    }
    _travelHasContent = true;
    $('#travel-current-name').text(current.name || '…');
    const $list = $('#travel-destinations').empty();
    destinations.forEach(function (d) {
        const $li = $('<li/>');
        const $btn = $('<button type="button" class="travel-destination-btn"/>');
        $btn.attr('data-destination-id', d.id);
        $btn.append($('<span class="travel-name"/>').text(d.name || `#${d.id}`));
        $btn.append($('<span class="travel-cost"/>').text(_formatTravelCost(d.minutes)));
        $li.append($btn);
        $list.append($li);
    });
    refreshActionPanels();
}

function _formatTravelCost(minutes) {
    const m = Math.max(0, parseInt(minutes || 0, 10));
    if (m < 60) return `${m}m`;
    const hours = Math.floor(m / 60);
    const rem = m % 60;
    return rem ? `${hours}h ${rem}m` : `${hours}h`;
}

// Send the player to ``destinationId``; on success the world clock,
// current location, destinations panel and suggestions all refresh
// against the new location.
function submitTravel(destinationId) {
    if (!_travelActiveSeedId || !destinationId) return;
    const seedId = _travelActiveSeedId;
    const $btn = $(`.travel-destination-btn[data-destination-id="${destinationId}"]`);
    $btn.prop('disabled', true);
    $.ajax({
        url: `/api/seed/${seedId}/travel`,
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ destination_id: destinationId }),
        success: function (response) {
            if (!response || !response.success) {
                $btn.prop('disabled', false);
                $('#game-error').text((response && response.message) || 'Travel failed.');
                return;
            }
            $('#game-error').text('');
            updateNarrativeList(response.entries || []);
            if (response.clock) renderClock(response.clock);
            renderTravelPanel(response.current_location, response.destinations || []);
            // Refresh suggestions against the new location so the next
            // batch fits where the player just arrived.
            if (!isScenarioActive()) {
                loadSuggestions(seedId);
            }
        },
        error: function (xhr) {
            $btn.prop('disabled', false);
            const msg = (xhr.responseJSON && xhr.responseJSON.message) || 'Travel failed.';
            $('#game-error').text(msg);
        },
    });
}

// Helper functions for authentication
function showLandingPage() {
    $('#landing-page').show();
    // Clear any pending collapse timer before hiding the header
    if (_headerCollapseTimer !== null) {
        clearTimeout(_headerCollapseTimer);
        _headerCollapseTimer = null;
    }
    _headerHoverStart = null;
    $('#app-header').removeClass('header-collapsed').hide();
    $('#main-menu').hide();
    $('#game-view').hide();
    $('#options-view').hide();
}

function showApp() {
    $('#landing-page').hide();
    // Ensure header starts fully expanded when app is shown
    $('#app-header').removeClass('header-collapsed').show();
    showMainMenu();
    // Sync the cached "key configured server-side" flag so the menu gating
    // and the API Keys view both reflect what the server actually has.
    refreshGrokKeyStatus();
    refreshElevenLabsKeyStatus();
    // /templates/* is gated by @login_required, so the Handlebars sources
    // can only be fetched after the user is signed in.
    loadFrontendTemplates();
}

// Helper functions for view management
function showMainMenu() {
    $('#main-menu').show();
    $('#game-view').hide();
    $('#options-view').hide();
    $('#back-to-menu-btn').hide();
    updateResumeButtonVisibility();
    updateGameMenuButtonsState();
}

// Check if there's an active game and show/hide resume button
function updateResumeButtonVisibility() {
    const currentSeedId = getLocalStorageItem('current-seed-id');
    if (currentSeedId) {
        $('#menu-resume-game-btn').show();
    } else {
        $('#menu-resume-game-btn').hide();
    }
}

function showView(viewName) {
    $('#main-menu').hide();
    $('#game-view').hide();
    $('#options-view').show();
    $('#back-to-menu-btn').show();

    // Hide all sub-views
    $('#new-game-view').hide();
    $('#load-game-view').hide();
    $('#api-keys-view').hide();
    $('#settings-view').hide();

    // Show the requested view
    $(`#${viewName}-view`).show();
}

function showGameView() {
    $('#main-menu').hide();
    $('#options-view').hide();
    $('#game-view').show();
    $('#back-to-menu-btn').show();
}

// Game view requires a Grok (xAI) API key bound to the server-side session.
// The browser only knows whether one is configured (via /api/grok-key); the
// actual key value lives in the server's encrypted in-memory store.
function hasValidGrokApiKey() {
    return hasGrokKey();
}

// Returns true when the gate is satisfied. Otherwise opens the API Key
// Required modal so the user can choose to navigate to the API Keys view.
function requireValidGrokApiKey() {
    if (hasValidGrokApiKey()) return true;
    $('#api-key-required-modal').fadeIn(200);
    return false;
}

// Disable the menu entries that ultimately lead to the game view when no
// Grok key is configured server-side. Settings and API Keys remain reachable
// so the user can supply a key.
function updateGameMenuButtonsState() {
    const enabled = hasValidGrokApiKey();
    $('#menu-resume-game-btn, #menu-new-game-btn, #menu-load-game-btn')
        .prop('disabled', !enabled)
        .attr('title', enabled ? '' : 'Add a valid xAI API key in the API Keys menu to enable.');
}

// Reflect the current key-configured state in the API Keys view: the
// "Configured" badge appears when the server confirms a stored key, and the
// Clear button is only enabled while one is present.
function updateApiKeysViewStatus() {
    const present = hasGrokKey();
    $('#grok-api-key-status')
        .text(present ? '✅ Key configured server-side' : '⚠️ No key configured')
        .toggleClass('grok-key-status-ok', present)
        .toggleClass('grok-key-status-missing', !present);
    $('#clear-api-key-btn').prop('disabled', !present);
}

$(document).ready(function () {
    // Check authentication status
    $.ajax({
        url: '/auth/check',
        type: 'GET',
        success: function(response) {
            if (response.authenticated) {
                // User is logged in
                $('#user-welcome').text(`Welcome, ${response.username}!`);
                showApp();
            } else {
                // User is not logged in
                showLandingPage();
            }
        },
        error: function() {
            // On error, show landing page
            showLandingPage();
        }
    });

    // Keep the floating "scroll to latest" arrow in sync with the user's
    // scroll position, and let it jump back to the newest entry on click.
    // Function declarations inside this ready() closure are hoisted, so the
    // helpers referenced here are resolvable even though they're defined
    // further down in the same scope.
    $('#game-output').on('scroll', updateScrollToBottomButtonVisibility);
    $('#scroll-to-bottom-btn').on('click', function () {
        scrollNarrativeToBottom();
        updateScrollToBottomButtonVisibility();
    });

    // Authentication handlers
    $('#show-signup').click(function(e) {
        e.preventDefault();
        $('#signin-form').hide();
        $('#signup-form').show();
        $('#signin-error').text('');
    });

    $('#show-signin').click(function(e) {
        e.preventDefault();
        $('#signup-form').hide();
        $('#signin-form').show();
        $('#signup-error').text('');
    });

    $('#signin-btn').click(function(e) {
        e.preventDefault();
        const username = $('#signin-username').val();
        const password = $('#signin-password').val();
        const remember = $('#signin-remember').is(':checked');

        if (!username || !password) {
            $('#signin-error').text('Please enter both username and password');
            return;
        }

        $.ajax({
            url: '/auth/login',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ username, password, remember }),
            success: function(response) {
                if (response.success) {
                    $('#user-welcome').text(`Welcome, ${response.username}!`);
                    showApp();
                } else {
                    $('#signin-error').text(response.message);
                }
            },
            error: function(xhr) {
                const response = xhr.responseJSON;
                $('#signin-error').text(response?.message || 'Login failed');
            }
        });
    });

    $('#signup-btn').click(function(e) {
        e.preventDefault();
        const username = $('#signup-username').val();
        const email = $('#signup-email').val();
        const password = $('#signup-password').val();

        if (!username || !email || !password) {
            $('#signup-error').text('Please fill in all fields');
            return;
        }

        $.ajax({
            url: '/auth/signup',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ username, email, password }),
            success: function(response) {
                if (response.success) {
                    $('#user-welcome').text(`Welcome, ${response.username}!`);
                    showApp();
                } else {
                    $('#signup-error').text(response.message);
                }
            },
            error: function(xhr) {
                const response = xhr.responseJSON;
                $('#signup-error').text(response?.message || 'Signup failed');
            }
        });
    });

    $('#logout-btn').click(function(e) {
        e.preventDefault();
        $.ajax({
            url: '/auth/logout',
            type: 'POST',
            success: function() {
                // Drop any leftover legacy cache and the active seed pointer
                // so the next user on the same browser doesn't inherit them.
                // The Grok key itself was already cleared server-side by the
                // /auth/logout handler; we just reset the in-page flag so the
                // menu gating reflects the new "no key" state.
                try {
                    localStorage.removeItem('key-grok-xai');
                    localStorage.removeItem('current-seed-id');
                } catch (_) { /* ignore storage errors */ }
                // Drop any mounted scenario panel so the next user on this
                // browser doesn't inherit the previous session's UI state.
                unmountScenario();
                setScenarioSeed(null);
                _travelActiveSeedId = null;
                renderTravelPanel(null, []);
                $('#grok-api-key-input').val('');
                $('#elevenlabs-api-key-input').val('');
                _grokKeyAvailable = false;
                _elevenlabsKeyAvailable = false;
                stopCurrentTtsPlayback();
                updateGameMenuButtonsState();
                updateApiKeysViewStatus();
                updateElevenLabsKeysViewStatus();
                updateTtsToggleAvailability();

                showLandingPage();
                // Clear forms
                $('#signin-username').val('');
                $('#signin-password').val('');
                $('#signup-username').val('');
                $('#signup-email').val('');
                $('#signup-password').val('');
                $('#signin-error').text('');
                $('#signup-error').text('');
            }
        });
    });

    // Allow Enter key to submit forms
    $('#signin-password').keypress(function(e) {
        if (e.which === 13) {
            $('#signin-btn').click();
        }
    });

    $('#signup-password').keypress(function(e) {
        if (e.which === 13) {
            $('#signup-btn').click();
        }
    });

    // One-shot migration: if a previous build cached the Grok key in
    // localStorage, push it to the new server-side store and wipe the
    // browser copy. The key never lives in the DOM or localStorage again.
    try {
        const legacyKey = (localStorage.getItem('key-grok-xai') || '').trim();
        if (legacyKey) {
            $.ajax({
                url: '/api/grok-key',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({ api_key: legacyKey })
            }).always(function () {
                try { localStorage.removeItem('key-grok-xai'); } catch (_) {}
                refreshGrokKeyStatus();
            });
        }
    } catch (_) { /* ignore storage errors */ }

    // Main menu button handlers
    $('#menu-resume-game-btn').click(function (e) {
        e.preventDefault();
        if (!requireValidGrokApiKey()) return;
        const currentSeedId = getLocalStorageItem('current-seed-id');
        if (currentSeedId) {
            showGameView();
            loadWorldData(currentSeedId);
        }
    });

    $('#menu-new-game-btn').click(function (e) {
        e.preventDefault();
        const currentSeedId = getLocalStorageItem('current-seed-id');

        // If there's an active game, show confirmation modal
        if (currentSeedId) {
            $('#new-game-confirmation-modal').fadeIn(200);
        } else {
            // No active game, proceed directly to new game view
            showView('new-game');
        }
    });

    $('#menu-load-game-btn').click(function (e) {
        e.preventDefault();
        showView('load-game');
        loadSavedGames();
    });

    $('#menu-api-keys-btn').click(function (e) {
        e.preventDefault();
        showView('api-keys');
    });

    $('#menu-settings-btn').click(function (e) {
        e.preventDefault();
        showView('settings');
        loadSettings();
    });

    // Back to menu button
    $('#back-to-menu-btn').click(function (e) {
        e.preventDefault();
        showMainMenu();
    });

    // Save: push the key to the server-side store and wipe the input field.
    // The key never lives in localStorage or the DOM longer than this call.
    $('#save-api-keys-btn').click(function (e) {
        e.preventDefault();
        const $input = $('#grok-api-key-input');
        const $msg = $('#api-keys-message');
        const apiKey = ($input.val() || '').trim();
        if (!apiKey) {
            $msg.text('Enter your xAI API key (starts with "xai-").').show();
            return;
        }
        $msg.text('Saving...').show();
        $.ajax({
            url: '/api/grok-key',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ api_key: apiKey })
        })
            .done(function () {
                $input.val('');
                $msg.text('Key saved server-side for this session.').show();
                refreshGrokKeyStatus();
            })
            .fail(function (xhr) {
                const r = xhr.responseJSON;
                $msg.text((r && r.message) || 'Failed to save API key.').show();
            });
    });

    // Clear: remove the key from the server-side store. No browser-side
    // state to wipe because the key was never cached locally.
    $('#clear-api-key-btn').click(function (e) {
        e.preventDefault();
        const $msg = $('#api-keys-message');
        $msg.text('Clearing...').show();
        $.ajax({ url: '/api/grok-key', type: 'DELETE' })
            .done(function () {
                $msg.text('Key cleared.').show();
                refreshGrokKeyStatus();
            })
            .fail(function () {
                $msg.text('Failed to clear API key.').show();
            });
    });

    // ElevenLabs key save / clear. Same transport pattern as the Grok key.
    $('#save-elevenlabs-key-btn').click(function (e) {
        e.preventDefault();
        const $input = $('#elevenlabs-api-key-input');
        const $msg = $('#elevenlabs-keys-message');
        const apiKey = ($input.val() || '').trim();
        if (!apiKey) {
            $msg.text('Enter your ElevenLabs API key.').show();
            return;
        }
        $msg.text('Saving...').show();
        $.ajax({
            url: '/api/elevenlabs-key',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ api_key: apiKey })
        })
            .done(function () {
                $input.val('');
                $msg.text('Key saved server-side for this session.').show();
                refreshElevenLabsKeyStatus();
            })
            .fail(function (xhr) {
                const r = xhr.responseJSON;
                $msg.text((r && r.message) || 'Failed to save ElevenLabs key.').show();
            });
    });

    $('#clear-elevenlabs-key-btn').click(function (e) {
        e.preventDefault();
        const $msg = $('#elevenlabs-keys-message');
        $msg.text('Clearing...').show();
        $.ajax({ url: '/api/elevenlabs-key', type: 'DELETE' })
            .done(function () {
                $msg.text('Key cleared.').show();
                refreshElevenLabsKeyStatus();
            })
            .fail(function () {
                $msg.text('Failed to clear ElevenLabs key.').show();
            });
    });

    // TTS toggle: pure UX preference, persisted in localStorage so the
    // setting survives reloads. Disabling it stops any in-flight playback.
    loadTtsEnabledFromStorage();
    $('#tts-toggle').on('change', function () {
        setTtsEnabled(this.checked);
    });

    // Per-entry play button. Delegated so the binding survives transcript
    // re-renders. Stops any in-flight playback before starting a new one.
    $('#narrativeList').on('click', '.narrative-tts-btn', function (e) {
        e.preventDefault();
        e.stopPropagation();
        const $btn = $(this);
        const entryId = parseInt($btn.attr('data-entry-id') || $btn.closest('[data-entry-id]').attr('data-entry-id'), 10);
        const seedId = getLocalStorageItem('current-seed-id');
        if (!seedId || !entryId) return;
        playTtsForEntry(seedId, entryId, $btn);
    });

    // Confirmation modal handlers
    $('#cancel-new-game-btn').click(function (e) {
        e.preventDefault();
        $('#new-game-confirmation-modal').fadeOut(200);
    });

    $('#confirm-new-game-btn').click(function (e) {
        e.preventDefault();
        $('#new-game-confirmation-modal').fadeOut(200);
        showView('new-game');
    });

    // Close modal when clicking outside of it
    $('#new-game-confirmation-modal').click(function (e) {
        if (e.target === this) {
            $(this).fadeOut(200);
        }
    });

    // API Key Required modal handlers
    $('#api-key-required-cancel-btn').click(function (e) {
        e.preventDefault();
        $('#api-key-required-modal').fadeOut(200);
    });

    $('#api-key-required-goto-btn').click(function (e) {
        e.preventDefault();
        $('#api-key-required-modal').fadeOut(200, function () {
            showView('api-keys');
            setTimeout(() => $('#grok-api-key-input').trigger('focus'), 0);
        });
    });

    $('#api-key-required-modal').click(function (e) {
        if (e.target === this) {
            $(this).fadeOut(200);
        }
    });

    // Handlebars template fetches moved into loadFrontendTemplates(),
    // which is called from showApp() once the user is authenticated.
    // /templates/* is gated by @login_required, so issuing the requests
    // here (before sign-in) was returning 401 and leaving the templates
    // empty for the rest of the page lifecycle.

    // Wire the structured-scenario controller. Scenario-emitted transcript
    // entries flow through the same updateNarrativeList path as turn-emitted
    // ones so per-kind styling, pagination and TTS autoplay all work
    // unchanged. ``getCsrfToken`` is hoisted via the module-level decl.
    initScenarioController({
        onEntries: updateNarrativeList,
        csrfToken: getCsrfToken,
    });

    // Randomize buttons in the basic-setup form. Pure client-side: a small
    // built-in pool keeps the form responsive without round-tripping to the
    // server. Triggers 'change' so any listeners (e.g. validation) re-run.
    const RANDOM_NAME_POOL = [
        'Aelar', 'Brennan', 'Cassia', 'Doran', 'Elowen', 'Fenris', 'Gwyneth',
        'Hadrian', 'Isolde', 'Jorvik', 'Kaelen', 'Lyra', 'Mordecai', 'Nyx',
        'Orin', 'Perrin', 'Quill', 'Rhiannon', 'Soren', 'Tamsin', 'Ulric',
        'Vesper', 'Wrenna', 'Xander', 'Ysolde', 'Zephyr', 'Anya', 'Bram',
        'Calla', 'Dax', 'Esme', 'Finn', 'Gideon', 'Hazel', 'Ivor', 'Juno'
    ];
    const RANDOM_GENDERS = ['male', 'female', 'other'];

    $('#new-game-view').on('click', '.randomize-btn', function () {
        const $btn = $(this);
        const $target = $($btn.attr('data-target'));
        if (!$target.length) return;

        switch ($btn.attr('data-randomize')) {
            case 'name':
                $target.val(RANDOM_NAME_POOL[Math.floor(Math.random() * RANDOM_NAME_POOL.length)]);
                break;
            case 'age':
                $target.val(Math.floor(Math.random() * 48) + 18); // 18-65
                break;
            case 'gender':
                $target.val(RANDOM_GENDERS[Math.floor(Math.random() * RANDOM_GENDERS.length)]);
                break;
        }
        $target.trigger('change');
    });

    // Handle the Create Seed button click
    $('#create-seed-btn').on('click', function () {
        if (!requireValidGrokApiKey()) return;
        let seedData;

        // Check if we have a stereotype-generated build
        if (stereotypeGeneratedBuild) {
            // Use the stereotype-generated build
            seedData = stereotypeGeneratedBuild;
        } else {
            // Collect data from manual input fields
            const characterName = $('#new-game-character-name-input').val();
            const characterAge = $('#new-game-character-age-input').val();
            const characterGender = $('#new-game-character-gender-input').val();
            const storyInspiration = $('#new-game-inspiration-input').val();

            seedData = {
                character_name: characterName,
                character_age: characterAge,
                character_gender: characterGender,
                story_inspiration: storyInspiration
            };
        }

        $.ajax({
            url: '/create_seed',
            type: 'POST',
            contentType: 'application/json',
            success: function (response) {
                setLocalStorageItem('current-seed-id', response.seed_id);

                // Switch to game view
                showGameView();

                // Initialize world building
                initializeWorldBuilding(response.seed_id, JSON.stringify(seedData));
            },
            error: function (error) {
                console.error('Error creating seed:', error);
            }
        });
    });

    async function initializeWorldBuilding(seedId, seedData) {
        // Reset the paginated transcript so progress lines for the new seed
        // don't share a buffer with whatever was last loaded.
        renderNarrativeTranscript([]);
        // Clear the side panels too; otherwise the previous seed's locations,
        // events, characters, quests, items and profile linger until the SSE
        // stream finishes and loadWorldData() repopulates them.
        resetGameViewPanels();
        updateNarrativeList("Starting world building process...");

        try {
            await executeWorldBuildingWithSSE(seedId, seedData);
        } catch (error) {
            console.error("Error during world building:", error);
            updateNarrativeList("An error occurred during world building: " + error.message);
        }
    }

    // Empty every game-view side panel so stale data from a previous seed
    // doesn't bleed through while a new world is being built. Called when a
    // new adventure starts, before the SSE stream begins emitting progress.
    function resetGameViewPanels() {
        Object.values(ENTITY_RENDER_MAP).forEach(cfg => {
            $(`#${cfg.accordion}`).empty();
        });
        $('#characterProfile').html(
            '<div class="character-profile-empty">Building world…</div>'
        );
        $('#optionsList').empty();
        _suggestionsHasContent = false;
        _travelHasContent = false;
        _activePanel = null;
        refreshActionPanels();
        // A fresh seed can't have an in-flight scenario yet; tear down any
        // panel inherited from the previous game so the free-text input is
        // visible while the new world is being built.
        unmountScenario();
        // Hide the previous seed's geography while the new world is being
        // built; loadWorldData will re-render the map once /api/world/.../map
        // returns for the freshly-seeded world.
        clearWorldMap();
    }

    async function executeWorldBuildingWithSSE(seedId, seedData) {
        return new Promise((resolve, reject) => {
            // POST to start the SSE stream. The Grok key is no longer
            // attached here; the server pulls it from the session-scoped
            // store. fetch() still needs the CSRF header set explicitly
            // because it bypasses jQuery's ajaxSetup.
            fetch('/initialize_world_building_stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': getCsrfToken()
                },
                body: JSON.stringify({
                    seed_id: seedId,
                    seed_data: seedData
                })
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error('Failed to start world building');
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                function processStream() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            console.log('Stream complete');
                            return;
                        }

                        // Decode the chunk and add to buffer
                        buffer += decoder.decode(value, { stream: true });

                        // Process complete messages (separated by \n\n)
                        const messages = buffer.split('\n\n');
                        buffer = messages.pop(); // Keep incomplete message in buffer

                        messages.forEach(message => {
                            if (message.trim() === '' || message.startsWith(':')) {
                                // Skip empty messages and keepalive comments
                                return;
                            }

                            // Parse SSE message
                            const lines = message.split('\n');
                            let data = '';

                            lines.forEach(line => {
                                if (line.startsWith('data: ')) {
                                    data = line.substring(6);
                                }
                            });

                            if (data) {
                                try {
                                    const event = JSON.parse(data);

                                    if (event.type === 'progress') {
                                        updateNarrativeList({ text: event.message, kind: 'world_building' });
                                    } else if (event.type === 'complete') {
                                        console.log('World building results:', event.results);
                                        updateNarrativeList({ text: "✓ World building completed successfully!", kind: 'system' });
                                        // loadWorldData also fetches fresh suggestions for the new world.
                                        loadWorldData(seedId);
                                        resolve(event.results);
                                    } else if (event.type === 'error') {
                                        updateNarrativeList({ text: "✗ Error: " + event.message, kind: 'system' });
                                        reject(new Error(event.message));
                                    }
                                } catch (e) {
                                    console.error('Failed to parse SSE message:', e, data);
                                }
                            }
                        });

                        // Continue reading
                        processStream();
                    }).catch(error => {
                        console.error('Stream reading error:', error);
                        reject(error);
                    });
                }

                processStream();
            })
            .catch(error => {
                console.error('Failed to start SSE stream:', error);
                reject(error);
            });
        });
    }

    // Keep the old function as fallback. The Grok key rides on the
    // X-Grok-API-Key header injected by $.ajaxSetup; it is never echoed
    // into the request body.
    async function executeWorldBuilding(seedId, seedData) {
        return new Promise((resolve, reject) => {
            $.ajax({
                url: '/initialize_world_building',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    seed_id: seedId,
                    seed_data: seedData
                }),
                success: function (response) {
                    resolve(response);
                },
                error: function (error) {
                    reject(error);
                }
            });
        });
    }

    function loadWorldData(seedId) {
        if (!seedId) return;
        // Bind the scenario controller to this seed before any payload
        // arrives so a mid-flight refresh remounts the right panel. The
        // travel panel is bound the same way so its destination buttons
        // know which seed to POST against.
        setScenarioSeed(seedId);
        _travelActiveSeedId = seedId;
        $.ajax({
            url: `/api/world/${seedId}`,
            type: 'GET',
            success: function (world) {
                // Replay the persisted narrative transcript into the panel so
                // refreshing or resuming a seed restores the same scrolling
                // story the user saw originally, with per-kind styling intact.
                // Long histories are paginated: only the most recent page is
                // mounted, with a "Show More" button revealing older entries.
                renderNarrativeTranscript(world.transcript || []);
                Object.entries(ENTITY_RENDER_MAP).forEach(([key, cfg]) => {
                    const items = world[key] || [];
                    const filtered = cfg.filter ? items.filter(cfg.filter) : items;
                    renderEntityList(filtered, cfg.template, cfg.accordion);
                });
                renderCharacterProfile(
                    world.main_character,
                    world.stats || [],
                    world.skills || [],
                    world.statuses || [],
                );
                renderClock(world.clock);
                renderTravelPanel(world.current_location, world.destinations || []);
                // Mount or tear down the structured scenario panel from the
                // payload. While a scenario is active the free-text input and
                // suggestion list are hidden, so skip the suggestions call to
                // avoid showing buttons the player can't act on.
                mountScenario(world.scenario || null);
                if (!isScenarioActive()) {
                    loadSuggestions(seedId);
                }
                // Pull the geography on its own request: the map endpoint
                // returns the unfiltered settlement / sub-location set plus
                // the road connections, which /api/world doesn't carry.
                loadWorldMap(seedId);
            },
            error: function (xhr) {
                console.error('Failed to load world data:', xhr);
            }
        });
    }

    // Fetch the seed's geography and hand it to the Leaflet widget. Failures
    // are silent: the map is supplemental and must not block the rest of the
    // game view from rendering when the endpoint hiccups.
    function loadWorldMap(seedId) {
        if (!seedId) return;
        $.ajax({
            url: `/api/world/${seedId}/map`,
            type: 'GET',
            success: function (payload) {
                renderWorldMap(payload);
            },
            error: function (xhr) {
                console.error('Failed to load world map:', xhr);
                clearWorldMap();
            }
        });
    }

    // Fetch a fresh batch of action suggestions for the current situation
    // and render them as clickable buttons above the input box. Failures are
    // silent: suggestions are a UX nicety and must not block the player from
    // typing their own action.
    function loadSuggestions(seedId) {
        if (!seedId) return;
        $('#optionsList').empty();
        $('#suggestions-label-text').text('Loading suggestions…');
        _suggestionsHasContent = true;
        _activePanel = 'suggestions';
        refreshActionPanels();
        $.ajax({
            url: `/api/seed/${seedId}/suggestions`,
            type: 'GET',
            success: function (response) {
                renderSuggestions(response.suggestions || []);
            },
            error: function (xhr) {
                console.error('Failed to load suggestions:', xhr);
                renderSuggestions([]);
            }
        });
    }

    // Render the suggestion strings as buttons. Clicking a suggestion
    // submits it directly as the player's next action; the textarea remains
    // available for free-form input via the Submit button or Ctrl+Enter.
    function renderSuggestions(suggestions) {
        const $list = $('#optionsList');
        $list.empty();
        if (!suggestions || suggestions.length === 0) {
            _suggestionsHasContent = false;
            refreshActionPanels();
            return;
        }
        $('#suggestions-label-text').text('Suggested actions');
        _suggestionsHasContent = true;
        suggestions.forEach(text => {
            const $li = $('<li>').addClass('nav-item');
            const $btn = $('<button>')
                .attr('type', 'button')
                .addClass('btn btn-link suggestion-btn')
                .text(text)
                .on('click', function () {
                    submitGameInput(text);
                });
            $li.append($btn);
            $list.append($li);
        });
        refreshActionPanels();
    }

    // Submit the player's action to the backend, optimistically reflect it
    // in the transcript panel, then append the narrator's reply and refresh
    // suggestions. Disables the input controls for the duration so the
    // player can't double-submit while a turn is in flight.
    function submitGameInput(actionText) {
        const seedId = getLocalStorageItem('current-seed-id');
        const action = (actionText != null ? actionText : $('#game-input').val() || '').trim();
        if (!seedId || !action) return;
        if (!requireValidGrokApiKey()) return;
        // Free-form turns are gated while a scenario panel owns the input;
        // the player must drive the scenario through its own action bar.
        if (isScenarioActive()) {
            $('#game-error').text(
                'Resolve the active scenario before taking another free action.'
            );
            return;
        }

        const $input = $('#game-input');
        const $submit = $('#submit-game-input-btn');
        const $error = $('#game-error');

        $error.text('');
        $input.prop('disabled', true);
        $submit.prop('disabled', true).text('Thinking…');

        // Optimistic echo so the player sees their action immediately.
        updateNarrativeList({ text: action, kind: 'player_input', speaker: 'You' });

        // Clear the suggestions while the turn is in flight; the response
        // brings a fresh batch grounded in the new transcript state.
        $('#optionsList').empty();
        $('#suggestions-label-text').text('Loading suggestions…');
        _suggestionsHasContent = true;
        _activePanel = 'suggestions';
        refreshActionPanels();

        $.ajax({
            url: `/api/seed/${seedId}/turn`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ action: action }),
            success: function (response) {
                // The turn returns an ordered list of new transcript entries
                // (narration first, then per-character dialogue lines), each
                // attributed to its own speaker so the right voice plays.
                // ``narration`` is kept around as a legacy fallback for older
                // backends that don't ship the structured ``entries`` array.
                if (Array.isArray(response.entries) && response.entries.length) {
                    response.entries.forEach(entry => updateNarrativeList(entry));
                } else if (response.narration) {
                    updateNarrativeList({
                        id: response.narration_id || null,
                        text: response.narration,
                        kind: 'narration',
                        speaker: 'Narrator',
                    });
                }
                // Pull a fresh world payload whenever the LLM introduced new
                // characters this turn so the NPC accordion reflects them
                // (and TTS for their voice id is wired up on next playback).
                if (Array.isArray(response.new_characters) && response.new_characters.length) {
                    loadWorldData(seedId);
                }
                // The narrator may have handed off to a structured scenario
                // this turn. Mounting hides the free-text input + suggestions
                // so the player drives the scenario from its own panel.
                if (response.scenario) {
                    mountScenario(response.scenario);
                }
                if (response.clock) {
                    renderClock(response.clock);
                }
                if (!isScenarioActive()) {
                    renderSuggestions(response.suggestions || []);
                }
                $input.val('');
            },
            error: function (xhr) {
                const msg = xhr.responseJSON?.message || 'Failed to advance the story.';
                $error.text(msg);
                // A 409 from /turn carries the active scenario so the panel
                // can resync if local state had drifted out of sync.
                if (xhr.responseJSON?.scenario) {
                    mountScenario(xhr.responseJSON.scenario);
                } else {
                    renderSuggestions([]);
                }
            },
            complete: function () {
                $input.prop('disabled', false);
                $submit.prop('disabled', false).text('Submit');
                $input.trigger('focus');
            }
        });
    }

    // Submit button + Ctrl/Cmd+Enter shortcut for the free-form input.
    $('#submit-game-input-btn').on('click', function (e) {
        e.preventDefault();
        submitGameInput();
    });
    $('#game-input').on('keydown', function (e) {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submitGameInput();
        }
    });

    // Travel destination buttons are delegated against #travel-destinations
    // so they survive every renderTravelPanel() rebuild.
    $('#travel-destinations').on('click', '.travel-destination-btn', function (e) {
        e.preventDefault();
        const id = parseInt($(this).attr('data-destination-id'), 10);
        if (id) submitTravel(id);
    });
    // Clicking a tab activates its list (and collapses the other);
    // clicking the active tab again collapses both. Only one panel is
    // ever visible below the tabs.
    $('#travel-label').on('click', function () {
        setActivePanel(_activePanel === 'travel' ? null : 'travel');
    });
    $('#suggestions-label').on('click', function () {
        setActivePanel(_activePanel === 'suggestions' ? null : 'suggestions');
    });

    // Render the main character's identity, vitals, attributes, skills and
    // statuses into #characterProfile. All four lists come from /api/world
    // (stats, skills, statuses are id/name/description tuples) so the same
    // payload feeds the headline, the attribute grid and the chip lists.
    function renderCharacterProfile(mainCharacter, stats, skills, statuses) {
        const $container = $('#characterProfile');
        if (!$container.length) return;

        if (!mainCharacter) {
            $container.html('<div class="character-profile-empty">No character loaded.</div>');
            return;
        }

        const statByKey = {};
        (stats || []).forEach(s => { statByKey[s.id] = s.description; });

        const escape = escapeHtml;

        const name = escape(mainCharacter.name || 'Unnamed Hero');
        const subtitleParts = [];
        if (mainCharacter.race) subtitleParts.push(escape(mainCharacter.race));
        if (mainCharacter.level != null) subtitleParts.push(`Level ${escape(mainCharacter.level)}`);
        const subtitle = subtitleParts.join(' · ');

        const cur = Number(mainCharacter.current_health);
        const max = Number(mainCharacter.max_health);
        const hasHealth = Number.isFinite(cur) && Number.isFinite(max) && max > 0;
        const healthPct = hasHealth ? Math.max(0, Math.min(100, (cur / max) * 100)) : 0;

        let html = '';
        html += `<div class="profile-header">`;
        html += `  <div class="profile-avatar">👤</div>`;
        html += `  <div class="profile-identity">`;
        html += `    <div class="profile-name">${name}</div>`;
        if (subtitle) html += `<div class="profile-subtitle">${subtitle}</div>`;
        html += `  </div>`;
        html += `</div>`;

        html += `<div class="profile-vitals">`;
        if (hasHealth) {
            html += `<div class="profile-vital">`;
            html += `  <div class="profile-vital-label"><span>❤ Health</span><span>${escape(cur)} / ${escape(max)}</span></div>`;
            html += `  <div class="profile-bar"><div class="profile-bar-fill" style="width:${healthPct}%"></div></div>`;
            html += `</div>`;
        }
        if (mainCharacter.current_currency != null) {
            html += `<div class="profile-vital-row"><span>💰 Currency</span><span>${escape(mainCharacter.current_currency)}</span></div>`;
        }
        if (mainCharacter.exp_points != null) {
            html += `<div class="profile-vital-row"><span>⭐ Experience</span><span>${escape(mainCharacter.exp_points)}</span></div>`;
        }
        html += `</div>`;

        const attributeCells = Object.entries(PROFILE_STAT_META)
            .filter(([key]) => statByKey[key] != null)
            .map(([key, meta]) => {
                return `<div class="profile-stat" title="${escape(key)}">`
                    + `<div class="profile-stat-icon">${meta.icon}</div>`
                    + `<div class="profile-stat-label">${meta.label}</div>`
                    + `<div class="profile-stat-value">${escape(statByKey[key])}</div>`
                    + `</div>`;
            })
            .join('');

        if (attributeCells) {
            html += `<div class="profile-section-title">Attributes</div>`;
            html += `<div class="profile-stats-grid">${attributeCells}</div>`;
        }

        html += renderProfileEntryList('⚡ Skills', skills, 'profile-skill');
        html += renderProfileEntryList('💫 Statuses', statuses, 'profile-status');

        $container.html(html);
    }

    // Render a labelled list of {name, description} entries used by the
    // Skills and Statuses sections of the character profile. Returns an
    // empty string when there are no entries so the section title doesn't
    // appear above an empty list.
    function renderProfileEntryList(title, entries, itemClass) {
        const list = entries || [];
        const escape = escapeHtml;
        let html = `<div class="profile-section-title">${escape(title)}</div>`;
        if (list.length === 0) {
            html += `<div class="profile-entry-empty">None</div>`;
            return html;
        }
        html += `<ul class="profile-entry-list">`;
        list.forEach(entry => {
            const name = escape(entry.name || 'Unknown');
            const description = entry.description ? escape(entry.description) : '';
            html += `<li class="profile-entry ${itemClass}">`
                + `<div class="profile-entry-name">${name}</div>`
                + (description ? `<div class="profile-entry-desc">${description}</div>` : '')
                + `</li>`;
        });
        html += `</ul>`;
        return html;
    }

    function renderEntityList(items, templateName, accordionId) {
        const $container = $(`#${accordionId}`);
        $container.empty();
        const template = entityTemplates[templateName];
        if (!template) {
            console.warn(`Template '${templateName}' not loaded yet; skipping render of #${accordionId}`);
            return;
        }
        items.forEach(item => {
            $container.append(template(item));
        });
    }

    // Saved-games view paginates client-side: /api/seeds returns the full
    // list (already sorted newest-first by the server), and we slice it into
    // pages of SAVED_GAMES_PAGE_SIZE rows so long histories stay scannable.
    const SAVED_GAMES_PAGE_SIZE = 10;
    const savedGamesState = { seeds: [], page: 1 };

    function loadSavedGames() {
        const $view = $('#load-game-view');
        $view.html(
            '<h3 class="tab-heading">💾 Load Game</h3>'
            + '<div id="saved-games-list">Loading...</div>'
            + '<div id="saved-games-pagination" class="saved-games-pagination"></div>'
        );
        $.ajax({
            url: '/api/seeds',
            type: 'GET',
            success: function (response) {
                savedGamesState.seeds = response.seeds || [];
                savedGamesState.page = 1;
                renderSavedGamesPage();
            },
            error: function (xhr) {
                $('#saved-games-list').html('<p style="color:red;">Failed to load saved games.</p>');
                $('#saved-games-pagination').empty();
                console.error('Failed to load saved games:', xhr);
            }
        });
    }

    function renderSavedGamesPage() {
        const $list = $('#saved-games-list');
        const $pagination = $('#saved-games-pagination');
        $list.empty();
        $pagination.empty();

        const seeds = savedGamesState.seeds;
        if (seeds.length === 0) {
            $list.html('<p>No saved games found.</p>');
            return;
        }

        const totalPages = Math.max(1, Math.ceil(seeds.length / SAVED_GAMES_PAGE_SIZE));
        // Clamp the page number in case the underlying list shrank between
        // renders (e.g. a future delete action).
        if (savedGamesState.page > totalPages) savedGamesState.page = totalPages;
        if (savedGamesState.page < 1) savedGamesState.page = 1;

        const start = (savedGamesState.page - 1) * SAVED_GAMES_PAGE_SIZE;
        const pageSeeds = seeds.slice(start, start + SAVED_GAMES_PAGE_SIZE);

        pageSeeds.forEach(s => {
            const created = s.created_at ? new Date(s.created_at).toLocaleString() : 'Unknown';
            const charName = s.main_character_name || '(no character)';
            const $row = $('<div>').addClass('saved-game-row').css({
                'display': 'flex', 'justify-content': 'space-between',
                'align-items': 'center', 'padding': '0.5rem 0',
                'border-bottom': '1px solid rgba(255,255,255,0.1)'
            });
            // Build the row with .text() on every user-derived field so a
            // crafted main_character_name (or any future field) cannot inject
            // markup into the saved-games list.
            const $info = $('<div>');
            $info.append($('<strong>').text(charName));
            $info.append('<br>');
            $info.append(
                $('<small>').text(
                    `Seed #${s.seed_id} · Turn ${s.current_turn} · Created ${created}`
                )
            );
            $row.append($info);
            const $btn = $('<button>').addClass('btn btn-primary').text('Load').click(function () {
                if (!requireValidGrokApiKey()) return;
                setLocalStorageItem('current-seed-id', s.seed_id);
                showGameView();
                loadWorldData(s.seed_id);
            });
            $row.append($btn);
            $list.append($row);
        });

        renderSavedGamesPagination(totalPages);
    }

    // Render Prev / Next controls plus a "Page X of Y" indicator. Hidden
    // outright when there's only one page so the single-save case stays
    // visually clean.
    function renderSavedGamesPagination(totalPages) {
        const $pagination = $('#saved-games-pagination');
        $pagination.empty();
        if (totalPages <= 1) return;

        const page = savedGamesState.page;
        const $prev = $('<button>')
            .attr('type', 'button')
            .addClass('btn btn-secondary saved-games-page-btn')
            .text('← Prev')
            .prop('disabled', page <= 1)
            .on('click', function () {
                if (savedGamesState.page > 1) {
                    savedGamesState.page -= 1;
                    renderSavedGamesPage();
                }
            });
        const $next = $('<button>')
            .attr('type', 'button')
            .addClass('btn btn-secondary saved-games-page-btn')
            .text('Next →')
            .prop('disabled', page >= totalPages)
            .on('click', function () {
                if (savedGamesState.page < totalPages) {
                    savedGamesState.page += 1;
                    renderSavedGamesPage();
                }
            });
        const $indicator = $('<span>')
            .addClass('saved-games-page-indicator')
            .text(`Page ${page} of ${totalPages}`);

        $pagination.append($prev).append($indicator).append($next);
    }

    // Normalise an entry-or-string argument into the { text, kind, speaker }
    // shape the narrative template expects. Plain strings come from legacy
    // call sites that emit quick system messages.
    function normaliseNarrativeEntry(entryOrText) {
        const entry = (typeof entryOrText === 'string')
            ? { text: entryOrText, kind: 'system' }
            : entryOrText;
        return {
            // Persisted entries carry an id; ephemeral / streamed messages
            // (world-building progress, optimistic player echoes) don't, and
            // the template suppresses the TTS button when id is missing.
            id: entry.id != null ? entry.id : null,
            text: entry.text,
            kind: entry.kind || 'system',
            speaker: entry.speaker || null,
            // Structured payload (e.g. dice roll breakdown) preserved so
            // downstream consumers (dice overlay flash, future scenario
            // hooks) can read it without re-parsing the text line.
            meta: entry.meta || null,
        };
    }

    // Tolerance (in pixels) for treating the transcript as "scrolled to the
    // bottom". Browser sub-pixel rounding plus the trailing margin on the
    // last entry can leave a few pixels of slack even when the user is fully
    // pinned to the latest content; this threshold keeps the auto-follow
    // behaviour from disengaging on those near-misses.
    const NARRATIVE_BOTTOM_THRESHOLD_PX = 24;

    function getNarrativeScroller() {
        return document.getElementById('game-output');
    }

    function isNarrativeAtBottom() {
        const el = getNarrativeScroller();
        if (!el) return true;
        return (el.scrollHeight - el.scrollTop - el.clientHeight)
            <= NARRATIVE_BOTTOM_THRESHOLD_PX;
    }

    function scrollNarrativeToBottom() {
        const el = getNarrativeScroller();
        if (!el) return;
        el.scrollTop = el.scrollHeight;
    }

    // Show the floating jump-to-latest arrow only when the user has scrolled
    // far enough up that auto-follow has disengaged and new content would
    // otherwise be appended off-screen.
    function updateScrollToBottomButtonVisibility() {
        const $btn = $('#scroll-to-bottom-btn');
        if (!$btn.length) return;
        if (isNarrativeAtBottom()) {
            $btn.attr('hidden', 'hidden');
        } else {
            $btn.removeAttr('hidden');
        }
    }

    // Append a narrative entry to #narrativeList. Accepts either a plain
    // string (legacy / quick system messages) or a transcript entry object
    // with at least { text, kind, speaker } so per-kind styling and speaker
    // attribution survive both live-streamed messages and replayed history.
    // The entry is also pushed onto narrativeEntries so the pagination state
    // stays in sync with what the user has already seen.
    function updateNarrativeList(entryOrText) {
        if (!narrativeTemplate) {
            console.error('Narrative item template not loaded');
            return;
        }
        const context = normaliseNarrativeEntry(entryOrText);
        // Capture the follow state before mutation: if the user was already
        // pinned to the latest entry, keep them pinned by scrolling after the
        // append; if they had scrolled up to read history, leave their
        // viewport alone and let the floating arrow advertise the new line.
        const stickToBottom = isNarrativeAtBottom();
        narrativeEntries.push(context);
        narrativeVisibleCount += 1;
        $('#narrativeList').append(narrativeTemplate(context));
        if (stickToBottom) {
            scrollNarrativeToBottom();
        }
        updateScrollToBottomButtonVisibility();
        // Phase 6: flash the d20 overlay for live dice entries so the
        // player feels the roll happen instead of just reading it.
        // Skipped for transcript replay (renderNarrativeTranscript
        // path) so resuming a session doesn't trigger a stale flash.
        if (context.kind === 'dice' && context.meta) {
            flashDiceOverlay(context.meta);
        }
        // Autoplay newly-appended narration / dialogue when the toggle is
        // on and a key is configured. Skipped for system / world-building /
        // player-input lines so the user doesn't hear their own commands or
        // progress chatter spoken back. The queue path keeps multi-entry
        // turns playing in chronological order instead of overlapping.
        if (context.id != null && getTtsEnabled() && hasElevenLabsKey()) {
            const speakable = context.kind === 'narration' || context.kind === 'dialogue';
            if (speakable) {
                const seedId = getLocalStorageItem('current-seed-id');
                if (seedId) enqueueTtsForEntry(seedId, context.id);
            }
        }
    }

    // Replace the buffer with a full transcript and render only the most
    // recent NARRATIVE_PAGE_SIZE entries. Older entries stay in memory and
    // become visible one page at a time via the "Show More" button.
    function renderNarrativeTranscript(entries) {
        if (!narrativeTemplate) {
            console.error('Narrative item template not loaded');
            return;
        }
        narrativeEntries = (entries || []).map(normaliseNarrativeEntry);
        narrativeVisibleCount = Math.min(narrativeEntries.length, NARRATIVE_PAGE_SIZE);
        const $list = $('#narrativeList');
        $list.empty();
        const start = narrativeEntries.length - narrativeVisibleCount;
        let html = '';
        for (let i = start; i < narrativeEntries.length; i++) {
            html += narrativeTemplate(narrativeEntries[i]);
        }
        $list.append(html);
        refreshNarrativeShowMoreButton();
        // A full re-render always represents "the user just opened / resumed
        // this transcript", so jump straight to the latest content. The
        // scroller may still be hidden (game view not yet shown) when this
        // runs, in which case scrollHeight is 0 and the assignment is a
        // harmless no-op that the next visible append will correct.
        scrollNarrativeToBottom();
        updateScrollToBottomButtonVisibility();
    }

    // Toggle the "Show More" header at the top of #narrativeList based on
    // whether older entries remain hidden. The button label advertises how
    // many entries the next click will reveal so the user can gauge cost.
    function refreshNarrativeShowMoreButton() {
        const $list = $('#narrativeList');
        $list.find('.narrative-show-more').remove();
        const hidden = narrativeEntries.length - narrativeVisibleCount;
        if (hidden <= 0) return;
        const next = Math.min(NARRATIVE_PAGE_SIZE, hidden);
        const $li = $('<li>').addClass('nav-item narrative-show-more');
        const $btn = $('<button>')
            .attr('type', 'button')
            .addClass('btn btn-link narrative-show-more-btn')
            .text(`Show ${next} more (${hidden} hidden)`)
            .on('click', showMoreNarrativeEntries);
        $li.append($btn);
        $list.prepend($li);
    }

    // Reveal the next older page of transcript entries. Captures the scroll
    // container's geometry before mutation and restores the user's viewport
    // afterwards so prepended history doesn't shove the current text out of
    // sight.
    function showMoreNarrativeEntries() {
        if (narrativeVisibleCount >= narrativeEntries.length) return;
        const additional = Math.min(
            NARRATIVE_PAGE_SIZE,
            narrativeEntries.length - narrativeVisibleCount,
        );
        const newEnd = narrativeEntries.length - narrativeVisibleCount;
        const newStart = newEnd - additional;

        const $list = $('#narrativeList');
        const $scroller = $list.closest('#game-output');
        const scroller = $scroller.get(0);
        const prevHeight = scroller ? scroller.scrollHeight : 0;
        const prevTop = scroller ? scroller.scrollTop : 0;

        let html = '';
        for (let i = newStart; i < newEnd; i++) {
            html += narrativeTemplate(narrativeEntries[i]);
        }
        $list.find('.narrative-show-more').remove();
        $list.prepend(html);
        narrativeVisibleCount += additional;
        refreshNarrativeShowMoreButton();

        if (scroller) {
            scroller.scrollTop = prevTop + (scroller.scrollHeight - prevHeight);
        }
    }

    // === Header auto-collapse on hover leave ===
    // On mouseenter: cancel any pending collapse and restore the header.
    // On mouseleave: wait an exponentially growing delay (based on dwell time,
    //   capped at 9 s input) then collapse the header by shrinking the real
    //   typography and padding so the row keeps full width and the title
    //   reflows onto a single line.
    //
    // Delay formula: e^(dwellSeconds / 3) * 1000 ms
    //   dwell 0 s  →  ~1 s delay
    //   dwell 3 s  →  ~2.7 s delay
    //   dwell 6 s  →  ~7.4 s delay
    //   dwell 9 s  →  ~20 s delay  (cap)
    $('#app-header')
        .on('mouseenter', function () {
            if (_headerCollapseTimer !== null) {
                clearTimeout(_headerCollapseTimer);
                _headerCollapseTimer = null;
            }
            $(this).removeClass('header-collapsed');
            _headerHoverStart = Date.now();
        })
        .on('mouseleave', function () {
            const dwellMs = _headerHoverStart !== null ? (Date.now() - _headerHoverStart) : 0;
            const dwellSeconds = Math.min(dwellMs / 1000, 9);
            const delayMs = Math.pow(Math.E, dwellSeconds / 3) * 1000;

            const $header = $(this);
            _headerCollapseTimer = setTimeout(function () {
                $header.addClass('header-collapsed');
                _headerCollapseTimer = null;
            }, delayMs);

            _headerHoverStart = null;
        });

    // ===== ADVENTURE TAB NAVIGATION =====
    let uploadedImageData = null;
    let stereotypeGeneratedBuild = null;

    // Tab switching functionality
    $('.adventure-tab').on('click', function() {
        const tabName = $(this).data('tab');

        // Update active tab button
        $('.adventure-tab').removeClass('active');
        $(this).addClass('active');

        // Update active tab content
        $('.tab-content').removeClass('active');
        $(`#${tabName}-tab`).addClass('active');
    });

    // ===== IMAGE UPLOAD FUNCTIONALITY =====
    // Click to upload - use event delegation to ensure it works even if elements load later
    $(document).on('click', '#upload-area', function(e) {
        // Don't trigger if clicking on the remove button or the file input itself
        if ($(e.target).is('#remove-image-btn') ||
            $(e.target).closest('#remove-image-btn').length ||
            $(e.target).is('#stereotype-image-input')) {
            return;
        }

        e.preventDefault();
        e.stopPropagation();

        // Trigger the file input
        const fileInput = document.getElementById('stereotype-image-input');
        if (fileInput) {
            fileInput.click();
        }
    });

    // File input change
    $(document).on('change', '#stereotype-image-input', function(e) {
        const file = e.target.files[0];
        if (file) {
            handleImageFile(file);
        }
    });

    // Drag and drop
    $(document).on('dragover', '#upload-area', function(e) {
        e.preventDefault();
        e.stopPropagation();
        $(this).addClass('dragover');
    });

    $(document).on('dragleave', '#upload-area', function(e) {
        e.preventDefault();
        e.stopPropagation();
        $(this).removeClass('dragover');
    });

    $(document).on('drop', '#upload-area', function(e) {
        e.preventDefault();
        e.stopPropagation();
        $(this).removeClass('dragover');

        const file = e.originalEvent.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
            handleImageFile(file);
        }
    });

    // Remove image
    $(document).on('click', '#remove-image-btn', function(e) {
        e.stopPropagation();
        uploadedImageData = null;
        stereotypeGeneratedBuild = null;
        $('#stereotype-image-input').val('');
        $('#image-preview').hide();
        $('#upload-placeholder').show();
        $('#analyze-stereotype-btn').hide();
        $('#stereotype-status').hide();
        $('#stereotype-result').hide();
    });

    // Handle image file
    function handleImageFile(file) {
        // Validate file size (5MB max)
        if (file.size > 5 * 1024 * 1024) {
            alert('File size must be less than 5MB');
            return;
        }

        // Validate file type
        if (!file.type.startsWith('image/')) {
            alert('Please upload an image file');
            return;
        }

        // Read and display image
        const reader = new FileReader();
        reader.onload = function(e) {
            uploadedImageData = e.target.result;
            $('#preview-img').attr('src', e.target.result);
            $('#upload-placeholder').hide();
            $('#image-preview').show();
            $('#analyze-stereotype-btn').show();
            $('#stereotype-status').hide();
            $('#stereotype-result').hide();
        };
        reader.readAsDataURL(file);
    }

    // Analyze stereotype button
    $(document).on('click', '#analyze-stereotype-btn', function() {
        if (!uploadedImageData) {
            alert('Please upload an image first');
            return;
        }

        // Show loading status
        $('#stereotype-status').show();
        $('#status-message').html('🔮 Analyzing image and generating stereotypical build...');
        $('#analyze-stereotype-btn').prop('disabled', true);

        // Send image to backend for analysis. The Grok key rides on the
        // X-Grok-API-Key header injected by $.ajaxSetup; it is never echoed
        // into the request body.
        $.ajax({
            url: '/analyze_stereotype',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                image_data: uploadedImageData
            }),
            success: function(response) {
                if (response.success) {
                    stereotypeGeneratedBuild = response.build;
                    $('#status-message').text('✅ Analysis complete!');

                    // Display the generated build
                    $('#stereotype-result').show();
                    $('#result-content').html(formatStereotypeBuild(response.build));

                    setTimeout(() => {
                        $('#stereotype-status').hide();
                    }, 2000);
                } else {
                    // Error message comes from the server (and may echo crafted
                    // input from the LLM response); render as text to keep it
                    // out of the HTML parser.
                    $('#status-message').text('❌ Error: ' + response.message);
                }
                $('#analyze-stereotype-btn').prop('disabled', false);
            },
            error: function(xhr) {
                const errorMsg = xhr.responseJSON?.message || 'Failed to analyze image';
                $('#status-message').text('❌ Error: ' + errorMsg);
                $('#analyze-stereotype-btn').prop('disabled', false);
            }
        });
    });

    // Format stereotype build for display. Every build.* field is escaped
    // because the values come from the LLM response and can therefore contain
    // arbitrary HTML / script payloads.
    function formatStereotypeBuild(build) {
        let html = '<div class="build-details">';

        if (build.description) {
            html += `<p><strong>Description:</strong> ${escapeHtml(build.description)}</p>`;
        }

        if (build.character_name) {
            html += `<p><strong>Character Name:</strong> ${escapeHtml(build.character_name)}</p>`;
        }

        if (build.character_age) {
            html += `<p><strong>Age:</strong> ${escapeHtml(build.character_age)}</p>`;
        }

        if (build.character_gender) {
            html += `<p><strong>Gender:</strong> ${escapeHtml(build.character_gender)}</p>`;
        }

        if (build.story_inspiration) {
            html += `<p><strong>Story Theme:</strong> ${escapeHtml(build.story_inspiration)}</p>`;
        }

        html += '</div>';
        return html;
    }

    // ===== SETTINGS FUNCTIONALITY =====
    let currentSettings = null;

    function loadSettings() {
        $.ajax({
            url: '/api/settings',
            type: 'GET',
            success: function(settings) {
                currentSettings = settings;

                // Set model dropdowns
                $('#min-grok-select').val(settings.min_grok);
                $('#max-grok-select').val(settings.max_grok);

                // Attach auto-save handlers to dropdowns
                $('#min-grok-select, #max-grok-select').off('change').on('change', autoSaveSettings);
            },
            error: function(error) {
                console.error('Error loading settings:', error);
                $('#settings-message').text('Error loading settings').css('color', 'red').show();
            }
        });
    }

    function autoSaveSettings() {
        const settingsData = {
            min_grok: $('#min-grok-select').val(),
            max_grok: $('#max-grok-select').val(),
        };

        $.ajax({
            url: '/api/settings/save',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(settingsData),
            success: function() {
                $('#settings-message').text('✅ Saved').css('color', 'green').show();
                setTimeout(() => $('#settings-message').fadeOut(), 2000);
            },
            error: function(error) {
                console.error('Error saving settings:', error);
                $('#settings-message').text('❌ Error saving').css('color', 'red').show();
            }
        });
    }

    // Back button for settings
    $('#settings-back-btn').on('click', function() {
        showMainMenu();
    });
});
