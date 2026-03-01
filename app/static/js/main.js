import { changeTabViews, setTabClickEvents } from './ui/tabs.js';
import { setLocalStorageItem, getLocalStorageItem } from './utils/storage.js';

let narrativeTemplate;

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
}

// Helper functions for view management
function showMainMenu() {
    $('#main-menu').show();
    $('#game-view').hide();
    $('#options-view').hide();
    $('#back-to-menu-btn').hide();
    updateResumeButtonVisibility();
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

        if (!username || !password) {
            $('#signin-error').text('Please enter both username and password');
            return;
        }

        $.ajax({
            url: '/auth/login',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ username, password }),
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

    // Loading API keys from local storage
    const grokApiKey = getLocalStorageItem('key-grok-xai');
    const seedId = getLocalStorageItem('current-seed-id');

    if (grokApiKey) {
        $("#grok-api-key-input").val(grokApiKey);
    }

    // Main menu button handlers
    $('#menu-resume-game-btn').click(function (e) {
        e.preventDefault();
        const currentSeedId = getLocalStorageItem('current-seed-id');
        if (currentSeedId) {
            showGameView();
            // TODO: Load the existing game state here if needed
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

    $('#save-api-keys-btn').click(function (e) {
        e.preventDefault();
        setLocalStorageItem('key-grok-xai', $("#grok-api-key-input").val());
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

    // Fetch the narrative item template
    $.get('/templates/narrativeItem.hbs', function (template) {
        narrativeTemplate = Handlebars.compile(template);
    }).fail(function (jqXHR, textStatus, errorThrown) {
        console.error('Error fetching narrative item template:', textStatus, errorThrown);
    });

    // Fetch the entire config file from the server
    $.get('/get_config', function (config) {
        const classes = config.classes;
        const classSelect = $('#new-game-character-class-input');
        $.each(classes, function (index, cls) {
            classSelect.append(new Option(cls, cls));
        });
    }).fail(function () {
        console.error('Error fetching config');
    });

    // Handle the Create Seed button click
    $('#create-seed-btn').on('click', function () {
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
            const characterClass = $('#new-game-character-class-input').val();
            const storyInspiration = $('#new-game-inspiration-input').val();

            seedData = {
                character_name: characterName,
                character_age: characterAge,
                character_gender: characterGender,
                character_class: characterClass,
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
            // First, make a POST request to start the SSE stream
            fetch('/initialize_world_building_stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    seed_id: seedId,
                    seed_data: seedData,
                    grok_api_key: grokApiKey
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
                                        updateNarrativeList(event.message);
                                    } else if (event.type === 'complete') {
                                        console.log('World building results:', event.results);
                                        updateNarrativeList("✓ World building completed successfully!");
                                        resolve(event.results);
                                    } else if (event.type === 'error') {
                                        updateNarrativeList("✗ Error: " + event.message);
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

    // Keep the old function as fallback
    async function executeWorldBuilding(seedId, seedData) {
        return new Promise((resolve, reject) => {
            $.ajax({
                url: '/initialize_world_building',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    seed_id: seedId,
                    seed_data: seedData,
                    grok_api_key: grokApiKey
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
    
    function updateNarrativeList(text) {
        if (narrativeTemplate) {
            const context = { text: text };
            const html = narrativeTemplate(context);
            $('#narrativeList').append(html);
        } else {
            console.error('Narrative item template not loaded');
        }
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

        // Send image to backend for analysis
        $.ajax({
            url: '/analyze_stereotype',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                image_data: uploadedImageData,
                grok_api_key: grokApiKey
            }),
            success: function(response) {
                if (response.success) {
                    stereotypeGeneratedBuild = response.build;
                    $('#status-message').html('✅ Analysis complete!');

                    // Display the generated build
                    $('#stereotype-result').show();
                    $('#result-content').html(formatStereotypeBuild(response.build));

                    setTimeout(() => {
                        $('#stereotype-status').hide();
                    }, 2000);
                } else {
                    $('#status-message').html('❌ Error: ' + response.message);
                }
                $('#analyze-stereotype-btn').prop('disabled', false);
            },
            error: function(xhr) {
                const errorMsg = xhr.responseJSON?.message || 'Failed to analyze image';
                $('#status-message').html('❌ Error: ' + errorMsg);
                $('#analyze-stereotype-btn').prop('disabled', false);
            }
        });
    });

    // Format stereotype build for display
    function formatStereotypeBuild(build) {
        let html = '<div class="build-details">';

        if (build.description) {
            html += `<p><strong>Description:</strong> ${build.description}</p>`;
        }

        if (build.character_name) {
            html += `<p><strong>Character Name:</strong> ${build.character_name}</p>`;
        }

        if (build.character_age) {
            html += `<p><strong>Age:</strong> ${build.character_age}</p>`;
        }

        if (build.character_gender) {
            html += `<p><strong>Gender:</strong> ${build.character_gender}</p>`;
        }

        if (build.character_class) {
            html += `<p><strong>Class:</strong> ${build.character_class}</p>`;
        }

        if (build.story_inspiration) {
            html += `<p><strong>Story Theme:</strong> ${build.story_inspiration}</p>`;
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
            classes: currentSettings.classes
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
