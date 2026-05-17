'use strict';

/* LexGuard API client — base URL matches the Django backend from API.md */
window.LexAPI = (function () {
  const isLocal = /^(localhost|127\.0\.0\.1|0\.0\.0\.0|::1)$/.test(window.location.hostname);
  const BASE = (isLocal && window.location.port && window.location.port !== '8000')
    ? 'http://localhost:8000/api/v1'
    : '/api/v1';

  /* Single in-flight refresh promise — dedupes concurrent 401s so we only
     hit /auth/token/refresh/ once even if several requests expire together. */
  let refreshInFlight = null;

  function isTokenExpired(status, data) {
    return status === 401 && data && data.code === 'token_not_valid';
  }

  /* Clear the session and bounce the user to the auth page so they can sign in
     again. Skipped if we're already on auth.html to avoid a redirect loop. */
  function handleAuthFailure() {
    window.LexAuth.clear();
    const path = window.location.pathname;
    if (!path.endsWith('auth.html') && !path.endsWith('index.html') && path !== '/') {
      const redirect = encodeURIComponent(window.location.href);
      window.location.href = 'auth.html?redirect=' + redirect;
    }
  }

  async function refreshAccessToken() {
    if (refreshInFlight) return refreshInFlight;

    const refreshToken = window.LexAuth.getRefreshToken();
    if (!refreshToken) {
      handleAuthFailure();
      throw new Error('No refresh token available');
    }

    refreshInFlight = (async () => {
      try {
        const res = await fetch(BASE + '/auth/token/refresh/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh: refreshToken }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.access) {
          handleAuthFailure();
          throw new Error('Token refresh failed');
        }
        /* SimpleJWT may rotate the refresh token too — persist whatever came back. */
        window.LexAuth.updateTokens(data);
        return data.access;
      } finally {
        refreshInFlight = null;
      }
    })();

    return refreshInFlight;
  }

  /* Authenticated fetch with automatic refresh-and-retry on token expiry.
     Returns the raw Response so callers can decide how to parse the body. */
  async function authedFetch(url, init = {}) {
    const buildInit = (token) => {
      const headers = new Headers(init.headers || {});
      if (token) headers.set('Authorization', 'Bearer ' + token);
      return { ...init, headers };
    };

    let token = window.LexAuth.getAccessToken();
    let res = await fetch(url, buildInit(token));

    if (res.status !== 401) return res;

    /* Peek at the body to confirm this is a token_not_valid response before
       attempting a refresh. Clone so the caller can still read the body if
       refresh isn't appropriate. */
    const clone = res.clone();
    const data = await clone.json().catch(() => ({}));
    if (!isTokenExpired(res.status, data)) return res;

    try {
      token = await refreshAccessToken();
    } catch {
      return res;
    }
    return fetch(url, buildInit(token));
  }

  async function request(path, { method = 'GET', body, withAuth = false } = {}) {
    const init = {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    };

    let res;
    try {
      res = withAuth
        ? await authedFetch(BASE + path, init)
        : await fetch(BASE + path, init);
    } catch (networkErr) {
      const err = new Error('Cannot reach the server. Make sure the backend is running on http://localhost:8000.');
      err.isNetworkError = true;
      throw err;
    }

    let data;
    try {
      data = await res.json();
    } catch {
      data = {};
    }

    if (!res.ok) {
      const err = new Error('Request failed');
      err.status = res.status;
      err.data = data;
      throw err;
    }

    return data;
  }

  /* ── Auth endpoints — fully implemented per API.md ── */
  const auth = {
    register(payload) {
      return request('/auth/register/', { method: 'POST', body: payload });
    },
    login(payload) {
      return request('/auth/login/', { method: 'POST', body: payload });
    },
    refresh(refreshToken) {
      return request('/auth/token/refresh/', { method: 'POST', body: { refresh: refreshToken } });
    },
    me() {
      return request('/auth/me/', { withAuth: true });
    },
  };

  /* ── Document endpoints ── */
  const documents = {
    /* POST /documents/upload/ — multipart/form-data with `file` field.
       Response: 202 { document_id, status, original_filename } */
    upload(file) {
      const form = new FormData();
      form.append('file', file);
      return authedFetch(BASE + '/documents/upload/', {
        method: 'POST',
        body: form,
      }).then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          const err = new Error('Upload failed');
          err.status = r.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    },

    /* GET /documents/{id}/status/
       Response: { id, original_filename, status, document_type, ... } */
    status(docId) {
      return request('/documents/' + docId + '/status/', { withAuth: true });
    },

    /* GET /documents/{id}/analysis/
       Response: { id, name, type, score, clauses: [...], financial_risks: [...] } */
    analysis(docId) {
      return request('/documents/' + docId + '/analysis/', { withAuth: true });
    },

    /* GET /documents/
       Response: [{ id, name, org, type, score, critical_count, created_at, status }] */
    list() {
      return request('/documents/', { withAuth: true });
    },

    /* GET /documents/{id}/risks/  — raw risk findings (per clause) */
    risks(docId) {
      return request('/documents/' + docId + '/risks/', { withAuth: true });
    },

    /* GET /documents/{id}/financial-risks/  — raw financial risk findings */
    financialRisks(docId) {
      return request('/documents/' + docId + '/financial-risks/', { withAuth: true });
    },

    /* POST /documents/{id}/query/  — Q&A grounded in the document
       Body: { question } → { answer, sources: [{ chunk_id, section, score }] } */
    query(docId, question) {
      return request('/documents/' + docId + '/query/', {
        method: 'POST',
        withAuth: true,
        body: { question },
      });
    },

    /* GET /documents/{id}/chunks/ — paginated chunks */
    chunks(docId, page = 1) {
      return request('/documents/' + docId + '/chunks/?page=' + page, { withAuth: true });
    },
  };

  /* Flatten API error messages into a single human-readable string */
  function extractErrors(err) {
    if (err.isNetworkError) return err.message;
    if (!err.data) return 'Something went wrong. Please try again.';

    const d = err.data;
    if (d.errors && Array.isArray(d.errors)) return d.errors.join(' ');
    if (d.detail) return d.detail;

    /* Field-level validation errors: { email: ["..."], password: ["..."] } */
    const msgs = [];
    for (const [field, messages] of Object.entries(d)) {
      if (Array.isArray(messages)) {
        msgs.push(field + ': ' + messages.join(', '));
      }
    }
    return msgs.length ? msgs.join('\n') : 'Something went wrong. Please try again.';
  }

  return { auth, documents, extractErrors };
})();
