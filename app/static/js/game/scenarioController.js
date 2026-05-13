// scenarioController.js
//
// Mounts the structured scenario panel inside #scenario-ui when the
// backend reports an active scenario (battle / dialogue / trade) and
// routes player input through /api/seed/<id>/scenario/<sid>/action
// instead of the free-form turn endpoint while it lives.
//
// The controller is the only piece that talks to the backend's scenario
// routes; per-kind renderer modules (./scenarios/<kind>Scenario.js) are
// passed an ``api`` adapter and a ``container`` element to populate.
// They re-render imperatively whenever the controller hands them a fresh
// view payload.
import { renderDialogueScenario } from './scenarios/dialogueScenario.js';
import { renderBattleScenario } from './scenarios/battleScenario.js';
import { renderTradeScenario } from './scenarios/tradeScenario.js';

const RENDERERS = {
    dialogue: renderDialogueScenario,
    battle: renderBattleScenario,
    trade: renderTradeScenario,
};

let _seedId = null;
let _view = null;
let _entryHandler = null;     // (entries) => void; appends entries to the narrative panel
let _csrfTokenFn = () => '';  // () => string; resolved lazily so main.js helper is reused

function getCsrfToken() { return _csrfTokenFn(); }

function getContainer() { return document.getElementById('scenario-ui'); }
function getUserOptions() { return document.getElementById('user-options'); }

// Public init: wire the controller against the host page once at startup.
// ``onEntries`` is the callback that pushes scenario-produced transcript
// entries into the narrative list (so they get the same styling, TTS
// queueing and pagination as turn-emitted entries).
export function initScenarioController({ onEntries, csrfToken }) {
    _entryHandler = typeof onEntries === 'function' ? onEntries : null;
    if (typeof csrfToken === 'function') _csrfTokenFn = csrfToken;
}

export function setActiveSeed(seedId) {
    _seedId = seedId || null;
}

// Returns true when a scenario panel is currently mounted; main.js uses
// this to decide whether the free-text turn endpoint should be called.
export function isScenarioActive() {
    return _view != null;
}

// Mount or refresh the scenario panel from a view payload returned by
// the backend (start-of-scenario from /turn, post-action from /action,
// or resume from /api/world). Passing null tears down any active panel.
export function mountScenario(view) {
    if (!view || view.status !== 'active') {
        unmountScenario();
        return;
    }
    _view = view;
    const container = getContainer();
    const userOptions = getUserOptions();
    if (!container) return;
    container.classList.remove('hidden');
    if (userOptions) userOptions.classList.add('hidden');
    rerender();
}

export function unmountScenario() {
    _view = null;
    const container = getContainer();
    const userOptions = getUserOptions();
    if (container) {
        container.innerHTML = '';
        container.classList.add('hidden');
    }
    if (userOptions) userOptions.classList.remove('hidden');
}

function rerender() {
    const container = getContainer();
    if (!container || !_view) return;
    const renderer = RENDERERS[_view.kind];
    container.innerHTML = '';
    if (!renderer) {
        const note = document.createElement('div');
        note.className = 'scenario-error';
        note.textContent = `No frontend for scenario kind: ${_view.kind}`;
        container.appendChild(note);
        return;
    }
    renderer(_view, { submitAction, abortScenario });
}

// Submit a structured action to the backend. The backend response shape
// is {success, view, entries, resolved, summary}. Successful actions
// re-render the panel with the new view; failures bubble back to the
// renderer through ``onError``.
function submitAction(payload, { onError } = {}) {
    if (!_seedId || !_view) return Promise.reject(new Error('No active scenario'));
    return $.ajax({
        url: `/api/seed/${_seedId}/scenario/${_view.id}/action`,
        type: 'POST',
        contentType: 'application/json',
        headers: { 'X-CSRF-Token': getCsrfToken() },
        data: JSON.stringify(payload || {}),
    }).then(response => {
        if (Array.isArray(response.entries) && response.entries.length && _entryHandler) {
            response.entries.forEach(e => _entryHandler(e));
        }
        if (response.resolved) {
            unmountScenario();
            return response;
        }
        if (response.view) mountScenario(response.view);
        return response;
    }).catch(xhr => {
        const msg = xhr?.responseJSON?.message
            || xhr?.responseJSON?.error
            || 'Scenario action failed.';
        if (typeof onError === 'function') onError(msg);
        throw xhr;
    });
}

function abortScenario() {
    if (!_seedId || !_view) return Promise.resolve();
    return $.ajax({
        url: `/api/seed/${_seedId}/scenario/${_view.id}/abort`,
        type: 'POST',
        contentType: 'application/json',
        headers: { 'X-CSRF-Token': getCsrfToken() },
    }).always(() => {
        unmountScenario();
    });
}
