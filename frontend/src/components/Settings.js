import { api } from '../api.js';

export const CURVE_PROFILES = {
  Quiet: {
    hddTemps: [25, 30, 35, 40, 44, 47, 50, 53],
    hddPwms:  [25, 25, 30, 40, 55, 70, 88, 100],
    ssdTemps: [30, 38, 45, 52, 58, 63, 67, 70],
    ssdPwms:  [25, 25, 30, 40, 55, 70, 88, 100],
  },
  Balanced: {
    hddTemps: [25, 30, 35, 40, 44, 47, 50, 53],
    hddPwms:  [30, 35, 42, 55, 68, 82, 95, 100],
    ssdTemps: [30, 38, 45, 52, 58, 63, 67, 70],
    ssdPwms:  [30, 34, 40, 50, 65, 80, 95, 100],
  },
  Performance: {
    hddTemps: [25, 30, 35, 40, 44, 47, 50, 53],
    hddPwms:  [40, 45, 55, 68, 80, 90, 100, 100],
    ssdTemps: [30, 38, 45, 52, 58, 63, 67, 70],
    ssdPwms:  [40, 45, 52, 63, 76, 88, 100, 100],
  }
};

let latestDriveAssignments = {};
let latestExcludedDevices = [];
let systemLogTimer = null;
const DRIVE_SORT_STORAGE_KEY = 'fanbridge-global-drive-sort';
const DRIVE_SORT_KEYS = new Set(['name', 'device', 'serial', 'capacity', 'type', 'state', 'temp', 'assignment']);
const driveSortCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
let globalDriveSort = { key: 'device', direction: 'asc' };

function loadDriveSortPreference() {
  try {
    const saved = JSON.parse(localStorage.getItem(DRIVE_SORT_STORAGE_KEY) || '{}');
    if (DRIVE_SORT_KEYS.has(saved.key) && ['asc', 'desc'].includes(saved.direction)) {
      globalDriveSort = { key: saved.key, direction: saved.direction };
    }
  } catch {
    globalDriveSort = { key: 'device', direction: 'asc' };
  }
}

function saveDriveSortPreference() {
  try {
    localStorage.setItem(DRIVE_SORT_STORAGE_KEY, JSON.stringify(globalDriveSort));
  } catch {
    // Sorting still works when browser storage is unavailable.
  }
}

function formatUnraidName(drive) {
  const rawName = String(drive?.slot || drive?.section || '').trim();
  if (!rawName) return '—';
  if (/^parity$/i.test(rawName)) return 'Parity 1';
  const parityMatch = rawName.match(/^parity(\d+)$/i);
  if (parityMatch) return `Parity ${parityMatch[1]}`;
  const diskMatch = rawName.match(/^disk(\d+)$/i);
  if (diskMatch) return `Disk ${diskMatch[1]}`;
  return rawName;
}

function getDriveRowSortValue(row, key) {
  if (key === 'assignment') {
    return row.querySelector('select')?.selectedOptions?.[0]?.textContent?.trim() || '';
  }
  const property = `sort${key.charAt(0).toUpperCase()}${key.slice(1)}`;
  return row.dataset[property] || '';
}

function applyGlobalDriveSort() {
  const table = document.querySelector('#global-drives-table-container table');
  const tbody = table?.querySelector('tbody');
  if (!table || !tbody) return;

  const rows = [...tbody.querySelectorAll('tr[data-drive-row]')];
  const numericKeys = new Set(['capacity', 'temp']);
  rows.sort((left, right) => {
    const leftValue = getDriveRowSortValue(left, globalDriveSort.key);
    const rightValue = getDriveRowSortValue(right, globalDriveSort.key);
    const leftMissing = leftValue === '';
    const rightMissing = rightValue === '';
    if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;

    let comparison;
    if (numericKeys.has(globalDriveSort.key)) {
      comparison = Number(leftValue) - Number(rightValue);
    } else {
      comparison = driveSortCollator.compare(leftValue, rightValue);
    }
    return globalDriveSort.direction === 'desc' ? -comparison : comparison;
  });
  rows.forEach(row => tbody.appendChild(row));

  table.querySelectorAll('th[data-sort-key]').forEach(header => {
    const active = header.dataset.sortKey === globalDriveSort.key;
    header.setAttribute('aria-sort', active
      ? (globalDriveSort.direction === 'asc' ? 'ascending' : 'descending')
      : 'none');
  });
}

function formatCapacity(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const order = Math.min(Math.floor(Math.log(bytes) / Math.log(1000)), units.length - 1);
  const amount = bytes / (1000 ** order);
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: amount >= 10 ? 1 : 2 }).format(amount)} ${units[order]}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

function initSystemLogs() {
  const logbox = document.getElementById('system-logbox');
  const levelSelect = document.getElementById('system-log-level');
  const clearButton = document.getElementById('system-log-clear');
  const downloadButton = document.getElementById('system-log-download');
  if (!logbox || !levelSelect || !clearButton || !downloadButton) return;

  let lastId = 0;
  let entries = [];
  const colours = {
    DEBUG: 'var(--color-text-muted)',
    INFO: '#3b82f6',
    WARNING: 'var(--color-warning)',
    ERROR: 'var(--color-error)',
    CRITICAL: 'var(--color-error)',
  };

  const render = () => {
    logbox.replaceChildren();
    logbox.style.removeProperty('color');
    if (!entries.length) {
      const empty = document.createElement('span');
      empty.className = 'text-muted';
      empty.textContent = 'No system log entries at this level yet.';
      logbox.appendChild(empty);
      return;
    }

    entries.forEach((entry) => {
      const line = document.createElement('div');
      const level = String(entry.level || 'INFO').toUpperCase();
      const timestamp = new Date(Number(entry.ts || 0) * 1000);
      const time = Number.isFinite(timestamp.getTime()) ? timestamp.toLocaleTimeString() : '--:--:--';
      line.style.display = 'grid';
      line.style.gridTemplateColumns = '70px 66px minmax(0, 1fr)';
      line.style.gap = '8px';
      line.style.marginBottom = '4px';

      const timeToken = document.createElement('span');
      timeToken.textContent = time;
      timeToken.style.color = 'var(--color-text-muted)';
      const levelToken = document.createElement('span');
      levelToken.textContent = level;
      levelToken.style.color = colours[level] || 'var(--color-text-primary)';
      const messageToken = document.createElement('span');
      messageToken.textContent = String(entry.msg || '');
      messageToken.style.overflowWrap = 'anywhere';
      messageToken.style.color = level === 'ERROR' || level === 'CRITICAL'
        ? 'var(--color-error)'
        : 'var(--color-text-primary)';
      line.append(timeToken, levelToken, messageToken);
      logbox.appendChild(line);
    });
    logbox.scrollTop = logbox.scrollHeight;
  };

  const loadLogs = async () => {
    if (document.hidden) return;
    try {
      const response = await api.getLogs({
        since: lastId,
        minLevel: levelSelect.value === 'DEBUG' ? 'DEBUG' : 'INFO',
        limit: 500,
        scope: 'system',
      });
      if (Array.isArray(response.items) && response.items.length) {
        entries.push(...response.items);
        entries = entries.slice(-500);
      }
      lastId = Math.max(lastId, Number(response.last_id || 0));
      render();
    } catch (error) {
      logbox.textContent = error.message || 'System logs are unavailable.';
      logbox.style.color = 'var(--color-error)';
    }
  };

  levelSelect.addEventListener('change', async () => {
    try {
      await api.setLogLevel(levelSelect.value);
      lastId = 0;
      entries = [];
      await loadLogs();
    } catch (error) {
      levelSelect.value = 'INFO';
      logbox.textContent = error.message || 'Could not change the log level.';
      logbox.style.color = 'var(--color-error)';
    }
  });
  clearButton.addEventListener('click', async () => {
    try {
      await api.clearLogs({ scope: 'system' });
      lastId = 0;
      entries = [];
      render();
    } catch (error) {
      logbox.textContent = error.message || 'Could not clear system logs.';
      logbox.style.color = 'var(--color-error)';
    }
  });
  downloadButton.addEventListener('click', () => {
    window.location.assign('/api/logs/download?scope=system&format=text');
  });

  if (systemLogTimer !== null) window.clearInterval(systemLogTimer);
  loadLogs();
  systemLogTimer = window.setInterval(loadLogs, 3000);
}

export function initSettingsContainer(container) {
  container.innerHTML = `
    <div style="width: 100%;">
      <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; border-bottom: 1px solid var(--color-border);">
        <div class="dash-tabs" style="margin-bottom: 0; border-bottom: none;">
          <button class="dash-tab active" id="btn-tab-global-drives">Drive Assignment</button>
          <button class="dash-tab" id="btn-tab-global-curves">Fan Curves</button>
          <button class="dash-tab" id="btn-tab-global-program">FanBridge Settings</button>
        </div>
        <div id="settings-status" style="font-size: 13px; font-weight: 500; margin-bottom: 12px; margin-right: 16px; opacity: 0; transition: opacity 0.3s;"></div>
      </div>

      <div id="tab-global-drives">

      <div class="glass-card" style="margin-bottom: 24px;">
        <h3 style="margin: 0 0 16px 0;">Global Drive Assignments</h3>
        <p class="text-muted" style="font-size: 13px; margin-bottom: 16px;">Assign detected drives to specific controllers. Drives not assigned to a controller will not factor into its Fan Curve calculation.</p>

        <div id="global-drives-table-container" style="display: none; overflow-x: auto; border: 1px solid var(--glass-border); border-radius: 8px;">
          <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
            <thead style="background: var(--color-bg-inset);">
              <tr>
                <th data-sort-key="name" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="name">Unraid Name</button></th>
                <th data-sort-key="device" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="device">Device</button></th>
                <th data-sort-key="serial" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="serial">Serial / ID</button></th>
                <th data-sort-key="capacity" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="capacity">Capacity</button></th>
                <th data-sort-key="type" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="type">Type</button></th>
                <th data-sort-key="state" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="state">State</button></th>
                <th data-sort-key="temp" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="temp">Temp (°C)</button></th>
                <th data-sort-key="assignment" aria-sort="none"><button type="button" class="table-sort-button" data-sort-key="assignment">Assignment</button></th>
              </tr>
            </thead>
            <tbody>
              <tr style="border-bottom: 1px solid var(--glass-border);">
                <td style="padding: 10px 12px;">/dev/sda</td>
                <td style="padding: 10px 12px; font-family: ui-monospace, monospace;">WDC-WD80EFAX</td>
                <td style="padding: 10px 12px; white-space: nowrap;">8 TB</td>
                <td style="padding: 10px 12px;"><span style="background: hsla(200, 50%, 50%, 0.2); color: #7dd3fc; padding: 2px 6px; border-radius: 4px; font-size: 11px;">HDD</span></td>
                <td style="padding: 10px 12px;">Active</td>
                <td style="padding: 10px 12px;">34°C</td>
                <td style="padding: 10px 12px;">
                  <select class="input-base" style="padding: 4px 8px; font-size: 12px;">
                    <option value="none">Not Included</option>
                    <option value="jbod1" selected>JBOD 1 (Local)</option>
                  </select>
                </td>
              </tr>
              <tr>
                <td style="padding: 10px 12px;">/dev/nvme0n1</td>
                <td style="padding: 10px 12px; font-family: ui-monospace, monospace;">NVME-CACHE</td>
                <td style="padding: 10px 12px; white-space: nowrap;">2 TB</td>
                <td style="padding: 10px 12px;"><span style="background: hsla(280, 50%, 50%, 0.2); color: #d8b4fe; padding: 2px 6px; border-radius: 4px; font-size: 11px;">SSD</span></td>
                <td style="padding: 10px 12px;">Standby</td>
                <td style="padding: 10px 12px;">42°C</td>
                <td style="padding: 10px 12px;">
                  <select class="input-base" style="padding: 4px 8px; font-size: 12px;">
                    <option value="none">Not Included</option>
                    <option value="jbod1" selected>JBOD 1 (Local)</option>
                  </select>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div id="global-drives-empty-state" style="display: none; text-align: center; padding: 48px; border: 1px dashed var(--glass-border); border-radius: 8px;">
          <p class="text-muted" style="font-size: 14px; margin: 0;">No controllers added.</p>
          <p class="text-muted" style="font-size: 13px; margin-top: 8px;">Add a controller first to assign drives.</p>
        </div>
      </div>

      </div> <!-- End tab-global-drives -->

      <div id="tab-global-curves" style="display: none;">

      <div class="glass-card" style="margin-bottom: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <h3 style="margin: 0; font-size: 14px;">Fan Curves</h3>
          <p class="text-muted" style="font-size: 13px; margin: 0;">Configure global thresholds and automated fan curves.</p>
        </div>

        <div style="padding-top: 16px; border-top: 1px solid var(--glass-border);">
          <div style="display: flex; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; gap: 16px;">
            <div style="display: flex; gap: 32px; flex-wrap: wrap; align-items: flex-start;">
              <div style="border-right: 1px solid var(--color-border); padding-right: 32px;">
                <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Single Drive Max Overrides</label>
                <div style="display: flex; gap: 12px;">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">HDD (°C)</span>
                    <input type="number" id="setting-max-hdd" class="input-base" value="53" style="width: 60px;">
                  </div>
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">SSD (°C)</span>
                    <input type="number" id="setting-max-ssd" class="input-base" value="70" style="width: 60px;">
                  </div>
                </div>
              </div>

              <div>
                <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Auto-Apply Settings</label>
                <div style="display: flex; gap: 12px;">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">Min interval (s)</span>
                    <input type="number" id="setting-min-interval" class="input-base" value="3" style="width: 60px;">
                  </div>
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">Hysteresis (°C)</span>
                    <input type="number" id="setting-hysteresis" class="input-base" value="2" style="width: 60px;">
                  </div>
                </div>
              </div>
            </div>

            <div>
              <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px; text-align: right;">Global Profile</label>
              <select id="global-curve-profile" class="input-base" style="width: 150px;">
                <option value="Quiet">Quiet</option>
                <option value="Balanced" selected>Balanced</option>
                <option value="Performance">Performance</option>
                <option value="Custom">Custom</option>
              </select>
            </div>
          </div>

          <div style="margin-bottom: 24px;">
            <h4 style="margin: 0 0 12px 0; font-size: 13px;">HDD Fan Curve</h4>
            <div style="display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px;" id="hdd-curve-container">
              <!-- Dynamic Inputs -->
            </div>
          </div>
          <div>
            <h4 style="margin: 0 0 12px 0; font-size: 13px;">SSD Fan Curve</h4>
            <div style="display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px;" id="ssd-curve-container">
              <!-- Dynamic Inputs -->
            </div>
          </div>
        </div>
      </div>

      </div> <!-- End tab-global-curves -->

      <div id="tab-global-program" style="display: none;">

      <div class="glass-card" style="margin-bottom: 24px;">
        <h3 style="margin: 0 0 16px 0;">Preferences</h3>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px;">
          <div>
            <label class="text-secondary" style="font-size: 12px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Theme Mode</label>
            <select id="setting-theme" class="input-base" style="width: 100%;">
              <option value="system">System Default</option>
              <option value="light">Light Mode</option>
              <option value="dark">Dark Mode</option>
            </select>
          </div>
          <div>
            <label class="text-secondary" style="font-size: 12px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">disks.ini Polling</label>
            <div class="input-base" style="display: flex; justify-content: space-between; align-items: center; background: var(--glass-bg);">
              <span>Last Polled:</span>
              <span id="pip-disks-time" class="text-accent" style="font-family: monospace;">--:--:--</span>
            </div>
          </div>
        </div>
      </div>

      <div class="glass-card" style="margin-bottom: 24px;">
        <h3 style="margin: 0 0 16px 0;">System Security</h3>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px;">
          <div>
            <label class="text-secondary" style="font-size: 12px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Current Password</label>
            <input type="password" id="setting-pw-current" class="input-base" style="width: 100%;">
          </div>
          <div>
            <label class="text-secondary" style="font-size: 12px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">New Password</label>
            <input type="password" id="setting-pw-new" class="input-base" style="width: 100%; margin-bottom: 8px;">
            <input type="password" id="setting-pw-confirm" class="input-base" placeholder="Confirm Password" style="width: 100%;">
          </div>
        </div>
        <button class="btn" style="margin-top: 12px;" id="btn-change-pw">Update Password</button>
      </div>

      <div class="glass-card" style="margin-bottom: 24px;">
        <h3 style="margin:0 0 16px; display:flex; justify-content: space-between; align-items:center;">
          <span>System Logs</span>
          <div style="display: flex; gap: 8px;">
            <select id="system-log-level" class="input-base" style="font-size: 11px; padding: 4px 8px;">
              <option value="INFO">Normal</option>
              <option value="DEBUG">Debug</option>
            </select>
            <button id="system-log-download" class="btn" style="font-size: 11px; padding: 4px 8px;">Download</button>
            <button id="system-log-clear" class="btn" style="font-size: 11px; padding: 4px 8px;">Clear</button>
          </div>
        </h3>
        <p class="text-muted" style="font-size: 12px; margin: -6px 0 14px;">
          Live FanBridge container and application output. Controller-specific traffic is shown in each controller console.
        </p>
        <div id="system-logbox" role="log" aria-live="polite" style="height: 180px; overflow-y: auto; background: var(--color-bg-inset); border: 1px solid var(--glass-border); border-radius: 8px; padding: 12px; font-family: ui-monospace, monospace; font-size: 11px; color: var(--color-text-primary); white-space: pre-wrap;">Loading system logs…</div>
      </div>

      </div> <!-- End tab-global-program -->

      </div> <!-- End tab-global-program -->
    </div>
  `;

  // Sub-tabs Logic
  const tabDrivesBtn = document.getElementById('btn-tab-global-drives');
  const tabCurvesBtn = document.getElementById('btn-tab-global-curves');
  const tabProgramBtn = document.getElementById('btn-tab-global-program');

  const tabDrivesContent = document.getElementById('tab-global-drives');
  const tabCurvesContent = document.getElementById('tab-global-curves');
  const tabProgramContent = document.getElementById('tab-global-program');

  const switchTab = (activeBtn, activeContent) => {
    [tabDrivesBtn, tabCurvesBtn, tabProgramBtn].forEach(btn => btn?.classList.remove('active'));
    [tabDrivesContent, tabCurvesContent, tabProgramContent].forEach(content => {
      if (content) content.style.display = 'none';
    });

    if (activeBtn) activeBtn.classList.add('active');
    if (activeContent) activeContent.style.display = 'block';
  };

  if (tabDrivesBtn) tabDrivesBtn.addEventListener('click', () => switchTab(tabDrivesBtn, tabDrivesContent));
  if (tabCurvesBtn) tabCurvesBtn.addEventListener('click', () => switchTab(tabCurvesBtn, tabCurvesContent));
  if (tabProgramBtn) tabProgramBtn.addEventListener('click', () => switchTab(tabProgramBtn, tabProgramContent));

  // Pre-fill input pairs for curves
  const hddContainer = document.getElementById('hdd-curve-container');
  const ssdContainer = document.getElementById('ssd-curve-container');
  const profileSelect = document.getElementById('global-curve-profile');

  let isGlobalCurvesRendered = false;

  const renderGlobalCurves = (profileName) => {
    let p = CURVE_PROFILES[profileName];
    if (!p) return;

    if (!isGlobalCurvesRendered) {
      hddContainer.innerHTML = generateCurveHTML('hdd', p.hddTemps, p.hddPwms);
      ssdContainer.innerHTML = generateCurveHTML('ssd', p.ssdTemps, p.ssdPwms);

      // Add input listeners to change profile to 'Custom' if user edits manually
      const inputs = [...hddContainer.querySelectorAll('input'), ...ssdContainer.querySelectorAll('input')];
      inputs.forEach(input => {
        input.addEventListener('input', () => {
          profileSelect.value = 'Custom';
        });
      });
      isGlobalCurvesRendered = true;
    } else {
      // Direct value update to avoid browser state restoration on id
      for(let i = 0; i < 8; i++) {
        document.getElementById(`curve-hdd-temp-${i}`).value = p.hddTemps[i];
        document.getElementById(`curve-hdd-pwm-${i}`).value = p.hddPwms[i];
        document.getElementById(`curve-ssd-temp-${i}`).value = p.ssdTemps[i];
        document.getElementById(`curve-ssd-pwm-${i}`).value = p.ssdPwms[i];
      }
    }
  };

  renderGlobalCurves(profileSelect.value);

  profileSelect.addEventListener('change', (e) => {
    if (e.target.value !== 'Custom') {
      renderGlobalCurves(e.target.value);
    }
  });

  // Auto-Save Logic
  let saveTimeout;
  const autoSave = () => {
    clearTimeout(saveTimeout);
    const statusEl = document.getElementById('settings-status');
    if (statusEl) {
      statusEl.textContent = 'Saving...';
      statusEl.style.color = 'var(--color-text-muted)';
    }
    saveTimeout = setTimeout(() => {
      saveSettings();
    }, 1000);
  };

  if (tabDrivesContent) {
    tabDrivesContent.addEventListener('input', autoSave);
    tabDrivesContent.addEventListener('change', autoSave);
  }

  if (tabCurvesContent) {
    tabCurvesContent.addEventListener('input', autoSave);
    tabCurvesContent.addEventListener('change', autoSave);
  }
  const themeSelect = document.getElementById('setting-theme');
  themeSelect.value = localStorage.getItem('fanbridge-theme') || 'system';
  themeSelect.addEventListener('change', (e) => {
    localStorage.setItem('fanbridge-theme', e.target.value);
    if (window.applyTheme) window.applyTheme();
  });

  loadDriveSortPreference();
  container.querySelectorAll('.table-sort-button').forEach(button => {
    button.addEventListener('click', () => {
      const key = button.dataset.sortKey;
      if (!DRIVE_SORT_KEYS.has(key)) return;
      globalDriveSort = {
        key,
        direction: globalDriveSort.key === key && globalDriveSort.direction === 'asc' ? 'desc' : 'asc',
      };
      saveDriveSortPreference();
      applyGlobalDriveSort();
    });
  });
  initSystemLogs();
}

function generateCurveHTML(type, temps, pwms) {
  let tempsHtml = '';
  let pwmsHtml = '';
  for (let i = 0; i < 8; i++) {
    tempsHtml += `<input type="number" id="curve-${type}-temp-${i}" value="${temps[i]}" class="input-base" style="width: 100%; min-width: 40px; flex: 1; text-align: center; padding: 6px;">`;
    pwmsHtml += `<input type="number" id="curve-${type}-pwm-${i}" value="${pwms[i]}" class="input-base" style="width: 100%; min-width: 40px; flex: 1; text-align: center; padding: 6px;">`;
  }

  return `
    <div style="display: flex; flex-direction: column; gap: 8px; width: 100%;">
      <div style="display: flex; align-items: center; gap: 12px;">
        <span style="font-size: 11px; color: var(--color-text-secondary); width: 80px; flex-shrink: 0;">Thresholds (°C)</span>
        <div style="display: flex; gap: 8px; overflow-x: auto; width: 100%;">
          ${tempsHtml}
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 12px;">
        <span style="font-size: 11px; color: var(--color-text-secondary); width: 80px; flex-shrink: 0;">PWM (%)</span>
        <div style="display: flex; gap: 8px; overflow-x: auto; width: 100%;">
          ${pwmsHtml}
        </div>
      </div>
    </div>
  `;
}

export async function loadSettings() {
  try {
    // We can extract settings from the standard /api/status payload if it's there,
    // or fetch explicitly from /api/status or whatever endpoint the API has.
    const res = await api.getStatus();
    if (!res || !res.config) return;

    // We always want to show the global drives table, even if there are no controllers.
    const drivesTableContainer = document.getElementById('global-drives-table-container');
    const drivesEmptyState = document.getElementById('global-drives-empty-state');
    if (drivesTableContainer) drivesTableContainer.style.display = 'block';
    if (drivesEmptyState) drivesEmptyState.style.display = 'none';

    // Populate the global drives table
    const drivesTbody = document.querySelector('#global-drives-table-container tbody');
    if (drivesTbody && res.drives) {
      if (res.drives.length === 0) {
        drivesTbody.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 16px; color: var(--color-text-secondary);">No drives detected in disks.ini</td></tr>';
      } else {
        const controllers = res.controllers || [];
        const excludeList = res.exclude_devices || [];
        latestExcludedDevices = [...excludeList];
        latestDriveAssignments = {
          ...(res.config?.drive_assignments || {}),
          ...(res.drive_assignments || {}),
        };
        const controllerIds = new Set(controllers.map(c => String(c.id || '')));

        drivesTbody.innerHTML = res.drives.map(d => {
          const isExcluded = excludeList.includes(d.dev);
          const isHDD = d.type === 'HDD';
          const typeColor = isHDD ? '#7dd3fc' : '#d8b4fe';
          const typeBg = isHDD ? 'hsla(200, 50%, 50%, 0.2)' : 'hsla(280, 50%, 50%, 0.2)';
          const assignmentKey = String(d.id || d.slot || d.section || d.dev || '');
          const assignment = d.assignment
            ?? latestDriveAssignments[d.id]
            ?? latestDriveAssignments[d.slot]
            ?? latestDriveAssignments[d.section]
            ?? latestDriveAssignments[d.dev];
          const selectedAssignment = !isExcluded && controllerIds.has(String(assignment || ''))
            ? String(assignment)
            : 'none';
          const serial = escapeHtml(d.serial || d.id || '—');
          const unraidName = escapeHtml(formatUnraidName(d));
          const rawUnraidName = escapeHtml(d.slot || d.section || '');
          const capacityBytes = Number.isFinite(Number(d.capacity_bytes)) ? Number(d.capacity_bytes) : '';
          const tempValue = d.temp !== null && Number.isFinite(Number(d.temp)) ? Number(d.temp) : '';
          const controllerOptions = [
            `<option value="none"${selectedAssignment === 'none' ? ' selected' : ''}>Not Included</option>`,
            ...controllers.map(c => `<option value="${c.id}"${selectedAssignment === String(c.id) ? ' selected' : ''}>${c.name}</option>`),
          ].join('');

          return `
            <tr data-drive-row="true" data-sort-name="${unraidName}" data-sort-device="${escapeHtml(d.dev)}" data-sort-serial="${serial}" data-sort-capacity="${capacityBytes}" data-sort-type="${escapeHtml(d.type)}" data-sort-state="${escapeHtml(d.state || '')}" data-sort-temp="${tempValue}" style="border-bottom: 1px solid var(--glass-border);">
              <td style="padding: 10px 12px; white-space: nowrap; font-weight: 500;" title="${rawUnraidName}">${unraidName}</td>
              <td style="padding: 10px 12px; white-space: nowrap; font-family: ui-monospace, monospace;">${escapeHtml(d.dev)}</td>
              <td style="padding: 10px 12px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace;" title="${serial}">${serial}</td>
              <td style="padding: 10px 12px; white-space: nowrap;">${formatCapacity(d.capacity_bytes)}</td>
              <td style="padding: 10px 12px;"><span style="background: ${typeBg}; color: ${typeColor}; padding: 2px 6px; border-radius: 4px; font-size: 11px;">${d.type}</span></td>
              <td style="padding: 10px 12px;">${d.state || 'Unknown'}</td>
              <td style="padding: 10px 12px;">${d.temp !== null ? d.temp + '°C' : '--'}</td>
              <td style="padding: 10px 12px;">
                <select class="input-base" style="padding: 4px 8px; font-size: 12px;" data-dev="${d.dev}" data-assignment-key="${assignmentKey}" data-legacy-excluded="${isExcluded}">
                  ${controllerOptions}
                </select>
              </td>
            </tr>
          `;
        }).join('');
        applyGlobalDriveSort();
      }
    }

    const cfg = res.config;
    document.getElementById('setting-min-interval').value = cfg.auto_apply_min_interval_seconds ?? res.auto_apply_min_interval_seconds ?? 3;
    document.getElementById('setting-hysteresis').value = cfg.auto_apply_hysteresis_percent ?? res.auto_apply_hysteresis_percent ?? 2;

    const curves = res.curves || {};
    populateCurve('hdd', curves.hdd || []);
    populateCurve('ssd', curves.ssd || []);
  } catch (e) {
    console.error('Failed to load settings', e);
  }
}

function populateCurve(type, dataArray) {
  // dataArray looks like [[30,0], [35,20], ...]
  for (let i = 0; i < 8; i++) {
    const tempEl = document.getElementById(`curve-${type}-temp-${i}`);
    const pwmEl = document.getElementById(`curve-${type}-pwm-${i}`);
    if (dataArray[i]) {
      tempEl.value = dataArray[i][0];
      pwmEl.value = dataArray[i][1];
    } else {
      tempEl.value = '';
      pwmEl.value = '';
    }
  }
}

async function saveSettings() {
  const statusEl = document.getElementById('settings-status');
  if (!statusEl) return;

  statusEl.textContent = 'Saving...';
  statusEl.style.color = 'var(--color-text-muted)';
  statusEl.style.opacity = '1';

  try {
    // Read the form
    const excludes = new Set(latestExcludedDevices);
    const driveAssignments = { ...latestDriveAssignments };
    document.querySelectorAll('#global-drives-table-container select').forEach(select => {
      const dev = select.getAttribute('data-dev');
      const assignmentKey = select.getAttribute('data-assignment-key') || dev;
      if (!dev || !assignmentKey) return;
      excludes.delete(dev);
      driveAssignments[assignmentKey] = select.value || 'none';
      if (select.value === 'none' && select.getAttribute('data-legacy-excluded') === 'true') excludes.add(dev);
    });

    const settingsPayload = {
      min_interval_s: parseInt(document.getElementById('setting-min-interval').value || 3, 10),
      hysteresis_percent: parseInt(document.getElementById('setting-hysteresis').value || 2, 10),
      exclude_devices: [...excludes].sort(),
      drive_assignments: driveAssignments,
    };

    const hddCurve = extractCurve('hdd');
    const ssdCurve = extractCurve('ssd');

    // Save
    await api.saveSettings(settingsPayload);
    await api.saveCurves({ hdd: hddCurve, ssd: ssdCurve });
    latestExcludedDevices = settingsPayload.exclude_devices;
    latestDriveAssignments = settingsPayload.drive_assignments;

    statusEl.textContent = '✓ Saved Successfully';
    statusEl.style.color = 'var(--color-success)';
    setTimeout(() => { statusEl.style.opacity = '0'; }, 2000);
  } catch (e) {
    statusEl.textContent = 'Failed to save settings: ' + e.message;
    statusEl.style.color = 'var(--color-error)';
    setTimeout(() => { statusEl.style.opacity = '0'; }, 4000);
  }
}

function extractCurve(type) {
  const curve = [];
  for (let i = 0; i < 8; i++) {
    const temp = parseInt(document.getElementById(`curve-${type}-temp-${i}`).value, 10);
    const pwm = parseInt(document.getElementById(`curve-${type}-pwm-${i}`).value, 10);
    if (!isNaN(temp) && !isNaN(pwm)) {
      curve.push([temp, pwm]);
    }
  }
  // Sort by temperature ascending
  curve.sort((a, b) => a[0] - b[0]);
  return curve;
}
