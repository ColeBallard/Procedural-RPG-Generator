// utils/template.js
// CSP-safe minimal template renderer covering the Handlebars subset the
// app's templates actually use:
//   {{path.to.value}}        -> HTML-escaped substitution (dotted lookup)
//   {{#if path}} ... {{/if}} -> conditional block on truthiness (nestable)
// Anything else (helpers, partials, {{#each}}, triple-stash {{{raw}}}) is
// intentionally unsupported. Parsing happens once per template; the returned
// function only walks a pre-built node list, so no `eval`/`new Function` is
// involved and the page CSP can keep `script-src` free of `'unsafe-eval'`.

const _ESC = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
    '`': '&#x60;',
    '=': '&#x3D;',
};

function escapeHtml(value) {
    if (value == null) return '';
    return String(value).replace(/[&<>"'`=]/g, c => _ESC[c]);
}

function lookup(ctx, path) {
    if (path === '.' || path === 'this') return ctx;
    const parts = path.split('.');
    let cur = ctx;
    for (const p of parts) {
        if (cur == null) return undefined;
        cur = cur[p];
    }
    return cur;
}

// Split the source into a flat token stream of text / var / if / endif.
// The tag regex deliberately rejects unsupported sigils ({{!comment}},
// {{>partial}}, {{{raw}}}, {{#each}}, {{else}}, ...) by throwing so a
// template that drifts past the supported subset fails loudly at compile
// time rather than silently rendering the wrong thing.
function tokenize(src) {
    const tokens = [];
    const re = /\{\{\s*([^{}]+?)\s*\}\}/g;
    let last = 0;
    let m;
    while ((m = re.exec(src)) !== null) {
        if (m.index > last) {
            tokens.push({ type: 'text', value: src.slice(last, m.index) });
        }
        const tag = m[1];
        if (tag.startsWith('#if ')) {
            tokens.push({ type: 'if', path: tag.slice(4).trim() });
        } else if (tag === '/if') {
            tokens.push({ type: 'endif' });
        } else if (/^[A-Za-z_$][\w$.]*$/.test(tag)) {
            tokens.push({ type: 'var', path: tag });
        } else {
            throw new Error(`Unsupported template tag: {{${tag}}}`);
        }
        last = re.lastIndex;
    }
    if (last < src.length) {
        tokens.push({ type: 'text', value: src.slice(last) });
    }
    return tokens;
}

// Fold the flat token stream into a tree so {{#if}} blocks can nest.
function parse(tokens) {
    let i = 0;
    function parseList(insideIf) {
        const out = [];
        while (i < tokens.length) {
            const t = tokens[i];
            if (t.type === 'endif') {
                if (!insideIf) throw new Error('Unexpected {{/if}}');
                i++;
                return out;
            }
            i++;
            if (t.type === 'if') {
                const children = parseList(true);
                out.push({ type: 'if', path: t.path, children });
            } else {
                out.push(t);
            }
        }
        if (insideIf) throw new Error('Missing {{/if}}');
        return out;
    }
    return parseList(false);
}

function renderNodes(nodes, ctx) {
    let out = '';
    for (const n of nodes) {
        if (n.type === 'text') {
            out += n.value;
        } else if (n.type === 'var') {
            out += escapeHtml(lookup(ctx, n.path));
        } else if (n.type === 'if') {
            if (lookup(ctx, n.path)) {
                out += renderNodes(n.children, ctx);
            }
        }
    }
    return out;
}

function compileTemplate(src) {
    const ast = parse(tokenize(src));
    return function render(ctx) {
        return renderNodes(ast, ctx || {});
    };
}

export { compileTemplate };
