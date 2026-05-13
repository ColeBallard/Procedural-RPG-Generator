// battleScenario.js
// Renders the turn-based combat panel: HP bars for player + opponent,
// an initiative banner, an action bar (Attack / Defend / Flee), and the
// rolling combat log. Action submissions go through the controller; the
// backend runs both the player's verb and the opponent's response in a
// single round-trip so the renderer just re-paints from the new view.
import { el, button, header, progressBar, logList, errorBanner, showError }
    from './scenarioDom.js';

const VERB_LABELS = {
    attack: '⚔️ Attack',
    defend: '🛡️ Defend',
    flee: '🏃 Flee',
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
    root.appendChild(buildActionBar(view, controller, banner));
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

function buildActionBar(view, controller, banner) {
    const wrap = el('div', { className: 'scenario-action-bar' });
    const isPlayerTurn = view.active_id === view.player?.id;
    if (!isPlayerTurn) {
        wrap.appendChild(el('div', {
            className: 'scenario-action-hint',
            text: 'Waiting for opponent…',
        }));
        return wrap;
    }
    (view.verbs || Object.keys(VERB_LABELS)).forEach(verb => {
        const btn = button(VERB_LABELS[verb] || verb, {
            className: classForVerb(verb),
            onClick: () => {
                showError(banner, '');
                disableAll(wrap, true);
                const reenable = () => disableAll(wrap, false);
                controller.submitAction({ verb }, {
                    onError: msg => showError(banner, msg),
                }).then(reenable, reenable);
            },
        });
        wrap.appendChild(btn);
    });
    return wrap;
}

function classForVerb(verb) {
    const base = 'btn btn-sm scenario-verb-btn';
    if (verb === 'attack') return `${base} btn-danger`;
    if (verb === 'defend') return `${base} btn-info`;
    if (verb === 'flee') return `${base} btn-warning`;
    return `${base} btn-secondary`;
}

function disableAll(wrap, disabled) {
    wrap.querySelectorAll('button').forEach(b => { b.disabled = !!disabled; });
}
