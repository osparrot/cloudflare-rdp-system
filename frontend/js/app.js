// --- Configuration ---
const API_BASE_URL = window.location.origin; // Assumes API is served from the same domain/port
const API_KEY_STORAGE_KEY = 'rdp_api_key';

// --- DOM Elements ---
const loginSection = document.getElementById('login-section');
const dashboardSection = document.getElementById('dashboard-section');
const loginButton = document.getElementById('login-button');
const logoutButton = document.getElementById('logout-button');
const apiKeyInput = document.getElementById('api-key-input');
const loginError = document.getElementById('login-error');
const createSessionForm = document.getElementById('create-session-form');
const createError = document.getElementById('create-error');
const activeSessionDetails = document.getElementById('active-session-details');
const sessionHistoryList = document.getElementById('session-history-list');
const welcomeMessage = document.getElementById('welcome-message');

let currentApiKey = localStorage.getItem(API_KEY_STORAGE_KEY);

// --- API Functions ---

async function apiCall(endpoint, method = 'GET', body = null) {
    const headers = {
        'Content-Type': 'application/json',
        'X-API-Key': currentApiKey
    };

    const config = {
        method: method,
        headers: headers
    };

    if (body && method !== 'GET') {
        config.body = JSON.stringify(body);
    }

    const response = await fetch(`${API_BASE_URL}/api/v1${endpoint}`, config);
    
    if (response.status === 401) {
        logout();
        throw new Error("Invalid API Key or session expired.");
    }

    if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `API Error: ${response.statusText}`);
    }

    if (response.status === 204) {
        return null; // No content for delete
    }

    return response.json();
}

// --- Authentication and UI State ---

function setUIState(isLoggedIn) {
    if (isLoggedIn) {
        loginSection.style.display = 'none';
        dashboardSection.style.display = 'block';
        logoutButton.style.display = 'inline-block';
        welcomeMessage.style.display = 'inline-block';
        loginError.textContent = '';
        fetchDashboardData();
    } else {
        loginSection.style.display = 'block';
        dashboardSection.style.display = 'none';
        logoutButton.style.display = 'none';
        welcomeMessage.style.display = 'none';
        apiKeyInput.value = currentApiKey || '';
    }
}

function login() {
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) {
        loginError.textContent = "API Key cannot be empty.";
        return;
    }

    // Temporarily set the key to test it
    currentApiKey = apiKey;
    
    // Test the key by fetching user details
    apiCall('/user')
        .then(user => {
            localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
            currentApiKey = apiKey;
            welcomeMessage.textContent = `Welcome, User #${user.id}!`;
            setUIState(true);
        })
        .catch(error => {
            loginError.textContent = error.message;
            currentApiKey = null;
            localStorage.removeItem(API_KEY_STORAGE_KEY);
            setUIState(false);
        });
}

function logout() {
    currentApiKey = null;
    localStorage.removeItem(API_KEY_STORAGE_KEY);
    setUIState(false);
}

// --- Dashboard Functions ---

async function fetchDashboardData() {
    try {
        const sessions = await apiCall('/sessions');
        renderSessions(sessions);
    } catch (error) {
        console.error("Error fetching dashboard data:", error);
        // If error is 401, setUIState(false) is already handled in apiCall
        if (currentApiKey) {
            alert(`Could not load dashboard: ${error.message}`);
        }
    }
}

function renderSessions(sessions) {
    const activeSession = sessions.find(s => s.status === 'active');
    const historySessions = sessions.filter(s => s.status !== 'active').slice(0, 5);

    // Render Active Session
    if (activeSession) {
        const expires = new Date(activeSession.expires_at).toLocaleString();
        activeSessionDetails.innerHTML = `
            <p><strong>Status:</strong> <span style="color:green;">READY</span></p>
            <p><strong>Computer Name (FQDN):</strong> ${activeSession.fqdn}</p>
            <p><strong>Username:</strong> ${activeSession.rdp_username}</p>
            <p><strong>Password:</strong> <span id="rdp-password">${activeSession.rdp_password}</span></p>
            <p><strong>Expires:</strong> ${expires}</p>
            <button id="delete-session-button" data-sub="${activeSession.session_sub}">Terminate Session</button>
            <p class="connection-instructions">
                <strong>Connection Instructions:</strong> Use any RDP client (e.g., Microsoft Remote Desktop) and connect to the FQDN.
            </p>
        `;
        document.getElementById('delete-session-button').addEventListener('click', handleDeleteSession);
        createSessionForm.style.display = 'none';
    } else {
        activeSessionDetails.innerHTML = '<p>No active session found. Create one below.</p>';
        createSessionForm.style.display = 'block';
    }

    // Render History
    sessionHistoryList.innerHTML = historySessions.map(s => {
        const statusColor = s.status === 'terminated' ? 'orange' : s.status === 'revoked' ? 'red' : 'gray';
        return `<li>[${s.status.toUpperCase()}] ${s.fqdn} - Created: ${new Date(s.created_at).toLocaleDateString()} - Status: <span style="color:${statusColor};">${s.status}</span></li>`;
    }).join('');
}

async function handleCreateSession(event) {
    event.preventDefault();
    createError.textContent = '';
    
    const duration = document.getElementById('duration').value;
    const username = document.getElementById('username').value;

    const body = {
        duration_hours: parseInt(duration),
        rdp_username: username
    };

    try {
        await apiCall('/sessions', 'POST', body);
        alert("Session created successfully! Waiting for tunnel to establish...");
        // Refresh data after creation
        setTimeout(fetchDashboardData, 5000); // Wait 5 seconds for tunnel to start
    } catch (error) {
        createError.textContent = error.message;
    }
}

async function handleDeleteSession(event) {
    const sessionSub = event.target.dataset.sub;
    if (!confirm(`Are you sure you want to terminate session ${sessionSub}?`)) {
        return;
    }

    try {
        await apiCall(`/sessions/${sessionSub}`, 'DELETE');
        alert("Session terminated successfully. Cleanup script initiated.");
        // Refresh data after deletion
        fetchDashboardData();
    } catch (error) {
        alert(`Error terminating session: ${error.message}`);
    }
}

// --- Event Listeners and Initialization ---

loginButton.addEventListener('click', login);
logoutButton.addEventListener('click', logout);
createSessionForm.addEventListener('submit', handleCreateSession);

// Initialize the UI state on load
if (currentApiKey) {
    setUIState(true);
} else {
    setUIState(false);
}
