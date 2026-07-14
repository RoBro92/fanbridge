import { api } from '../api.js';

const LOG_LEVEL_COLOURS = {
  DEBUG: 'var(--color-text-muted)',
  INFO: '#3b82f6',
  WARNING: 'var(--color-warning)',
  ERROR: 'var(--color-error)',
  CRITICAL: 'var(--color-error)',
};

export function initLogs(container) {
  container.innerHTML = `
    <div class="glass-card" style="margin-top: 24px;">
      <h3 style="margin:0 0 16px; display:flex; justify-content: space-between; align-items:center;">
        <span>Controller Console</span>
        <div style="display: flex; gap: 8px;">
          <select id="controller-log-level" class="input-base" style="font-size: 11px; padding: 4px 8px;">
            <option value="INFO">Normal</option>
            <option value="DEBUG">Debug</option>
          </select>
          <button id="controller-log-download" class="btn" style="font-size: 11px; padding: 4px 8px;">Download</button>
          <button id="controller-log-clear" class="btn" style="font-size: 11px; padding: 4px 8px;">Clear</button>
        </div>
      </h3>

      <p class="text-muted" style="font-size: 12px; margin: -6px 0 14px;">
        Controller commands, confirmed replies and connection events. Use Debug for additional detail.
      </p>

      <div id="logbox" role="log" aria-live="polite" style="height: 220px; overflow:auto; font-family: ui-monospace, monospace; font-size:12px; background: var(--color-bg-inset); border:1px solid var(--glass-border); border-radius: 8px; padding: 12px; white-space: pre-wrap; color: var(--color-text-primary);">Loading controller logs…</div>
    </div>
  `;

  const logbox = document.getElementById('logbox');
  const levelSelect = document.getElementById('controller-log-level');
  let activeCid = '';
  let lastId = 0;
  let entries = [];

  const appendLogToken = (line, text, colour, background = '') => {
    const token = document.createElement('span');
    token.textContent = text;
    token.style.color = colour;
    if (background) {
      token.style.background = background;
      token.style.borderRadius = '4px';
      token.style.padding = '1px 5px';
    }
    line.appendChild(token);
  };

  const renderLogMessage = (line, message, level) => {
    const parts = String(message || '').split(/\s+\|\s+/);
    parts.forEach((part, index) => {
      if (index) appendLogToken(line, '│', 'var(--color-text-muted)');
      if (part.startsWith('TX ')) {
        appendLogToken(line, 'TX', 'var(--color-warning)', 'hsla(35, 90%, 50%, 0.12)');
        appendLogToken(line, part.slice(3), 'var(--color-text-primary)');
      } else if (part.startsWith('RX STATUS ')) {
        appendLogToken(line, 'RX', 'var(--color-success)', 'hsla(150, 60%, 45%, 0.14)');
        appendLogToken(line, 'STATUS', 'var(--color-success)');
        part.slice(10).split('; ').forEach((field) => {
          appendLogToken(line, field, 'var(--color-text-primary)', 'var(--color-bg-inset)');
        });
      } else if (part.startsWith('RX ')) {
        appendLogToken(line, 'RX', 'var(--color-success)', 'hsla(150, 60%, 45%, 0.14)');
        appendLogToken(line, part.slice(3), 'var(--color-success)');
      } else if (part.startsWith('ERROR ')) {
        appendLogToken(line, 'ERROR', 'var(--color-error)', 'hsla(0, 80%, 50%, 0.12)');
        appendLogToken(line, part.slice(6), 'var(--color-error)');
      } else {
        appendLogToken(
          line,
          part,
          level === 'ERROR' || level === 'CRITICAL'
            ? 'var(--color-error)'
            : 'var(--color-text-primary)',
        );
      }
    });
  };

  const render = () => {
    logbox.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement('span');
      empty.className = 'text-muted';
      empty.textContent = activeCid ? 'No controller-specific log entries yet.' : 'Select a controller to view its logs.';
      logbox.appendChild(empty);
      return;
    }
    entries.forEach((entry) => {
      const line = document.createElement('div');
      const level = String(entry.level || 'INFO').toUpperCase();
      const timestamp = new Date(Number(entry.ts || 0) * 1000);
      const time = Number.isFinite(timestamp.getTime()) ? timestamp.toLocaleTimeString() : '--:--:--';
      line.style.display = 'flex';
      line.style.flexWrap = 'wrap';
      line.style.alignItems = 'center';
      line.style.gap = '5px';
      line.style.marginBottom = '4px';
      appendLogToken(line, time, 'var(--color-text-muted)');
      appendLogToken(
        line,
        level,
        LOG_LEVEL_COLOURS[level] || 'var(--color-text-primary)',
        'var(--color-bg-inset)',
      );
      renderLogMessage(line, String(entry.msg || ''), level);
      logbox.appendChild(line);
    });
    logbox.scrollTop = logbox.scrollHeight;
  };

  const loadLogs = async () => {
    const cid = String(window.activeControllerId || '');
    if (cid !== activeCid) {
      activeCid = cid;
      lastId = 0;
      entries = [];
    }
    if (!cid || document.hidden) {
      render();
      return;
    }
    try {
      const response = await api.getLogs({
        since: lastId,
        minLevel: levelSelect.value === 'DEBUG' ? 'DEBUG' : 'INFO',
        limit: 500,
        cid,
        scope: 'controller',
      });
      if (Array.isArray(response.items) && response.items.length) {
        entries.push(...response.items);
        entries = entries.slice(-500);
      }
      lastId = Math.max(lastId, Number(response.last_id || 0));
      render();
    } catch (error) {
      logbox.textContent = error.message || 'Controller logs are unavailable.';
      logbox.style.color = 'var(--color-error)';
    }
  };

  window.refreshControllerLogs = loadLogs;

  levelSelect.addEventListener('change', async () => {
    try {
      await api.setLogLevel(levelSelect.value);
      lastId = 0;
      entries = [];
      await loadLogs();
    } catch (error) {
      levelSelect.value = 'INFO';
      logbox.textContent = error.message || 'Could not change the log level.';
    }
  });
  document.getElementById('controller-log-clear').addEventListener('click', async () => {
    try {
      const cid = String(window.activeControllerId || '');
      if (!cid) return;
      await api.clearLogs({ scope: 'controller', cid });
      lastId = 0;
      entries = [];
      render();
    } catch (error) {
      logbox.textContent = error.message || 'Could not clear logs.';
    }
  });
  document.getElementById('controller-log-download').addEventListener('click', () => {
    const cid = String(window.activeControllerId || '');
    if (!cid) return;
    window.location.assign(`/api/logs/download?${new URLSearchParams({ cid, format: 'text' })}`);
  });

  loadLogs();
  window.setInterval(loadLogs, 3000);
}

export function initLinkUpdates(container) {
  container.innerHTML = `
    <div class="glass-card firmware-panel">
      <header class="firmware-panel__header">
        <h3>Link Updates & Firmware</h3>
        <span id="rpConnection" class="firmware-connection" data-state="unknown">Checking</span>
      </header>

      <section class="firmware-section" aria-labelledby="firmware-device-heading">
        <h4 id="firmware-device-heading">Device Information</h4>
        <dl class="firmware-device-grid">
          <div class="firmware-device-field firmware-device-field--port">
            <dt>
              <span>Port</span>
              <button type="button" id="rpCopyPort" class="firmware-copy" aria-label="Copy controller port">Copy</button>
            </dt>
            <dd id="rpPort" title="">—</dd>
          </div>
          <div class="firmware-device-field">
            <dt>USB</dt>
            <dd id="rpUsb">—</dd>
          </div>
          <div class="firmware-device-field">
            <dt>Board</dt>
            <dd id="rpBoard">—</dd>
          </div>
          <div class="firmware-device-field">
            <dt>Controller Version</dt>
            <dd id="rpVer">—</dd>
          </div>
        </dl>
      </section>

      <section class="firmware-section firmware-section--management" aria-labelledby="firmware-management-heading">
        <div class="firmware-section__heading">
          <h4 id="firmware-management-heading">Firmware Management</h4>
          <button type="button" id="rpCheckLatest" class="firmware-check-button">Check for updates</button>
        </div>

        <div class="firmware-release-row">
          <div><span>Installed</span><strong id="rpInstalledVersion">—</strong></div>
          <div><span>Latest approved</span><strong id="rpLatestVersion">—</strong></div>
        </div>
        <p id="rpRemoteMessage" class="firmware-release-message">Checking the approved firmware channel…</p>

        <button type="button" id="rpFlashLatest" class="btn btn-primary firmware-install-button" disabled>
          Install latest approved firmware
        </button>

        <div class="firmware-action-separator"><span>or use a local build</span></div>

        <label class="firmware-upload" id="rpUploadLabel" aria-disabled="true">
          <input type="file" id="rpUf2File" accept=".uf2" disabled />
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14.5v3A2.5 2.5 0 0 0 7.5 20h9a2.5 2.5 0 0 0 2.5-2.5v-3" />
          </svg>
          <span>
            <strong>Upload verified .uf2</strong>
            <small>Choose a hardware-verified RP2040 firmware file</small>
          </span>
        </label>

        <p id="rpFlashStatus" class="firmware-operation-status" data-tone="neutral" role="status" aria-live="polite">
          Checking firmware support…
        </p>
      </section>
    </div>
  `;

  let lastCid = '';
  let lastChecked = 0;
  const statusText = document.getElementById('rpFlashStatus');
  const remoteMessage = document.getElementById('rpRemoteMessage');
  const fileInput = document.getElementById('rpUf2File');
  const uploadLabel = document.getElementById('rpUploadLabel');
  const installButton = document.getElementById('rpFlashLatest');
  const checkButton = document.getElementById('rpCheckLatest');
  const copyButton = document.getElementById('rpCopyPort');
  const connectionPill = document.getElementById('rpConnection');
  let latestStatus = null;

  const setOperationStatus = (message, tone = 'neutral') => {
    statusText.textContent = message;
    statusText.dataset.tone = tone;
  };

  const setConnectionState = (connected) => {
    const state = connected === true ? 'connected' : connected === false ? 'disconnected' : 'unknown';
    connectionPill.dataset.state = state;
    connectionPill.textContent = state === 'connected' ? 'Connected' : state === 'disconnected' ? 'Disconnected' : 'Unknown';
  };

  const applyStatus = (result, { preserveOperation = false } = {}) => {
    latestStatus = result;
    const port = result.serial?.preferred || '';
    const installed = result.controller_version || 'Unknown';
    const latest = result.latest_version || 'Not published';
    document.getElementById('rpPort').textContent = port || '—';
    document.getElementById('rpPort').title = port;
    document.getElementById('rpUsb').textContent = result.usb?.location || result.usb?.serial_number || 'Mapped USB device';
    document.getElementById('rpBoard').textContent = result.board || result.product || '—';
    document.getElementById('rpVer').textContent = installed;
    document.getElementById('rpInstalledVersion').textContent = installed;
    document.getElementById('rpLatestVersion').textContent = latest;
    setConnectionState(result.serial?.connected);
    copyButton.disabled = !port;

    remoteMessage.textContent = result.remote_update_message || 'Firmware release status is unavailable.';
    installButton.disabled = result.remote_install_enabled !== true;
    installButton.textContent = result.latest_version
      ? `Install firmware ${result.latest_version}`
      : 'Install latest approved firmware';

    const uploadEnabled = result.product === 'diy' && result.firmware_flash_enabled === true;
    fileInput.disabled = !uploadEnabled;
    uploadLabel.setAttribute('aria-disabled', String(!uploadEnabled));
    uploadLabel.classList.toggle('is-disabled', !uploadEnabled);
    if (preserveOperation) return;
    if (uploadEnabled) {
      setOperationStatus('Ready. Fan output is held at 100% while firmware is written and verified.');
    } else {
      setOperationStatus(
        result.flash_unavailable_reason || 'Firmware installation is unavailable for this controller.',
        'warning',
      );
    }
  };

  window.refreshFirmwarePanel = async (force = false, refreshRelease = false) => {
    const cid = String(window.activeControllerId || '');
    if (!cid) return;
    const now = Date.now();
    if (!force && !refreshRelease && cid === lastCid && now - lastChecked < 30000) return;
    lastCid = cid;
    lastChecked = now;
    try {
      const result = await api.getRpStatus(cid, { refresh: refreshRelease });
      if (cid !== String(window.activeControllerId || '')) return;
      applyStatus(result);
    } catch (error) {
      setConnectionState(null);
      remoteMessage.textContent = 'Firmware release status is unavailable.';
      setOperationStatus(error.message || 'Firmware status is unavailable.', 'error');
    }
  };

  checkButton.addEventListener('click', async () => {
    checkButton.disabled = true;
    checkButton.classList.add('is-loading');
    setOperationStatus('Checking the approved firmware channel…');
    try {
      await window.refreshFirmwarePanel(true, true);
    } finally {
      checkButton.disabled = false;
      checkButton.classList.remove('is-loading');
    }
  });

  copyButton.addEventListener('click', async () => {
    const port = String(document.getElementById('rpPort').textContent || '').trim();
    if (!port || port === '—') return;
    try {
      await navigator.clipboard.writeText(port);
      const original = copyButton.textContent;
      copyButton.textContent = 'Copied';
      window.setTimeout(() => { copyButton.textContent = original; }, 1500);
    } catch {
      setOperationStatus('The port could not be copied. Select it manually instead.', 'warning');
    }
  });

  installButton.addEventListener('click', async () => {
    const cid = String(window.activeControllerId || '');
    const version = String(latestStatus?.latest_version || '');
    if (!cid || !version || latestStatus?.remote_install_enabled !== true) return;
    if (!window.confirm(`Install approved firmware ${version} on this DIY controller? Fan output will be held at 100% and the controller will restart.`)) return;
    installButton.disabled = true;
    checkButton.disabled = true;
    fileInput.disabled = true;
    uploadLabel.classList.add('is-disabled');
    setOperationStatus(`Downloading and verifying firmware ${version}…`, 'working');
    try {
      const result = await api.flashRpLatest(cid, version);
      lastChecked = 0;
      await window.refreshFirmwarePanel(true, true);
      setOperationStatus(
        `Firmware ${result.controller_version || version} installed and verified.`,
        result.verified ? 'success' : 'warning',
      );
    } catch (error) {
      setOperationStatus(error.message || 'Remote firmware installation failed.', 'error');
    } finally {
      checkButton.disabled = false;
      if (latestStatus) applyStatus(latestStatus, { preserveOperation: true });
    }
  });

  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    const cid = String(window.activeControllerId || '');
    if (!file || !cid) return;
    if (!window.confirm(`Flash ${file.name} to the selected DIY controller? Fan output will be held at 100% and the controller will restart.`)) {
      fileInput.value = '';
      return;
    }
    fileInput.disabled = true;
    uploadLabel.classList.add('is-disabled');
    installButton.disabled = true;
    checkButton.disabled = true;
    setOperationStatus(`Validating ${file.name} and flashing the controller…`, 'working');
    try {
      const result = await api.flashRpUpload(cid, file);
      lastChecked = 0;
      await window.refreshFirmwarePanel(true, true);
      setOperationStatus(
        `Firmware flashed successfully${result.controller_version ? ` (${result.controller_version})` : ''}.`,
        result.verified ? 'success' : 'warning',
      );
    } catch (error) {
      setOperationStatus(error.message || 'Firmware flash failed. The controller remains in fail-safe mode.', 'error');
    } finally {
      fileInput.value = '';
      checkButton.disabled = false;
      if (latestStatus) applyStatus(latestStatus, { preserveOperation: true });
    }
  });
}
