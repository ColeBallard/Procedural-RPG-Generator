// battleScenario.js
// Renders the turn-based combat panel: HP bars for player + opponent, an
// initiative banner, the LLM-coached tactic suggestions (one per category)
// + a free-text declaration input, and the rolling combat log. Action
// submissions go through the controller; the backend adjudicates the
// declared tactic, rolls a dice check, and runs the opponent's response
// in a single round-trip so the renderer just re-paints from the new view.
import { el, button, header, progressBar, logList, errorBanner, showError }
    from './scenarioDom.js';

const VERB_LABELS = {
    attack: '⚔️ Attack',
    defend: '🛡️ Defend',
    flee: '🏃 Flee',
};

const VERB_PLACEHOLDERS = {
    attack: 'Describe how you strike (e.g. "lunge at his sword arm")',
    defend: 'Describe how you defend (e.g. "duck behind the table")',
    flee: 'Describe how you escape (e.g. "vault the railing")',
};

export function renderBattleScenario(view, controller) {
    const root = document.getElementById('scenario-ui');
    if (!root) return;

    const oppName = view.opponent?.name || 'Opponent';
    root.appendChild(header(`⚔️ Combat with ${oppName}`, {
        onLeave: () => controller.abortScenario(),
    }));

    const banner = errorBanner();
    root.appendChild(banner);

    root.appendChild(buildInitiativeStrip(view));
    root.appendChild(buildCombatants(view));
    root.appendChild(buildActionPanel(view, controller, banner));
    root.appendChild(logList(view.log || [], { limit: 8 }));
}

function buildInitiativeStrip(view) {
    const wrap = el('div', { className: 'scenario-initiative' });
    const round = el('span', {
        className: 'scenario-initiative-round',
        text: `Round ${view.round || 1}`,
    });
    wrap.appendChild(round);
    const turn = el('span', { className: 'scenario-initiative-turn' });
    if (view.active_id === view.player?.id) {
        turn.textContent = 'Your turn';
        turn.classList.add('is-player-turn');
    } else if (view.active_id === view.opponent?.id) {
        turn.textContent = `${view.opponent?.name || 'Opponent'}'s turn`;
    }
    wrap.appendChild(turn);
    return wrap;
}

function buildCombatants(view) {
    const wrap = el('div', { className: 'scenario-combatants' });
    if (view.player) wrap.appendChild(combatantCard(view.player, 'player'));
    if (view.opponent) wrap.appendChild(combatantCard(view.opponent, 'opponent'));
    return wrap;
}

function combatantCard(c, side) {
    const card = el('div', {
        className: `scenario-combatant scenario-combatant-${side}`
            + (c.guarding ? ' is-guarding' : ''),
    });
    card.appendChild(el('div', {
        className: 'scenario-combatant-name', text: c.name || '?',
    }));
    const meta = [];
    if (c.race) meta.push(c.race);
    if (c.level != null) meta.push(`Lv ${c.level}`);
    if (meta.length) {
        card.appendChild(el('div', {
            className: 'scenario-combatant-meta', text: meta.join(' · '),
        }));
    }
    card.appendChild(progressBar(c.hp || 0, c.max_hp || 1, {
        className: side === 'player' ? 'scenario-bar-player' : 'scenario-bar-opponent',
    }));
    if (c.guarding) {
        card.appendChild(el('div', {
            className: 'scenario-combatant-status',
            text: '🛡️ Guarding (next attack halved)',
        }));
    }
    return card;
}

function buildActionPanel(view, controller, banner) {
    const wrap = el('div', { className: 'scenario-action-panel' });
    const isPlayerTurn = view.active_id === view.player?.id;
    if (!isPlayerTurn) {
        wrap.appendChild(el('div', {
            className: 'scenario-action-hint',
            text: 'Waiting for opponent…',
        }));
        return wrap;
    }

    // Composer: pick a category, optionally pre-fill from a suggestion,
    // edit / write the declaration, then submit.
    const state = { verb: 'attack' };
    const categoryRow = el('div', { className: 'scenario-verb-row' });
    const verbs = view.verbs || Object.keys(VERB_LABELS);
    const verbButtons = {};
    verbs.forEach(verb => {
        const btn = button(VERB_LABELS[verb] || verb, {
            className: classForVerb(verb, verb === state.verb),
            onClick: () => selectVerb(verb),
        });
        verbButtons[verb] = btn;
        categoryRow.appendChild(btn);
    });
    wrap.appendChild(categoryRow);

    const suggestionsBox = el('div', { className: 'scenario-suggestions' });
    wrap.appendChild(suggestionsBox);

    const input = document.createElement('textarea');
    input.className = 'scenario-action-input form-control';
    input.rows = 2;
    input.placeholder = VERB_PLACEHOLDERS[state.verb] || '';
    wrap.appendChild(input);

    const submitRow = el('div', { className: 'scenario-action-submit-row' });
    const submitBtn = button('Commit', {
        className: 'btn btn-sm btn-primary scenario-action-submit',
        onClick: () => submit(),
    });
    submitRow.appendChild(submitBtn);
    wrap.appendChild(submitRow);

    function selectVerb(verb) {
        state.verb = verb;
        Object.entries(verbButtons).forEach(([v, b]) => {
            b.className = classForVerb(v, v === verb);
        });
        input.placeholder = VERB_PLACEHOLDERS[verb] || '';
        renderSuggestions();
    }

    function renderSuggestions() {
        suggestionsBox.innerHTML = '';
        const suggestion = (view.suggestions || {})[state.verb];
        if (!suggestion || !suggestion.text) {
            suggestionsBox.appendChild(el('div', {
                className: 'scenario-suggestion-empty',
                text: 'No coaching available — declare your own move below.',
            }));
            return;
        }
        const card = el('div', { className: 'scenario-suggestion-card' });
        card.appendChild(el('div', {
            className: 'scenario-suggestion-text', text: suggestion.text,
        }));
        if (suggestion.hint) {
            card.appendChild(el('div', {
                className: 'scenario-suggestion-hint', text: suggestion.hint,
            }));
        }
        card.appendChild(button('Use this tactic', {
            className: 'btn btn-sm btn-outline-light scenario-suggestion-use',
            onClick: () => { input.value = suggestion.text; input.focus(); },
        }));
        suggestionsBox.appendChild(card);
    }

    function submit() {
        const text = (input.value || '').trim();
        showError(banner, '');
        const setBusy = busy => {
            submitBtn.disabled = busy;
            input.disabled = busy;
            Object.values(verbButtons).forEach(b => { b.disabled = busy; });
        };
        setBusy(true);
        const payload = { verb: state.verb };
        if (text) payload.text = text;
        controller.submitAction(payload, {
            onError: msg => showError(banner, msg),
        }).then(() => setBusy(false), () => setBusy(false));
    }

    renderSuggestions();
    return wrap;
}

function classForVerb(verb, active) {
    const base = 'btn btn-sm scenario-verb-btn';
    const tone = (
        verb === 'attack' ? 'danger'
        : verb === 'defend' ? 'info'
        : verb === 'flee' ? 'warning'
        : 'secondary'
    );
    return `${base} btn-${active ? '' : 'outline-'}${tone}`
        + (active ? ' is-active' : '');
}
