// utils/storage.js
function setLocalStorageItem(key, value) {
    localStorage.setItem(key, value);
}

function getLocalStorageItem(key) {
    return localStorage.getItem(key);
}

export { setLocalStorageItem, getLocalStorageItem };