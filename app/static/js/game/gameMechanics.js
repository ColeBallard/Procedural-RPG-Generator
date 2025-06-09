import { fetchData } from '../api/apiService';

async function loadGameSettings() {
    const settings = await fetchData('/api/settings');
    // Handle settings
}

async function saveGameSettings(settings) {
    await fetchData('/api/settings/save', 'POST', settings);
    // Handle response
}

export { loadGameSettings, saveGameSettings };
