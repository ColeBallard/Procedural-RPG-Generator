// tradeScenario.js
// Renders the bartering panel: side-by-side inventories with per-item
// quantity steppers, currency contributions, the merchant's running
// haggle verdict, and Propose / Haggle / Leave controls. Inventory
// edits are sent to the backend immediately so the basket value the
// merchant haggles against always matches what the player sees.
import { el, button, header, logList, errorBanner, showError }
    from './scenarioDom.js';

export function renderTradeScenario(view, controller) {
    const root = document.getElementById('scenario-ui');
    if (!root) return;

    const merchantName = view.merchant?.name || 'Merchant';
    root.appendChild(header(`💰 Trading with ${merchantName}`, {
        onLeave: () => controller.submitAction({ verb: 'leave' }),
    }));

    const banner = errorBanner();
    root.appendChild(banner);

    const summary = el('div', { className: 'scenario-trade-summary' });
    const playerVal = view.basket_value?.player ?? 0;
    const merchantVal = view.basket_value?.merchant ?? 0;
    const balance = playerVal - merchantVal;
    summary.appendChild(el('span', {
        className: 'scenario-trade-balance',
        text: `Offer: ${playerVal} ⇄ ${merchantVal} (Δ ${balance >= 0 ? '+' : ''}${balance})`,
    }));
    if (view.last_haggle) {
        summary.appendChild(el('span', {
            className: 'scenario-trade-verdict'
                + (view.last_haggle.accept ? ' is-accept' : ' is-reject'),
            text: view.last_haggle.accept
                ? `Accepted (adj ${view.last_haggle.price_adjustment ?? 0}%)`
                : `Refused (adj ${view.last_haggle.price_adjustment ?? 0}%)`,
        }));
    }
    root.appendChild(summary);

    const sides = el('div', { className: 'scenario-trade-sides' });
    sides.appendChild(buildSide(view.player, 'player', controller, banner));
    sides.appendChild(buildSide(view.merchant, 'merchant', controller, banner));
    root.appendChild(sides);

    root.appendChild(buildHaggleRow(view, controller, banner));
    root.appendChild(logList(view.log || [], { limit: 8 }));
}

function buildSide(side, role, controller, banner) {
    const wrap = el('div', { className: `scenario-trade-side scenario-trade-${role}` });
    wrap.appendChild(el('h5', {
        className: 'scenario-trade-side-title',
        text: side?.name
            ? `${side.name} (${role === 'player' ? 'you' : 'merchant'})`
            : role,
    }));
    wrap.appendChild(buildCurrencyRow(side, role, controller, banner));
    wrap.appendChild(buildInventoryList(side, role, controller, banner));
    return wrap;
}

function buildCurrencyRow(side, role, controller, banner) {
    const row = el('div', { className: 'scenario-trade-currency-row' });
    row.appendChild(el('label', {
        className: 'scenario-trade-currency-label',
        text: `Currency (${side?.currency ?? 0} held):`,
    }));
    const input = el('input', {
        className: 'form-control form-control-sm scenario-trade-currency-input',
        attrs: { type: 'number', min: '0', max: String(side?.currency ?? 0),
                 value: String(side?.currency_offered ?? 0) },
    });
    input.addEventListener('change', () => {
        const amount = Math.max(0, parseInt(input.value, 10) || 0);
        controller.submitAction({
            verb: 'set_currency', side: role, amount,
        }, { onError: msg => showError(banner, msg) });
    });
    row.appendChild(input);
    return row;
}

function buildInventoryList(side, role, controller, banner) {
    const wrap = el('div', { className: 'scenario-trade-inventory' });
    if (!side?.inventory?.length) {
        wrap.appendChild(el('div', {
            className: 'scenario-trade-empty', text: '(no items)',
        }));
        return wrap;
    }
    side.inventory.forEach(item => {
        const row = el('div', {
            className: 'scenario-trade-item'
                + (item.in_basket > 0 ? ' is-offered' : ''),
        });
        row.appendChild(el('span', {
            className: 'scenario-trade-item-name',
            text: `${item.name} ×${item.quantity}`,
            attrs: { title: `Value: ${item.value}` },
        }));
        const counter = el('span', {
            className: 'scenario-trade-item-basket',
            text: `In basket: ${item.in_basket}`,
        });
        row.appendChild(counter);
        const minus = button('−', {
            className: 'btn btn-sm btn-outline-secondary',
            disabled: item.in_basket <= 0,
            onClick: () => controller.submitAction({
                verb: 'remove', side: role,
                character_item_id: item.character_item_id, quantity: 1,
            }, { onError: msg => showError(banner, msg) }),
        });
        const plus = button('+', {
            className: 'btn btn-sm btn-outline-secondary',
            disabled: item.in_basket >= item.quantity,
            onClick: () => controller.submitAction({
                verb: 'add', side: role,
                character_item_id: item.character_item_id, quantity: 1,
            }, { onError: msg => showError(banner, msg) }),
        });
        row.appendChild(minus);
        row.appendChild(plus);
        wrap.appendChild(row);
    });
    return wrap;
}

function buildHaggleRow(view, controller, banner) {
    const wrap = el('div', { className: 'scenario-trade-haggle' });
    const pitch = el('input', {
        className: 'form-control form-control-sm scenario-trade-pitch',
        attrs: { type: 'text', placeholder: 'Make your case to the merchant…' },
    });
    wrap.appendChild(pitch);
    wrap.appendChild(button('Haggle', {
        className: 'btn btn-sm btn-info',
        onClick: () => {
            const text = (pitch.value || '').trim();
            if (!text) {
                showError(banner, 'Type a short pitch to haggle.');
                return;
            }
            controller.submitAction({ verb: 'haggle', pitch: text }, {
                onError: msg => showError(banner, msg),
            });
        },
    }));
    wrap.appendChild(button('Propose Trade', {
        className: 'btn btn-sm btn-success',
        onClick: () => controller.submitAction({ verb: 'propose' }, {
            onError: msg => showError(banner, msg),
        }),
    }));
    return wrap;
}
