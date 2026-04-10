/**
 * Session refresh utility for portal layouts.
 * Keeps user sessions alive by periodically pinging a refresh endpoint.
 * Redirects to login when session expires.
 *
 * Cross-tab coordination: uses BroadcastChannel (with localStorage fallback)
 * so only one tab refreshes at a time, preventing token rotation races that
 * trigger auth_refresh_reuse_detected on the backend.
 *
 * @param {Object} config
 * @param {string} config.refreshUrl - Endpoint for session refresh
 * @param {string} config.loginUrl - Redirect URL on session expiry
 * @param {number} [config.intervalMs=600000] - Refresh interval (default 10 min)
 */
function initSessionRefresh(config) {
    const { refreshUrl, loginUrl, intervalMs = 10 * 60 * 1000 } = config;
    let intervalId = null;
    const originalFetch = window.fetch.bind(window);

    // -----------------------------------------------------------------------
    // Cross-tab coordination
    // -----------------------------------------------------------------------
    const CHANNEL_NAME = "dotmac_session_refresh";
    const LS_LOCK_KEY = "dotmac_refresh_lock";
    const LS_LAST_REFRESH_KEY = "dotmac_refresh_last";
    const LOCK_TTL_MS = 15000; // lock auto-expires after 15 s
    let channel = null;

    try {
        if (typeof BroadcastChannel !== "undefined") {
            channel = new BroadcastChannel(CHANNEL_NAME);
        }
    } catch (_e) {
        // BroadcastChannel unavailable (e.g. Safari < 15.4 in some contexts)
    }

    /**
     * Try to acquire a localStorage-based lock so only one tab refreshes.
     * Returns true if this tab won the lock.
     *
     * Note: this is not fully atomic (TOCTOU between getItem/setItem), but
     * the backend's 30-second grace period on token rotation is the real
     * safety net — this lock just reduces unnecessary traffic.
     */
    function acquireLock() {
        try {
            const raw = localStorage.getItem(LS_LOCK_KEY);
            if (raw) {
                const lock = JSON.parse(raw);
                if (Date.now() - lock.ts < LOCK_TTL_MS) {
                    return false; // another tab holds the lock
                }
            }
            localStorage.setItem(LS_LOCK_KEY, JSON.stringify({ ts: Date.now() }));
            return true;
        } catch (_e) {
            return true; // if localStorage is unavailable, proceed
        }
    }

    function releaseLock() {
        try {
            localStorage.removeItem(LS_LOCK_KEY);
        } catch (_e) {
            // ignore
        }
    }

    /**
     * Read the last successful refresh timestamp from localStorage so newly
     * opened tabs know whether a sibling already refreshed recently.
     */
    function readLastRefresh() {
        try {
            const raw = localStorage.getItem(LS_LAST_REFRESH_KEY);
            return raw ? parseInt(raw, 10) || 0 : 0;
        } catch (_e) {
            return 0;
        }
    }

    function writeLastRefresh(ts) {
        try {
            localStorage.setItem(LS_LAST_REFRESH_KEY, String(ts));
        } catch (_e) {
            // ignore
        }
    }

    /**
     * Notify other tabs that a refresh completed successfully, so they can
     * skip their own scheduled refresh.
     */
    function broadcastRefreshDone(ts) {
        if (channel) {
            try { channel.postMessage({ type: "refresh_done", ts }); } catch (_e) { /* ignore */ }
        }
    }

    /**
     * Notify other tabs that the session is dead — everyone should redirect.
     */
    function broadcastSessionExpired() {
        if (channel) {
            try { channel.postMessage({ type: "session_expired" }); } catch (_e) { /* ignore */ }
        }
    }

    // Track the last successful refresh time so we can skip redundant ones.
    // Seed from localStorage so a newly opened tab sees sibling refreshes.
    let lastRefreshAt = readLastRefresh();

    if (channel) {
        channel.onmessage = function (event) {
            const data = event.data;
            if (!data) return;
            if (data.type === "refresh_done") {
                // Another tab refreshed for us — update our timestamp
                lastRefreshAt = data.ts || Date.now();
            } else if (data.type === "session_expired") {
                redirectToLogin();
            }
        };
    }

    // -----------------------------------------------------------------------
    // URL helpers
    // -----------------------------------------------------------------------
    function resolveRequestUrl(input) {
        if (!input) return null;
        if (typeof input === "string") {
            return new URL(input, window.location.origin);
        }
        if (typeof Request !== "undefined" && input instanceof Request) {
            return new URL(input.url, window.location.origin);
        }
        if (typeof input.url === "string") {
            return new URL(input.url, window.location.origin);
        }
        return null;
    }

    function currentNextUrl() {
        return window.location.pathname + window.location.search;
    }

    function redirectToLogin() {
        if (window.__dotmacAuthRedirecting) return true;
        window.__dotmacAuthRedirecting = true;
        window.location.href = `${loginUrl}?next=${encodeURIComponent(currentNextUrl())}`;
        return true;
    }

    function isLoginResponse(response, explicitLoginUrl) {
        if (!response) return false;
        if (response.status === 401) return true;
        const candidate = explicitLoginUrl || loginUrl;
        if (!response.redirected || !response.url || !candidate) return false;
        try {
            const responseUrl = new URL(response.url, window.location.origin);
            const loginPath = new URL(candidate, window.location.origin).pathname;
            return responseUrl.pathname === loginPath;
        } catch (_err) {
            return false;
        }
    }

    function isSameOriginRequest(input) {
        try {
            const requestUrl = resolveRequestUrl(input);
            return !!requestUrl && requestUrl.origin === window.location.origin;
        } catch (_err) {
            return false;
        }
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------
    window.DotMacAuth = window.DotMacAuth || {};
    window.DotMacAuth.isLoginResponse = isLoginResponse;
    window.DotMacAuth.redirectToLogin = redirectToLogin;
    window.DotMacAuth.handleAuthResponse = function handleAuthResponse(response, explicitLoginUrl) {
        if (isLoginResponse(response, explicitLoginUrl)) {
            redirectToLogin();
            return true;
        }
        return false;
    };
    window.DotMacAuth.fetch = async function authAwareFetch(input, init, explicitLoginUrl) {
        const response = await originalFetch(input, init);
        if (isSameOriginRequest(input) && !(init && init.dotmacSkipAuthRedirect)) {
            window.DotMacAuth.handleAuthResponse(response, explicitLoginUrl);
        }
        return response;
    };

    if (!window.__dotmacAuthFetchPatched) {
        window.__dotmacAuthFetchPatched = true;
        window.fetch = function patchedFetch(input, init) {
            return window.DotMacAuth.fetch(input, init);
        };
    }

    document.body.addEventListener("htmx:beforeSwap", (event) => {
        const xhr = event.detail && event.detail.xhr;
        if (!xhr) return;
        const response = {
            status: xhr.status,
            redirected: xhr.responseURL && xhr.responseURL !== window.location.href,
            url: xhr.responseURL,
        };
        if (window.DotMacAuth.handleAuthResponse(response)) {
            event.detail.shouldSwap = false;
            event.preventDefault();
        }
    });

    // -----------------------------------------------------------------------
    // Core refresh logic with cross-tab coordination
    // -----------------------------------------------------------------------
    async function refreshSession() {
        // Skip if any tab (including this one) refreshed recently
        const now = Date.now();
        const lastKnown = Math.max(lastRefreshAt, readLastRefresh());
        if (now - lastKnown < intervalMs * 0.5) {
            return;
        }

        // Try to acquire the lock — if another tab is already refreshing, skip
        if (!acquireLock()) {
            return;
        }

        try {
            const response = await originalFetch(refreshUrl, { credentials: "same-origin" });
            if (window.DotMacAuth.handleAuthResponse(response)) {
                if (intervalId) {
                    clearInterval(intervalId);
                    intervalId = null;
                }
                broadcastSessionExpired();
                return;
            }
            // Success — persist and notify other tabs
            const refreshTs = Date.now();
            lastRefreshAt = refreshTs;
            writeLastRefresh(refreshTs);
            broadcastRefreshDone(refreshTs);
        } catch (err) {
            // Ignore transient network errors.
        } finally {
            releaseLock();
        }
    }

    function startRefresh() {
        refreshSession();
        intervalId = setInterval(refreshSession, intervalMs);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startRefresh);
    } else {
        startRefresh();
    }

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            refreshSession();
        }
    });
}
