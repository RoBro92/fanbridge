import { initDriveTable } from './DriveTable.js';
import { initSerialTools } from './SerialTools.js';
import { initLogs, initLinkUpdates } from './LogsUpdater.js';
import { CURVE_PROFILES } from './Settings.js';
import Chart from 'chart.js/auto';

export function initDashboardContainer(container) {
  // Setup HTML shell
  container.innerHTML = `
    <div class="dash-tabs">
      <button class="dash-tab active" id="btn-tab-drives">Drives & Telemetry</button>
      <button class="dash-tab" id="btn-tab-config">Controller Config</button>
    </div>

    <!-- Empty State -->
    <div id="empty-state-content" style="display: none; text-align: center; padding: 48px 24px; max-width: 500px; margin: 0 auto; height: 100%; flex-direction: column; justify-content: center; align-items: center;">
      <div class="empty-state-robot"></div>
      <h2 style="font-size: 28px; margin-bottom: 8px; font-weight: 700; letter-spacing: -0.5px;">404 Controller Not Found</h2>
      <p class="text-muted" style="font-size: 16px; margin-bottom: 32px;">No controllers set up yet</p>
      <button class="btn btn-outline" style="border-radius: 8px; font-size: 15px; padding: 12px 24px; display: inline-flex; align-items: center; gap: 8px; color: var(--color-primary);" onclick="document.getElementById('nav-add-controller').click()">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
        Add your first controller to get started
      </button>
    </div>

    <!-- TAB 1: Drives & Telemetry -->
    <div id="tab-drives-content" style="display: none;">
      <!-- Global Dashboard Status Pips -->
      <div style="display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap;">
        <div class="status-pip pip-success" id="pip-container-serial">
          <span class="pip-dot"></span>
          <span class="pip-label">Serial</span>
          <span class="pip-value" id="pip-serial-status">Connected</span>
        </div>
        <div class="status-pip pip-success" id="pip-container-12v">
          <span class="pip-dot"></span>
          <span class="pip-label">12V PWR</span>
          <span class="pip-value" id="pip-12v-status">OK</span>
        </div>
        <div class="status-pip pip-success" id="pip-container-thermal">
          <span class="pip-dot"></span>
          <span class="pip-label">Thermals</span>
          <span class="pip-value" id="pip-thermal-status">OK</span>
        </div>
        <div class="status-pip pip-success" id="pip-container-health">
          <span class="pip-dot"></span>
          <span class="pip-label">Fan Health</span>
          <span class="pip-value" id="pip-health-status">OK</span>
        </div>
        <div class="status-pip pip-success" id="pip-container-mode">
          <span class="pip-dot"></span>
          <span class="pip-label">Mode</span>
          <span class="pip-value" id="pip-mode-status">AUTO</span>
        </div>
        <div class="status-pip pip-success" id="pip-container-updated">
          <span class="pip-dot"></span>
          <span class="pip-label">Updated</span>
          <span class="pip-value" id="pip-last-updated">--:--:--</span>
        </div>
        <div class="status-pip">
          <span class="pip-label">Poll Rate</span>
          <select id="pip-refresh-rate" style="background: transparent; border: none; color: var(--color-text-primary); font-size: 11px; font-weight: 600; cursor: pointer; padding: 0; margin-left: -4px;">
            <option value="3">3s</option>
            <option value="5">5s</option>
            <option value="7" selected>7s</option>
            <option value="10">10s</option>
            <option value="15">15s</option>
            <option value="30">30s</option>
          </select>
        </div>
      </div>

      <!-- Historical Graph Container -->
      <div class="glass-card" style="margin-bottom: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <h3 style="margin: 0; font-size: 16px;">PWM & Temperature History</h3>
          <select id="chart-timeframe" class="input-base" style="padding: 4px 8px; font-size: 12px;">
            <option value="1h">1 Hour</option>
            <option value="12h">12 Hours</option>
            <option value="1d">1 Day</option>
            <option value="1w">1 Week</option>
            <option value="1m">1 Month</option>
          </select>
        </div>
        <div style="height: 180px; width: 100%;">
          <canvas id="historyChart"></canvas>
        </div>
      </div>

      <!-- Compact Fan Strip -->
      <div id="dashboard-fans-strip" style="display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin-bottom: 32px;"></div>

      <div id="drive-table-container"></div>
    </div>

    <!-- TAB 2: Controller Config -->
    <div id="tab-config-content" style="display: none;">
      <!-- Condensed Hardware Telemetry & Preferences -->
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px;">
        
        <!-- Hardware Telemetry Card -->
        <div class="glass-card" style="display: flex; flex-direction: column;">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
            <h3 style="margin: 0; font-size: 14px;">Hardware Telemetry</h3>
            <div style="display: flex; gap: 16px; text-align: right;">
              <div>
                <span style="font-size: 10px; color: var(--color-text-secondary); text-transform: uppercase;">Current</span>
                <div id="hero-amps" style="font-size: 16px; font-weight: 600;">-- A</div>
              </div>
              <div>
                <span style="font-size: 10px; color: var(--color-text-secondary); text-transform: uppercase;">Voltage</span>
                <div id="hero-volts" style="font-size: 16px; font-weight: 600;">-- V</div>
              </div>
            </div>
          </div>
          <div style="flex: 1; min-height: 80px; position: relative;">
            <canvas id="powerChart"></canvas>
          </div>
        </div>

        <!-- Controller Preferences Card -->
        <div class="glass-card" style="display: flex; flex-direction: column;">
          <h3 style="margin: 0 0 16px 0; font-size: 14px;">Controller Preferences</h3>
          
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 16px;">
            <div>
              <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Rename Controller</label>
              <div style="display: flex; gap: 8px;">
                <input type="text" id="controller-name" class="input-base" value="JBOD 1 (Local)" style="flex: 1;">
                <button id="btn-rename-controller" class="btn btn-primary" style="padding: 4px 12px;">Save</button>
              </div>
            </div>

            <div>
              <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Refresh Interval (s)</label>
              <div style="display: flex; gap: 8px;">
                <input type="number" id="poll-interval" class="input-base" value="7" min="3" max="60" style="width: 80px;">
                <button class="btn btn-primary" style="padding: 4px 12px;">Save</button>
              </div>
            </div>
          </div>

          <div>
            <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Active Fan Array</label>
            <div style="display: flex; gap: 16px; flex-wrap: wrap;" id="fan-config-toggles">
              <!-- Toggles generated by JS -->
            </div>
          </div>
        </div>
      </div>

      <!-- PWM Control Profile Card -->
      <div class="glass-card" style="margin-bottom: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <h3 style="margin: 0; font-size: 14px;">PWM Control Profile</h3>
          <div style="display: flex; align-items: center; gap: 12px;">
            <span style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--color-text-secondary); font-weight: 600;">Fan Curve</span>
            <div style="display: flex; background: var(--color-bg-inset); border: 1px solid var(--glass-border); border-radius: 6px; overflow: hidden;">
              <button class="btn" id="btn-curve-global" style="border: none; border-radius: 0; background: hsla(150, 60%, 45%, 0.2); color: var(--color-success); font-weight: 600; padding: 4px 16px;">GLOBAL</button>
              <button class="btn" id="btn-curve-custom" style="border: none; border-radius: 0; background: transparent; color: var(--color-text-secondary); font-weight: 600; padding: 4px 16px;">CUSTOM</button>
            </div>
          </div>
        </div>
        
        <div id="local-pwm-curves-container" style="display: none; padding-top: 16px; border-top: 1px solid var(--glass-border);">
          <div style="display: flex; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; gap: 16px;">
            <div style="display: flex; gap: 32px; flex-wrap: wrap; align-items: flex-start;">
              <div style="border-right: 1px solid var(--color-border); padding-right: 32px;">
                <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Single Drive Max Overrides</label>
                <div style="display: flex; gap: 12px;">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">HDD (°C)</span>
                    <input type="number" id="local-max-hdd" class="input-base" value="53" style="width: 60px;">
                  </div>
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">SSD (°C)</span>
                    <input type="number" id="local-max-ssd" class="input-base" value="70" style="width: 60px;">
                  </div>
                </div>
              </div>
              
              <div>
                <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px;">Auto-Apply Settings</label>
                <div style="display: flex; gap: 12px;">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">Min interval (s)</span>
                    <input type="number" id="local-min-interval" class="input-base" value="3" style="width: 60px;">
                  </div>
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 12px;">Hysteresis (°C)</span>
                    <input type="number" id="local-hysteresis" class="input-base" value="2" style="width: 60px;">
                  </div>
                </div>
              </div>
            </div>

            <div>
              <label class="text-secondary" style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; display: block; margin-bottom: 8px; text-align: right;">Base Profile</label>
              <select id="local-curve-profile" class="input-base" style="width: 150px;">
                <option value="Quiet">Quiet</option>
                <option value="Balanced" selected>Balanced</option>
                <option value="Performance">Performance</option>
                <option value="Custom">Custom</option>
              </select>
            </div>
          </div>
          <div style="margin-bottom: 24px;">
            <h4 style="margin: 0 0 12px 0; font-size: 13px;">Local HDD Fan Curve</h4>
            <div style="display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px;" id="local-hdd-curve-container">
              <!-- Dynamic Inputs -->
            </div>
          </div>
          <div>
            <h4 style="margin: 0 0 12px 0; font-size: 13px;">Local SSD Fan Curve</h4>
            <div style="display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px;" id="local-ssd-curve-container">
              <!-- Dynamic Inputs -->
            </div>
          </div>
          <div style="display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 16px;">
            <span id="local-pwm-status" style="font-size: 13px; color: var(--color-success); opacity: 0; transition: opacity 0.3s; font-weight: 500;">✓ Saved Successfully</span>
            <button class="btn btn-primary" id="btn-save-local-pwm">Save Local Overrides</button>
          </div>
        </div>
      </div>

      <!-- Danger Zone -->
      <div class="glass-card" style="margin-bottom: 24px; border: 1px solid hsla(0, 80%, 50%, 0.3);">
        <h3 style="margin: 0 0 8px 0; font-size: 14px; color: var(--color-error);">Danger Zone</h3>
        <p class="text-muted" style="font-size: 13px; margin: 0 0 16px 0;">Permanently remove this controller from FanBridge.</p>
        <button class="btn" id="btn-delete-controller" style="background: hsla(0, 80%, 50%, 0.1); border: 1px solid hsla(0, 80%, 50%, 0.3); color: var(--color-error);">Delete Controller</button>
      </div>

      <div id="serial-tools-container"></div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 24px; align-items: stretch;">
        <div id="logs-container" style="display: flex; flex-direction: column;"></div>
        <div id="updates-container" style="display: flex; flex-direction: column;"></div>
      </div>
    </div>
  `;

  // Generate Fan Toggles and Fan Strip dynamically
  let fansHtml = '';
  let togglesHtml = '';
  for (let i = 0; i < 6; i++) {
    const isEnabled = localStorage.getItem(`fan-enabled-${i}`) !== 'false';
    
    togglesHtml += `
      <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" id="toggle-fan-${i}" ${isEnabled ? 'checked' : ''} style="cursor: pointer;">
        <span style="font-size: 12px; font-weight: 500;">Fan ${i + 1}</span>
      </label>
    `;

    fansHtml += `
      <div class="glass-card" id="fan-card-${i}" style="padding: 12px; border-left: 4px solid var(--glass-border); display: ${isEnabled ? 'block' : 'none'};">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--color-text-secondary);">Fan ${i + 1}</span>
          <span id="fan-badge-${i}" style="font-size: 10px;" class="text-muted">Wait</span>
        </div>
        <div style="font-size: 20px; font-weight: 600;"><span id="fan-rpm-${i}">0</span> <span style="font-size: 11px; font-weight: 400;" class="text-muted">RPM</span></div>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px;">
          <span style="font-size: 10px; color: var(--color-text-secondary);">Target</span>
          <span style="font-size: 11px; font-weight: 600;"><span id="fan-pwm-${i}">0</span>%</span>
        </div>
        <div style="height: 20px; margin-top: 8px;">
          <canvas id="fan-chart-${i}"></canvas>
        </div>
      </div>
    `;
  }
  document.getElementById('dashboard-fans-strip').innerHTML = fansHtml;
  document.getElementById('fan-config-toggles').innerHTML = togglesHtml;

  // Bind Fan Toggles
  for (let i = 0; i < 6; i++) {
    document.getElementById(`toggle-fan-${i}`).addEventListener('change', (e) => {
      const isEnabled = e.target.checked;
      localStorage.setItem(`fan-enabled-${i}`, isEnabled);
      document.getElementById(`fan-card-${i}`).style.display = isEnabled ? 'block' : 'none';
    });
  }

  // Bind Rename Controller
  const btnRename = document.getElementById('btn-rename-controller');
  const inputName = document.getElementById('controller-name');
  
  // Load saved name
  const savedName = localStorage.getItem('jbod-1-name');
  if (savedName) {
    inputName.value = savedName;
    const navText = document.getElementById('nav-dashboard-text');
    if (navText) navText.textContent = savedName;
  }

  btnRename.addEventListener('click', () => {
    const newName = inputName.value.trim() || 'JBOD 1 (Local)';
    localStorage.setItem('jbod-1-name', newName);
    const navText = document.getElementById('nav-dashboard-text');
    if (navText) {
      navText.textContent = newName;
      
      // Visual feedback
      const originalText = btnRename.innerText;
      btnRename.innerText = "Saved";
      setTimeout(() => btnRename.innerText = originalText, 1500);
    }
  });

  // Bind Poll Rate pip
  document.getElementById('pip-refresh-rate').addEventListener('change', (e) => {
    if (window.updatePollInterval) {
      window.updatePollInterval(parseInt(e.target.value, 10));
    }
    // Also sync the standard setting input if it exists
    const standardInput = document.getElementById('poll-interval');
    if (standardInput) standardInput.value = e.target.value;
  });

  // Bind PWM Override Toggle
  const btnCurveGlobal = document.getElementById('btn-curve-global');
  const btnCurveCustom = document.getElementById('btn-curve-custom');
  const localCurvesContainer = document.getElementById('local-pwm-curves-container');
  
  if (btnCurveGlobal && btnCurveCustom && localCurvesContainer) {
    window.setCurveMode = (mode) => {
      if (mode === 'global') {
        btnCurveGlobal.style.background = 'hsla(150, 60%, 45%, 0.2)';
        btnCurveGlobal.style.color = 'var(--color-success)';
        btnCurveCustom.style.background = 'transparent';
        btnCurveCustom.style.color = 'var(--color-text-primary)';
        localCurvesContainer.style.display = 'none';
      } else {
        btnCurveCustom.style.background = 'hsla(40, 90%, 50%, 0.2)';
        btnCurveCustom.style.color = 'var(--color-warning)';
        btnCurveGlobal.style.background = 'transparent';
        btnCurveGlobal.style.color = 'var(--color-text-primary)';
        localCurvesContainer.style.display = 'block';
      }
    };

    btnCurveGlobal.addEventListener('click', () => window.setCurveMode('global'));
    btnCurveCustom.addEventListener('click', () => window.setCurveMode('custom'));
  }

  // Generate Local Curves Inputs
  const localHddContainer = document.getElementById('local-hdd-curve-container');
  const localSsdContainer = document.getElementById('local-ssd-curve-container');
  const localProfileSelect = document.getElementById('local-curve-profile');
  
  if (localHddContainer && localSsdContainer && localProfileSelect) {
    const generateLocalCurveHTML = (type, temps, pwms) => {
      let tempsHtml = '';
      let pwmsHtml = '';
      for (let i = 0; i < 8; i++) {
        tempsHtml += `<input type="number" id="local-curve-${type}-temp-${i}" value="${temps[i]}" class="input-base" style="width: 100%; min-width: 40px; flex: 1; text-align: center; padding: 6px;">`;
        pwmsHtml += `<input type="number" id="local-curve-${type}-pwm-${i}" value="${pwms[i]}" class="input-base" style="width: 100%; min-width: 40px; flex: 1; text-align: center; padding: 6px;">`;
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
    };

    let isLocalCurvesRendered = false;

    const renderLocalCurves = (profileName) => {
      let p = CURVE_PROFILES[profileName];
      if (!p) return;

      if (!isLocalCurvesRendered) {
        localHddContainer.innerHTML = generateLocalCurveHTML('hdd', p.hddTemps, p.hddPwms);
        localSsdContainer.innerHTML = generateLocalCurveHTML('ssd', p.ssdTemps, p.ssdPwms);

        const inputs = [...localHddContainer.querySelectorAll('input'), ...localSsdContainer.querySelectorAll('input')];
        inputs.forEach(input => {
          input.addEventListener('input', () => {
            localProfileSelect.value = 'Custom';
          });
        });
        isLocalCurvesRendered = true;
      } else {
        // Direct value update to avoid browser state restoration on id
        for(let i = 0; i < 8; i++) {
          document.getElementById(`local-curve-hdd-temp-${i}`).value = p.hddTemps[i];
          document.getElementById(`local-curve-hdd-pwm-${i}`).value = p.hddPwms[i];
          document.getElementById(`local-curve-ssd-temp-${i}`).value = p.ssdTemps[i];
          document.getElementById(`local-curve-ssd-pwm-${i}`).value = p.ssdPwms[i];
        }
      }
    };

    renderLocalCurves(localProfileSelect.value);

    localProfileSelect.addEventListener('change', (e) => {
      if (e.target.value !== 'Custom') {
        renderLocalCurves(e.target.value);
      }
    });
  }

  // Bind Save Local Overrides
  const btnSavePwm = document.getElementById('btn-save-local-pwm');
  const statusPwm = document.getElementById('local-pwm-status');
  if (btnSavePwm) {
    btnSavePwm.addEventListener('click', () => {
      if (statusPwm) {
        statusPwm.style.opacity = '1';
        setTimeout(() => { statusPwm.style.opacity = '0'; }, 2000);
      }
    });
  }

  // Bind Delete Controller
  const btnDeleteController = document.getElementById('btn-delete-controller');
  if (btnDeleteController) {
    btnDeleteController.addEventListener('click', async () => {
      if (!activeControllerId) return;
      if (!confirm('Are you sure you want to permanently delete this controller? This action cannot be undone.')) return;
      
      btnDeleteController.disabled = true;
      btnDeleteController.textContent = 'Deleting...';
      try {
        await api.deleteController(activeControllerId);
        // On success, simply reload the page or trigger a full state refresh
        window.location.reload();
      } catch (err) {
        alert(err.message);
        btnDeleteController.disabled = false;
        btnDeleteController.textContent = 'Delete Controller';
      }
    });
  }

  // Initialize Sub-components
  initDriveTable(document.getElementById('drive-table-container'));
  initSerialTools(document.getElementById('serial-tools-container'));
  initLogs(document.getElementById('logs-container'));
  initLinkUpdates(document.getElementById('updates-container'));

  // Initialize Placeholder Chart
  const ctx = document.getElementById('historyChart').getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['10:00', '10:05', '10:10', '10:15', '10:20', '10:25'],
      datasets: [
        { label: 'HDD Avg °C', data: [30, 31, 31, 32, 33, 33], borderColor: '#7dd3fc', tension: 0.3 },
        { label: 'SSD Avg °C', data: [38, 39, 41, 41, 42, 42], borderColor: '#d8b4fe', tension: 0.3 },
        { label: 'PWM %', data: [30, 35, 45, 50, 60, 60], borderColor: '#f97316', tension: 0.3, yAxisID: 'y1' }
      ]
    },
    options: {
      interaction: { mode: 'index', intersect: false },
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { type: 'linear', display: true, position: 'left', title: { display: true, text: 'Temp (°C)' } },
        y1: { type: 'linear', display: true, position: 'right', title: { display: true, text: 'PWM %' }, grid: { drawOnChartArea: false } }
      }
    }
  });

  // Initialize Power Chart
  const pCtx = document.getElementById('powerChart').getContext('2d');
  new Chart(pCtx, {
    type: 'line',
    data: {
      labels: ['-5m', '-4m', '-3m', '-2m', '-1m', 'Now'],
      datasets: [
        { label: 'Total Power (W)', data: [120, 125, 118, 122, 130, 128], borderColor: '#10b981', backgroundColor: 'rgba(16, 185, 129, 0.1)', fill: true, tension: 0.3 }
      ]
    },
    options: {
      interaction: { mode: 'index', intersect: false },
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { type: 'linear', display: true, position: 'right', grid: { color: 'rgba(255, 255, 255, 0.05)' } }
      }
    }
  });

  // Tab Navigation Logic
  const tabDrives = document.getElementById('btn-tab-drives');
  const tabConfig = document.getElementById('btn-tab-config');
  const contentDrives = document.getElementById('tab-drives-content');
  const contentConfig = document.getElementById('tab-config-content');

  tabDrives.addEventListener('click', () => {
    tabDrives.classList.add('active');
    tabConfig.classList.remove('active');
    contentDrives.style.display = 'block';
    contentConfig.style.display = 'none';
  });

  tabConfig.addEventListener('click', () => {
    tabConfig.classList.add('active');
    tabDrives.classList.remove('active');
    contentConfig.style.display = 'block';
    contentDrives.style.display = 'none';
  });
}



export function updateDashboardData(data, activeControllerId) {
  const emptyState = document.getElementById('empty-state-content');
  const tabDrives = document.getElementById('tab-drives-content');
  const tabConfig = document.getElementById('tab-config-content');
  const dashTabs = document.querySelector('.dash-tabs');

  if (!data || !data.controllers || data.controllers.length === 0 || !activeControllerId) {
    if (emptyState) emptyState.style.display = 'flex';
    if (tabDrives) tabDrives.style.display = 'none';
    if (tabConfig) tabConfig.style.display = 'none';
    if (dashTabs) dashTabs.style.display = 'none';
    return;
  }

  const controller = data.controllers.find(c => c.id === activeControllerId);
  if (!controller) return;

  if (emptyState) emptyState.style.display = 'none';
  if (dashTabs) dashTabs.style.display = 'flex';
  
  // Only show the active tab content
  const tabDrivesBtn = document.getElementById('btn-tab-drives');
  if (tabDrivesBtn && tabDrivesBtn.classList.contains('active')) {
    if (tabDrives) tabDrives.style.display = 'block';
  } else {
    if (tabConfig) tabConfig.style.display = 'block';
  }

  const metrics = controller.telemetry || {};
  const fans = metrics.fans || [];

  // Hide or show elements based on controller type
  const isDIY = controller.type === 'diy';
  const pip12v = document.getElementById('pip-container-12v');
  const pipThermal = document.getElementById('pip-container-thermal');
  const pipHealth = document.getElementById('pip-container-health');
  
  if (pip12v) pip12v.style.display = isDIY ? 'none' : 'flex';
  if (pipThermal) pipThermal.style.display = isDIY ? 'none' : 'flex';
  if (pipHealth) pipHealth.style.display = isDIY ? 'none' : 'flex';

  const volts = (metrics.bus_v || 0);
  const amps = (metrics.current_a || 0);
  const totalWatts = (volts * amps).toFixed(1);

  const heroWatts = document.getElementById('hero-watts');
  const heroAmps = document.getElementById('hero-amps');
  const heroVolts = document.getElementById('hero-volts');
  
  if (heroWatts) heroWatts.textContent = isDIY ? '-- W' : `\${totalWatts} W`;
  if (heroAmps) heroAmps.textContent = isDIY ? '-- A' : `\${amps.toFixed(2)} A`;
  if (heroVolts) heroVolts.textContent = isDIY ? '-- V' : `\${volts.toFixed(2)} V`;

  for (let i = 0; i < 6; i++) {
    const fan = fans[i] || { rpm: 0, pwm_percent: 0, state: 'unknown' };
    const isStalled = fan.pwm_percent > 0 && fan.rpm === 0;
    
    const cardEl = document.getElementById(`fan-card-\${i}`);
    const badgeEl = document.getElementById(`fan-badge-\${i}`);
    
    const rpmEl = document.getElementById(`fan-rpm-\${i}`);
    if (rpmEl) rpmEl.textContent = isDIY ? '--' : fan.rpm;
    
    const pwmEl = document.getElementById(`fan-pwm-\${i}`);
    if (pwmEl) pwmEl.textContent = fan.pwm_percent;
    
    if (!badgeEl) continue;

    if (isDIY) {
      badgeEl.innerHTML = '<span style="color: var(--color-success)">OK</span>';
      cardEl.style.borderColor = 'hsla(150, 60%, 45%, 0.3)';
    } else {
      if (fan.rpm > 0) {
        badgeEl.innerHTML = '<span style="color: var(--color-success)">Running</span>';
        cardEl.style.borderColor = 'hsla(150, 60%, 45%, 0.3)';
      } else if (isStalled) {
        badgeEl.innerHTML = '<span style="color: var(--color-error); font-weight: bold;">STALLED</span>';
        cardEl.style.borderColor = 'var(--color-error)';
      } else {
        badgeEl.innerHTML = '<span class="text-muted">Offline</span>';
        cardEl.style.borderColor = 'var(--glass-border)';
      }
    }
  }
}
