async function fetchData(url, method='GET', data=null) {
    const headers = {
        'Content-Type': 'application/json',
        // Add more headers if needed
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
