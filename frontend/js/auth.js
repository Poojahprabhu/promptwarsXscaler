'use strict';

/* Auth state — wraps localStorage session management */
window.LexAuth = (function () {
  const KEY = 'lexguard_session';

  function save(tokens, user) {
    localStorage.setItem(KEY, JSON.stringify({ tokens, user }));
  }

  /* Merge refreshed tokens into the existing session without touching the user. */
  function updateTokens(newTokens) {
    const session = getSession();
    if (!session) return;
    session.tokens = { ...session.tokens, ...newTokens };
    localStorage.setItem(KEY, JSON.stringify(session));
  }

  function clear() {
    localStorage.removeItem(KEY);
  }

  function getSession() {
    try {
      const raw = localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function getAccessToken() {
    return getSession()?.tokens?.access || null;
  }

  function getRefreshToken() {
    return getSession()?.tokens?.refresh || null;
  }

  function getUser() {
    return getSession()?.user || null;
  }

  function isAuthenticated() {
    return Boolean(getAccessToken());
  }

  /* Redirect to auth page if not logged in. Returns false if redirected. */
  function requireAuth() {
    if (!isAuthenticated()) {
      const redirect = encodeURIComponent(window.location.href);
      window.location.href = 'auth.html?redirect=' + redirect;
      return false;
    }
    return true;
  }

  /* Redirect to dashboard if already logged in. Returns false if redirected. */
  function requireGuest() {
    if (isAuthenticated()) {
      window.location.href = 'dashboard.html';
      return false;
    }
    return true;
  }

  return { save, updateTokens, clear, getAccessToken, getRefreshToken, getUser, isAuthenticated, requireAuth, requireGuest };
})();
