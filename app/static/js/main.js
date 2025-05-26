import { changeTabViews, setTabClickEvents } from './ui/tabs.js';
import { setLocalStorageItem, getLocalStorageItem } from './utils/storage.js';

let narrativeTemplate;

$(document).ready(function () {
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

    $('#game-view').hide();
    $('#options-view').show();

    changeTabViews('api-keys', ['new-game', 'game-view', 'story-settings']);

    $('#options-view-btn').click(function (e) {
        e.preventDefault();
        $('#game-view').hide();
        $('#options-view').show();
    });

    $('#game-view-btn').click(function (e) {
        e.preventDefault();
        $('#game-view').show();
        $('#options-view').hide();
    });

    setTabClickEvents(['new-game', 'load-game', 'story-settings', 'api-keys']);

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
                $('#game-view').show();
                $('#options-view').hide();

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
