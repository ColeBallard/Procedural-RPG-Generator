import { changeTabViews, setTabClickEvents } from './ui/tabs.js';
import { setLocalStorageItem, getLocalStorageItem } from './utils/storage.js';

let narrativeTemplate;

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

// Compiled Handlebars templates for each entity type rendered in the
// game-view accordions. Populated at startup by loadEntityTemplates().
const entityTemplates = {};

// Mapping of payload key -> { templateName, accordionId }.
// Stats, skills and statuses are no longer rendered as accordions;
// renderCharacterProfile() owns them alongside the main character's identity.
const ENTITY_RENDER_MAP = {
    locations:  { template: 'location',             accordion: 'locationsAccordion' },
    events:     { template: 'event',                accordion: 'eventsAccordion' },
    characters: { template: 'interactingCharacter', accordion: 'charactersAccordion' },
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
let _headerNaturalMargin = null; // measured natural margin-bottom in px, set before first collapse

// Helper functions for authentication
function showLandingPage() {
    $('#landing-page').show();
    // Clear any pending collapse timer before hiding the header
    if (_headerCollapseTimer !== null) {
        clearTimeout(_headerCollapseTimer);
        _headerCollapseTimer = null;
    }
    _headerHoverStart = null;
    $('#app-header').removeClass('header-collapsed').css('margin-bottom', '').hide();
    $('#main-menu').hide();
    $('#game-view').hide();
    $('#options-view').hide();
}

function showApp() {
    $('#landing-page').hide();
    // Ensure header starts fully expanded when app is shown
    $('#app-header').removeClass('header-collapsed').css('margin-bottom', '').show();
    showMainMenu();
    // Sync the cached "key configured server-side" flag so the menu gating
    // and the API Keys view both reflect what the server actually has.
    refreshGrokKeyStatus();
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
                $('#grok-api-key-input').val('');
                _grokKeyAvailable = false;
                updateGameMenuButtonsState();
                updateApiKeysViewStatus();

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

    // Fetch the narrative item template
    $.get('/templates/narrativeItem.hbs', function (template) {
        narrativeTemplate = Handlebars.compile(template);
    }).fail(function (jqXHR, textStatus, errorThrown) {
        console.error('Error fetching narrative item template:', textStatus, errorThrown);
    });

    // Load all per-entity Handlebars templates used by the game-view accordions
    loadEntityTemplates();

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
        updateNarrativeList("Starting world building process...");

        try {
            await executeWorldBuildingWithSSE(seedId, seedData);
        } catch (error) {
            console.error("Error during world building:", error);
            updateNarrativeList("An error occurred during world building: " + error.message);
        }
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

    function loadEntityTemplates() {
        // Each entity template lives at /templates/<name>.html and is wrapped
        // in a <script type="text/x-handlebars-template"> tag. Strip the
        // wrapper before compiling so the inner markup is the template source.
        const names = [
            'location', 'event', 'interactingCharacter', 'quest', 'item'
        ];
        names.forEach(name => {
            $.get(`/templates/${name}.html`)
                .done(html => {
                    const match = html.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
                    const source = match ? match[1] : html;
                    entityTemplates[name] = Handlebars.compile(source);
                })
                .fail((jqXHR, textStatus) => {
                    console.error(`Error fetching template ${name}.html:`, textStatus);
                });
        });
    }

    function loadWorldData(seedId) {
        if (!seedId) return;
        $.ajax({
            url: `/api/world/${seedId}`,
            type: 'GET',
            success: function (world) {
                // Replay the persisted narrative transcript into the panel so
                // refreshing or resuming a seed restores the same scrolling
                // story the user saw originally, with per-kind styling intact.
                $('#narrativeList').empty();
                (world.transcript || []).forEach(entry => {
                    updateNarrativeList(entry);
                });
                Object.entries(ENTITY_RENDER_MAP).forEach(([key, cfg]) => {
                    renderEntityList(world[key] || [], cfg.template, cfg.accordion);
                });
                renderCharacterProfile(
                    world.main_character,
                    world.stats || [],
                    world.skills || [],
                    world.statuses || [],
                );
                // Suggestions reflect the latest transcript, so refresh them
                // alongside the world payload (resume / refresh / new game).
                loadSuggestions(seedId);
            },
            error: function (xhr) {
                console.error('Failed to load world data:', xhr);
            }
        });
    }

    // Fetch a fresh batch of action suggestions for the current situation
    // and render them as clickable buttons above the input box. Failures are
    // silent: suggestions are a UX nicety and must not block the player from
    // typing their own action.
    function loadSuggestions(seedId) {
        if (!seedId) return;
        const $list = $('#optionsList');
        const $label = $('#suggestions-label');
        $list.empty();
        $label.text('Loading suggestions…').show();
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
        const $label = $('#suggestions-label');
        $list.empty();
        if (!suggestions || suggestions.length === 0) {
            $label.hide();
            return;
        }
        $label.text('Suggested actions').show();
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
        $('#suggestions-label').text('Loading suggestions…').show();

        $.ajax({
            url: `/api/seed/${seedId}/turn`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ action: action }),
            success: function (response) {
                if (response.narration) {
                    updateNarrativeList({
                        text: response.narration,
                        kind: 'narration',
                        speaker: 'Narrator',
                    });
                }
                renderSuggestions(response.suggestions || []);
                $input.val('');
            },
            error: function (xhr) {
                const msg = xhr.responseJSON?.message || 'Failed to advance the story.';
                $error.text(msg);
                renderSuggestions([]);
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

    function loadSavedGames() {
        const $view = $('#load-game-view');
        $view.html('<h3 class="tab-heading">💾 Load Game</h3><div id="saved-games-list">Loading...</div>');
        $.ajax({
            url: '/api/seeds',
            type: 'GET',
            success: function (response) {
                const seeds = response.seeds || [];
                const $list = $('#saved-games-list');
                $list.empty();
                if (seeds.length === 0) {
                    $list.html('<p>No saved games found.</p>');
                    return;
                }
                seeds.forEach(s => {
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
            },
            error: function (xhr) {
                $('#saved-games-list').html('<p style="color:red;">Failed to load saved games.</p>');
                console.error('Failed to load saved games:', xhr);
            }
        });
    }

    // Append a narrative entry to #narrativeList. Accepts either a plain
    // string (legacy / quick system messages) or a transcript entry object
    // with at least { text, kind, speaker } so per-kind styling and speaker
    // attribution survive both live-streamed messages and replayed history.
    function updateNarrativeList(entryOrText) {
        if (!narrativeTemplate) {
            console.error('Narrative item template not loaded');
            return;
        }
        const entry = (typeof entryOrText === 'string')
            ? { text: entryOrText, kind: 'system' }
            : entryOrText;
        const context = {
            text: entry.text,
            kind: entry.kind || 'system',
            speaker: entry.speaker || null,
        };
        $('#narrativeList').append(narrativeTemplate(context));
    }

    // === Header auto-collapse on hover leave ===
    // On mouseenter: cancel any pending collapse and restore the header.
    // On mouseleave: wait an exponentially growing delay (based on dwell time,
    //   capped at 9 s input) then collapse the header by sliding it up and
    //   shrinking it to 1/16 its size.
    //
    // Delay formula: e^(dwellSeconds / 3) * 1000 ms
    //   dwell 0 s  →  ~1 s delay
    //   dwell 3 s  →  ~2.7 s delay
    //   dwell 6 s  →  ~7.4 s delay
    //   dwell 9 s  →  ~20 s delay  (cap)
    $('#app-header')
        .on('mouseenter', function () {
            // Cancel any pending collapse
            if (_headerCollapseTimer !== null) {
                clearTimeout(_headerCollapseTimer);
                _headerCollapseTimer = null;
            }
            const $h = $(this);
            // Restore margin-bottom to natural value to trigger smooth transition back
            if (_headerNaturalMargin !== null) {
                $h.css('margin-bottom', _headerNaturalMargin + 'px');
                setTimeout(() => $h.css('margin-bottom', ''), 650);
            }
            $h.removeClass('header-collapsed');
            _headerHoverStart = Date.now();
        })
        .on('mouseleave', function () {
            const dwellMs = _headerHoverStart !== null ? (Date.now() - _headerHoverStart) : 0;
            // Cap the dwell time input at 9 seconds
            const dwellSeconds = Math.min(dwellMs / 1000, 9);
            // Exponential delay
            const delayMs = Math.pow(Math.E, dwellSeconds / 3) * 1000;

            const $header = $(this);
            _headerCollapseTimer = setTimeout(function () {
                // Measure the full height and natural margin before collapsing
                const scale = 0.2625;
                const fullH = $header.outerHeight();
                _headerNaturalMargin = parseFloat($header.css('margin-bottom')) || 0;
                // Compensate for the freed layout space: the transform shrinks visually but doesn't affect layout,
                // so we need to pull the next element up by reducing margin-bottom
                const compensatedMargin = _headerNaturalMargin - fullH * (1 - scale);
                $header.css('margin-bottom', compensatedMargin + 'px');
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

                // Render emotional attributes
                renderEmotionalAttributes(settings.emotional_attributes);

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
            emotional_attributes: currentSettings.emotional_attributes,
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

    function renderEmotionalAttributes(emotionalAttrs) {
        const container = $('#emotional-attributes-container');
        container.empty();

        for (const [attrName, words] of Object.entries(emotionalAttrs)) {
            const attrDiv = $('<div>').addClass('emotional-attribute-section');

            // Header with attribute name
            const header = $('<div>').addClass('attribute-header');
            header.append($('<h5>').text(attrName.charAt(0).toUpperCase() + attrName.slice(1)));
            attrDiv.append(header);

            // Words list
            const wordsList = $('<div>').addClass('words-list').attr('data-attribute', attrName);
            words.forEach((word, index) => {
                const wordItem = $('<div>').addClass('word-item');
                wordItem.append($('<span>').addClass('word-text').text(word));

                const actions = $('<div>').addClass('word-actions');
                actions.append(
                    $('<button>').addClass('btn btn-sm btn-secondary edit-word-btn')
                        .text('✏️').attr('data-index', index).attr('data-attribute', attrName)
                );
                actions.append(
                    $('<button>').addClass('btn btn-sm btn-danger delete-word-btn')
                        .text('🗑️').attr('data-index', index).attr('data-attribute', attrName)
                );
                wordItem.append(actions);
                wordsList.append(wordItem);
            });
            attrDiv.append(wordsList);

            // Add word button
            const addBtn = $('<button>').addClass('btn btn-sm btn-primary add-word-btn')
                .text('➕ Add Word').attr('data-attribute', attrName);
            attrDiv.append(addBtn);

            container.append(attrDiv);
        }

        // Attach event handlers
        attachEmotionalAttributeHandlers();
    }

    function attachEmotionalAttributeHandlers() {
        // Add word
        $('.add-word-btn').off('click').on('click', function() {
            const attrName = $(this).attr('data-attribute');
            const newWord = prompt(`Enter a new word for ${attrName}:`);
            if (newWord && newWord.trim()) {
                currentSettings.emotional_attributes[attrName].push(newWord.trim());
                renderEmotionalAttributes(currentSettings.emotional_attributes);
                autoSaveSettings(); // Auto-save after adding
            }
        });

        // Edit word
        $('.edit-word-btn').off('click').on('click', function() {
            const attrName = $(this).attr('data-attribute');
            const index = parseInt($(this).attr('data-index'));
            const currentWord = currentSettings.emotional_attributes[attrName][index];
            const newWord = prompt(`Edit word:`, currentWord);
            if (newWord && newWord.trim()) {
                currentSettings.emotional_attributes[attrName][index] = newWord.trim();
                renderEmotionalAttributes(currentSettings.emotional_attributes);
                autoSaveSettings(); // Auto-save after editing
            }
        });

        // Delete word
        $('.delete-word-btn').off('click').on('click', function() {
            const attrName = $(this).attr('data-attribute');
            const index = parseInt($(this).attr('data-index'));
            const word = currentSettings.emotional_attributes[attrName][index];
            if (confirm(`Delete "${word}"?`)) {
                currentSettings.emotional_attributes[attrName].splice(index, 1);
                renderEmotionalAttributes(currentSettings.emotional_attributes);
                autoSaveSettings(); // Auto-save after deleting
            }
        });
    }

    // Back button for settings
    $('#settings-back-btn').on('click', function() {
        showMainMenu();
    });
});
