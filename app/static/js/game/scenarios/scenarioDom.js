// scenarioDom.js
// Shared imperative DOM helpers used by every scenario renderer. Keeping
// these out of the per-kind modules prevents each one from re-deriving
// the same wrappers (header / log / button / progress bar) and gives the
// scenario panels a consistent look without depending on Handlebars
// (which the project's CSP-safe template engine doesn't fully support).

export function el(tag, opts = {}, children = []) {
    const node = document.createElement(tag);
    if (opts.className) node.className = opts.className;
    if (opts.id) node.id = opts.id;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.html != null) node.innerHTML = opts.html;
    if (opts.attrs) {
        for (const [k, v] of Object.entries(opts.attrs)) {
            if (v == null) continue;
            node.setAttribute(k, String(v));
        }
    }
    if (opts.dataset) {
        for (const [k, v] of Object.entries(opts.dataset)) {
            node.dataset[k] = String(v);
        }
    }
    (Array.isArray(children) ? children : [children]).forEach(c => {
        if (c == null) return;
        node.appendChild(typeof c === 'string'
            ? document.createTextNode(c) : c);
    });
    return node;
}

export function button(label, opts = {}) {
    const b = el('button', {
        className: opts.className || 'btn btn-sm btn-secondary scenario-btn',
        attrs: { type: 'button', title: opts.title || label,
                 disabled: opts.disabled ? 'disabled' : null },
        text: label,
    });
    if (opts.onClick) b.addEventListener('click', opts.onClick);
    return b;
}

export function header(title, { onLeave } = {}) {
    const wrap = el('div', { className: 'scenario-header' });
    wrap.appendChild(el('h4', { className: 'scenario-title', text: title }));
    if (onLeave) {
        wrap.appendChild(button('✕ Leave', {
            className: 'btn btn-sm btn-outline-light scenario-leave',
            onClick: onLeave,
            title: 'Abandon this scenario',
        }));
    }
    return wrap;
}

export function progressBar(current, max, { className = '' } = {}) {
    const ratio = max > 0 ? Math.max(0, Math.min(1, current / max)) : 0;
    const wrap = el('div', { className: `scenario-bar ${className}`.trim() });
    const fill = el('div', {
        className: 'scenario-bar-fill',
        attrs: { style: `width:${(ratio * 100).toFixed(1)}%;` },
    });
    const lbl = el('span', {
        className: 'scenario-bar-label',
        text: `${current ?? 0} / ${max ?? 0}`,
    });
    wrap.appendChild(fill);
    wrap.appendChild(lbl);
    return wrap;
}

// Render the scenario's rolling action log as a compact <ul>. Logs come
// from each handler's state.log (battle / trade) or state.history
// (dialogue) and are already strings so we just escape them via
// textContent.
export function logList(rows, { limit = 8 } = {}) {
    const wrap = el('div', { className: 'scenario-log' });
    const ul = el('ul', { className: 'scenario-log-list' });
    (rows || []).slice(-limit).forEach(row => {
        const li = el('li', { className: 'scenario-log-row' });
        const speaker = row.actor || row.speaker || '';
        if (speaker) {
            li.appendChild(el('span', {
                className: 'scenario-log-actor', text: `${speaker}: `,
            }));
        }
        li.appendChild(el('span', {
            className: 'scenario-log-text', text: row.text || '',
        }));
        ul.appendChild(li);
    });
    wrap.appendChild(ul);
    return wrap;
}

export function errorBanner() {
    return el('div', { className: 'scenario-error', attrs: { hidden: 'hidden' } });
}

export function showError(banner, message) {
    if (!banner) return;
    if (!message) {
        banner.textContent = '';
        banner.setAttribute('hidden', 'hidden');
        return;
    }
    banner.textContent = message;
    banner.removeAttribute('hidden');
}
