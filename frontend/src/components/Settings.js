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
        
        <div style="overflow-x: auto; border: 1px solid var(--glass-border); border-radius: 8px;">
          <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
            <thead style="background: var(--color-bg-inset);">
              <tr>
                <th style="padding: 10px 12px; color: var(--color-text-secondary); font-weight: 500;">Device</th>
                <th style="padding: 10px 12px; color: var(--color-text-secondary); font-weight: 500;">Type</th>
                <th style="padding: 10px 12px; color: var(--color-text-secondary); font-weight: 500;">State</th>
                <th style="padding: 10px 12px; color: var(--color-text-secondary); font-weight: 500;">Temp (°C)</th>
                <th style="padding: 10px 12px; color: var(--color-text-secondary); font-weight: 500;">Assignment</th>
              </tr>
            </thead>
            <tbody>
              <tr style="border-bottom: 1px solid var(--glass-border);">
                <td style="padding: 10px 12px;">/dev/sda</td>
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
            <select class="input-base" style="font-size: 11px; padding: 4px 8px;">
              <option value="NORMAL">Normal</option>
              <option value="DEBUG">Debug</option>
            </select>
            <button class="btn" style="font-size: 11px; padding: 4px 8px;">Download</button>
            <button class="btn" style="font-size: 11px; padding: 4px 8px;">Clear</button>
          </div>
        </h3>
        
        <div style="display: flex; gap: 12px; margin-bottom: 16px; font-size: 12px;">
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" checked class="custom-checkbox" style="--checkbox-color: var(--color-error);"> ERROR</label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" checked class="custom-checkbox" style="--checkbox-color: var(--color-warning);"> WARNING</label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" checked class="custom-checkbox" style="--checkbox-color: #3b82f6;"> INFO</label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" class="custom-checkbox" style="--checkbox-color: var(--color-text-muted);"> DEBUG</label>
        </div>

        <div style="height: 150px; overflow-y: auto; background: var(--color-bg-inset); border: 1px solid var(--glass-border); border-radius: 8px; padding: 12px; font-family: ui-monospace, monospace; font-size: 11px; color: var(--color-text-primary); white-space: pre-wrap;">
<span style="color: var(--color-text-secondary);">[17:15:00] INFO: FanBridge Container Started</span>
<span style="color: var(--color-text-secondary);">[17:15:01] INFO: Loading disks.ini... Success (4 drives found)</span>
<span style="color: var(--color-text-secondary);">[17:15:02] INFO: Connecting to JBOD 1 (/dev/ttyUSB0)... Connected</span>
<span style="color: var(--color-warning);">[17:15:05] WARN: /dev/nvme0n1 approaching warning threshold (42°C)</span>
        </div>
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

    const cfg = res.config;
    document.getElementById('setting-min-interval').value = cfg.min_interval_s || 3;
    document.getElementById('setting-hysteresis').value = cfg.hysteresis_percent || 2;

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
    const settingsPayload = {
      min_interval_s: parseInt(document.getElementById('setting-min-interval').value || 3, 10),
      hysteresis_percent: parseInt(document.getElementById('setting-hysteresis').value || 2, 10)
    };

    const hddCurve = extractCurve('hdd');
    const ssdCurve = extractCurve('ssd');

    // Save
    await api.saveSettings(settingsPayload);
    await api.saveCurves({ hdd: hddCurve, ssd: ssdCurve });

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
