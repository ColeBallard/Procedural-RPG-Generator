// dialogueScenario.js
// Renders the focused conversation panel: NPC roster (with relationship
// readouts), verb bar, and a free-form line input. Action submissions
// route through the controller's submitAction adapter and the panel
// re-renders from whichever view the backend returns.
import { el, button, header, logList, errorBanner, showError }
    from './scenarioDom.js';

const VERB_LABELS = {
    say: 'Say',
    persuade: 'Persuade',
    intimidate: 'Intimidate',
    flirt: 'Flirt',
    gift: 'Gift',
    leave: 'Leave',
};

const RELATIONSHIP_KEYS = [
    ['attraction', '💕'],
    ['respect', '🎖'],
    ['trust', '🤝'],
    ['familiarity', '🪞'],
    ['anger', '😠'],
    ['fear', '😨'],
];

export function renderDialogueScenario(view, controller) {
    const root = document.getElementById('scenario-ui');
    if (!root) return;
    const npcLabel = (view.npcs || []).map(n => n.name).join(', ') || 'NPC';
    root.appendChild(header(`💬 Conversation with ${npcLabel}`, {
        onLeave: () => controller.submitAction({ verb: 'leave' }),
    }));

    const banner = errorBanner();
    root.appendChild(banner);

    root.appendChild(buildNpcRoster(view, controller));
    root.appendChild(logList(view.history || [], { limit: 10 }));
    root.appendChild(buildInputRow(view, controller, banner));
}

function buildNpcRoster(view, controller) {
    const wrap = el('div', { className: 'scenario-roster' });
    (view.npcs || []).forEach(npc => {
        const card = el('div', {
            className: 'scenario-npc-card'
                + (npc.id === view.current_npc_id ? ' is-active' : ''),
            dataset: { npcId: npc.id },
        });
        card.addEventListener('click', () => {
            // Switching addressed NPC is a no-op verb -- just nudge the
            // backend with the next "say" so the current_npc_id updates.
            // Here we just visually flag it; the next action carries npc_id.
            view.current_npc_id = npc.id;
            wrap.querySelectorAll('.scenario-npc-card').forEach(n =>
                n.classList.toggle('is-active',
                    Number(n.dataset.npcId) === npc.id));
        });
        const subtitleParts = [];
        if (npc.race) subtitleParts.push(npc.race);
        if (npc.level != null) subtitleParts.push(`Lv ${npc.level}`);
        card.appendChild(el('div', {
            className: 'scenario-npc-name', text: npc.name || '?',
        }));
        if (subtitleParts.length) {
            card.appendChild(el('div', {
                className: 'scenario-npc-subtitle',
                text: subtitleParts.join(' · '),
            }));
        }
        if (npc.relationship) {
            card.appendChild(buildRelationshipStrip(npc.relationship));
        }
        wrap.appendChild(card);
    });
    return wrap;
}

function buildRelationshipStrip(rel) {
    const strip = el('div', { className: 'scenario-relationship' });
    RELATIONSHIP_KEYS.forEach(([key, icon]) => {
        const v = rel[key];
        if (v == null) return;
        const chip = el('span', {
            className: 'scenario-rel-chip',
            attrs: { title: `${key}: ${v} / 10` },
        });
        chip.appendChild(el('span', {
            className: 'scenario-rel-icon', text: icon,
        }));
        chip.appendChild(el('span', {
            className: 'scenario-rel-value', text: String(v),
        }));
        strip.appendChild(chip);
    });
    return strip;
}

function buildInputRow(view, controller, banner) {
    const wrap = el('div', { className: 'scenario-input-row' });

    const verbs = el('div', { className: 'scenario-verb-bar' });
    let activeVerb = 'say';
    const verbButtons = {};
    (view.verbs || Object.keys(VERB_LABELS)).forEach(v => {
        if (v === 'leave') return;  // Leave lives in the header.
        const btn = button(VERB_LABELS[v] || v, {
            className: 'btn btn-sm btn-outline-secondary scenario-verb-btn'
                + (v === activeVerb ? ' is-selected' : ''),
            onClick: () => {
                activeVerb = v;
                Object.entries(verbButtons).forEach(([key, b]) =>
                    b.classList.toggle('is-selected', key === v));
            },
        });
        verbButtons[v] = btn;
        verbs.appendChild(btn);
    });
    wrap.appendChild(verbs);

    const textarea = el('textarea', {
        className: 'form-control scenario-line-input',
        attrs: { rows: '2',
                 placeholder: 'Speak your line… (Ctrl+Enter to submit)' },
    });
    wrap.appendChild(textarea);

    const submit = button('Speak', {
        className: 'btn btn-primary btn-sm scenario-submit',
        onClick: () => doSubmit(),
    });
    wrap.appendChild(submit);

    function doSubmit() {
        const text = (textarea.value || '').trim();
        if (!text && activeVerb !== 'leave') {
            showError(banner, 'Type a line before submitting.');
            return;
        }
        showError(banner, '');
        submit.disabled = true;
        const reenable = () => { submit.disabled = false; };
        controller.submitAction({
            verb: activeVerb, text,
            npc_id: view.current_npc_id || null,
        }, { onError: msg => showError(banner, msg) })
            .then(reenable, reenable);
    }

    textarea.addEventListener('keydown', e => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            doSubmit();
        }
    });

    return wrap;
}
