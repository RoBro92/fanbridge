let csrfToken = '';

export function initApi() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) {
    csrfToken = meta.content;
  }
}

export async function fetchApi(endpoint, options = {}) {
  const headers = {
    ...options.headers,
  };

  // Add CSRF token to mutating requests
  if (options.method && ['POST', 'PUT', 'DELETE', 'PATCH'].includes(options.method.toUpperCase())) {
    headers['X-CSRF-Token'] = csrfToken;
    if (!headers['Content-Type'] && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
  }

  const res = await fetch(endpoint, {
    ...options,
    headers
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`API Error ${res.status}: ${errText}`);
  }

  // Handle 204 No Content
  if (res.status === 204) return null;
  
  return res.json();
}

export const api = {
  getStatus: () => fetchApi('/api/status'),
  getHistory: (hours) => fetchApi(`/api/history?hours=${hours}`),
  getRpStatus: () => fetchApi('/api/rp/status'),
  getPorts: () => fetchApi('/api/ports'),
  
  saveSettings: (settings) => fetchApi('/api/settings', {
    method: 'POST',
    body: JSON.stringify(settings)
  }),
  saveCurves: (curves) => fetchApi('/api/curves', {
    method: 'POST',
    body: JSON.stringify(curves)
  }),
  addController: (controller) => fetchApi('/api/controllers', {
    method: 'POST',
    body: JSON.stringify(controller)
  }),
  deleteController: (id) => fetchApi(`/api/controllers/${id}`, {
    method: 'DELETE'
  }),
};
