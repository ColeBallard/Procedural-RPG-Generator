function _readCsrfCookie() {
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
}

async function fetchData(url, method='GET', data=null) {
    const headers = {
        'Content-Type': 'application/json',
        'X-CSRF-Token': _readCsrfCookie(),
    };
    const body = data ? JSON.stringify(data) : null;
    try {
        const response = await fetch(url, { method, headers, body });
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        return await response.json();
    } catch (error) {
        console.error('Fetch error:', error);
        return null;
    }
}

export { fetchData };
