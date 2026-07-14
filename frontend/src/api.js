let csrfToken = '';
let sessionExpiredHandler = null;
let sessionExpired = false;

export class ApiError extends Error {
  constructor(message, { status = 0, payload = null, cause = null } = {}) {
    super(message, { cause });
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

export class SessionExpiredError extends ApiError {
  constructor(message = 'Your FanBridge session has expired.') {
    super(message, { status: 401 });
    this.name = 'SessionExpiredError';
  }
}

export function initApi() {
  csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  sessionExpired = false;
}

export function setSessionExpiredHandler(handler) {
  sessionExpiredHandler = typeof handler === 'function' ? handler : null;
}

function defaultSessionExpiredHandler() {
  const next = `${window.location.pathname}${window.location.search}`;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

function expireSession() {
  if (sessionExpired) return;
  sessionExpired = true;
  (sessionExpiredHandler || defaultSessionExpiredHandler)();
}

function isLoginResponse(response) {
  if (!response.redirected) return false;
  try {
    return new URL(response.url, window.location.href).pathname === '/login';
  } catch {
    return false;
  }
}

function responseMessage(status, payload, rawText) {
  if (payload && typeof payload === 'object') {
    const value = payload.error || payload.message;
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  if (rawText?.trim()) return rawText.trim().slice(0, 500);
  return `Request failed with status ${status}`;
}

export async function fetchApi(endpoint, options = {}) {
  const {
    timeoutMs = 8000,
    signal: callerSignal,
    headers: providedHeaders,
    ...fetchOptions
  } = options;
  const method = (fetchOptions.method || 'GET').toUpperCase();
  const headers = new Headers(providedHeaders || {});
  headers.set('Accept', 'application/json');

  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    headers.set('X-CSRF-Token', csrfToken);
    if (!headers.has('Content-Type') && !(fetchOptions.body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
    }
  }

  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) abortFromCaller();
  callerSignal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort('timeout');
  }, timeoutMs);

  let response;
  try {
    response = await fetch(endpoint, {
      ...fetchOptions,
      method,
      headers,
      signal: controller.signal,
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch (error) {
    if (controller.signal.aborted) {
      if (timedOut) {
        throw new ApiError('FanBridge did not respond before the request timed out.', {
          status: 408,
          cause: error,
        });
      }
      throw new ApiError('The request was cancelled.', { status: 499, cause: error });
    }
    throw new ApiError('FanBridge could not be reached.', { cause: error });
  } finally {
    window.clearTimeout(timeout);
    callerSignal?.removeEventListener('abort', abortFromCaller);
  }

  if (response.status === 401 || isLoginResponse(response)) {
    expireSession();
    throw new SessionExpiredError();
  }

  if (response.status === 204) return null;

  const contentType = response.headers.get('content-type') || '';
  const rawText = await response.text();
  let payload = null;
  if (rawText && contentType.includes('application/json')) {
    try {
      payload = JSON.parse(rawText);
    } catch (error) {
      throw new ApiError('FanBridge returned malformed JSON.', {
        status: response.status,
        cause: error,
      });
    }
  }

  if (!response.ok || payload?.ok === false) {
    throw new ApiError(responseMessage(response.status, payload, rawText), {
      status: response.status,
      payload,
    });
  }

  if (!contentType.includes('application/json')) {
    throw new ApiError('FanBridge returned an unexpected response.', {
      status: response.status,
    });
  }
  return payload;
}

function jsonRequest(endpoint, method, body) {
  return fetchApi(endpoint, { method, body: JSON.stringify(body) });
}

export const api = {
  getStatus: (options) => fetchApi('/api/status', options),
  getHistory: (hours, options) => {
    const query = new URLSearchParams({ hours: String(hours) });
    return fetchApi(`/api/history?${query}`, options);
  },
  getPorts: (options) => fetchApi('/api/ports', options),
  identifyPort: (port) => jsonRequest('/api/ports/identify', 'POST', { port }),
  getAppVersion: (options) => fetchApi('/api/app/version', options),
  getRpStatus: (cid, options) => {
    const query = cid ? `?${new URLSearchParams({ cid })}` : '';
    return fetchApi(`/api/rp/status${query}`, options);
  },
  saveSettings: (settings) => jsonRequest('/api/settings', 'POST', settings),
  saveCurves: (curves) => jsonRequest('/api/curves', 'POST', curves),
  saveConfiguration: (settings, curves) => jsonRequest('/api/config', 'POST', { settings, curves }),
  addController: (controller) => jsonRequest('/api/controllers', 'POST', controller),
  renameController: (id, name) => jsonRequest(`/api/controllers/${encodeURIComponent(id)}`, 'PATCH', { name }),
  deleteController: (id) => fetchApi(`/api/controllers/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  }),
  logout: () => fetchApi('/logout', { method: 'POST' }),
  changePassword: (values) => jsonRequest('/api/change_password', 'POST', values),
  getLogs: ({ since = 0, minLevel = 'INFO', limit = 300 } = {}, options) => {
    const query = new URLSearchParams({
      since: String(since),
      min_level: minLevel,
      limit: String(limit),
    });
    return fetchApi(`/api/logs?${query}`, options);
  },
  clearLogs: () => fetchApi('/api/logs/clear', { method: 'POST' }),
  serialStatus: (cid, options) => {
    const query = new URLSearchParams({ cid });
    return fetchApi(`/api/serial/status?${query}`, options);
  },
  serialTools: (cid, options) => {
    const query = new URLSearchParams({ cid });
    return fetchApi(`/api/serial/tools?${query}`, options);
  },
};
