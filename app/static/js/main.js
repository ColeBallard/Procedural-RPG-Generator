import { changeTabViews, setTabClickEvents } from './ui/tabs.js';
import { setLocalStorageItem, getLocalStorageItem } from './utils/storage.js';

let narrativeTemplate;

// Helper functions for authentication
function showLandingPage() {
    $('#landing-page').show();
    $('#app-header').hide();
    $('#main-menu').hide();
    $('#game-view').hide();
    $('#options-view').hide();
}

function showApp() {
    $('#landing-page').hide();
    $('#app-header').show();
    showMainMenu();
}

// Helper functions for view management
function showMainMenu() {
    $('#main-menu').show();
    $('#game-view').hide();
    $('#options-view').hide();
    $('#back-to-menu-btn').hide();
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
    $('#story-settings-view').hide();

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
    const openaiApiKey = getLocalStorageItem('key15-62689134');
    const groqApiKey = getLocalStorageItem('key73-41976154');
    const seedId = getLocalStorageItem('current-seed-id');

    if (openaiApiKey) {
        $("#openai-api-key-input").val(openaiApiKey);
    }
    if (groqApiKey) {
        $("#groq-api-key-input").val(groqApiKey);
    }

    // Main menu button handlers
    $('#menu-new-game-btn').click(function (e) {
        e.preventDefault();
        showView('new-game');
    });

    $('#menu-load-game-btn').click(function (e) {
        e.preventDefault();
        showView('load-game');
    });

    $('#menu-api-keys-btn').click(function (e) {
        e.preventDefault();
        showView('api-keys');
    });

    $('#menu-story-settings-btn').click(function (e) {
        e.preventDefault();
        showView('story-settings');
    });

    // Back to menu button
    $('#back-to-menu-btn').click(function (e) {
        e.preventDefault();
        showMainMenu();
    });

    $('#save-api-keys-btn').click(function (e) {
        e.preventDefault();
        setLocalStorageItem('key15-62689134', $("#openai-api-key-input").val());
        setLocalStorageItem('key73-41976154', $("#groq-api-key-input").val());
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
        const characterName = $('#new-game-character-name-input').val();
        const characterAge = $('#new-game-character-age-input').val();
        const characterGender = $('#new-game-character-gender-input').val();
        const characterClass = $('#new-game-character-class-input').val();
        const storyInspiration = $('#new-game-inspiration-input').val();

        const seedData = {
            character_name: characterName,
            character_age: characterAge,
            character_gender: characterGender,
            character_class: characterClass,
            story_inspiration: storyInspiration
        };

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
            const response = await executeWorldBuilding(seedId, seedData);
            // Here you have a single aggregated response (an object with keys for each step)
            console.log("Aggregated world building results:", response);
            updateNarrativeList("World building completed successfully!");
        } catch (error) {
            console.error("Error during world building:", error);
            updateNarrativeList("An error occurred during world building.");
        }
    }
    
    async function executeWorldBuilding(seedId, seedData) {
        return new Promise((resolve, reject) => {
            $.ajax({
                url: '/initialize_world_building',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({ 
                    seed_id: seedId, 
                    seed_data: seedData, 
                    openai_api_key: openaiApiKey 
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
});
