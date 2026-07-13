export function initSerialTools(container) {
  container.innerHTML = `
    <div class="glass-card" style="margin-top: 24px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3 style="margin: 0; display: flex; align-items: center; gap: 10px;">Serial Tools</h3>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--color-text-secondary); font-weight: 600;">Control Mode</span>
          <div style="display: flex; background: var(--color-bg-inset); border: 1px solid var(--glass-border); border-radius: 6px; overflow: hidden;">
            <button class="btn" id="btn-mode-auto" style="border: none; border-radius: 0; background: hsla(150, 60%, 45%, 0.2); color: var(--color-success); font-weight: 600; padding: 4px 16px;">AUTO</button>
            <button class="btn" id="btn-mode-manual" style="border: none; border-radius: 0; background: transparent; color: var(--color-text-secondary); font-weight: 600; padding: 4px 16px;">MANUAL</button>
          </div>
        </div>
      </div>
      <div style="display:grid; grid-template-columns: minmax(260px, 360px) 1fr; gap:24px;">
        <!-- Left: quick actions -->
        <div>
          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">Quick Commands</h4>
          <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px;">
            <button class="btn" id="btnPing">PING</button>
            <button class="btn" id="btnTest">TEST</button>
            <button class="btn" id="btnVer">VERSION</button>
            <button class="btn" id="btnStat">STATUS</button>
          </div>

          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">Send Raw Line</h4>
          <div style="display: flex; gap: 8px; margin-bottom: 24px;">
            <input type="text" id="serialLine" class="input-base" placeholder="Type a command e.g. 50" style="flex: 1;" />
            <button class="btn btn-primary" id="btnSendLine">Send</button>
          </div>

          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">PWM Test</h4>
          <div style="display: flex; align-items: center; gap: 12px;">
            <input type="range" id="pwmRange" min="0" max="100" value="0" style="flex: 1;" />
            <span class="text-muted" style="font-size: 14px;"><strong id="pwmVal">0</strong>%</span>
            <button class="btn" id="btnPwmSend">Apply</button>
            <button class="btn" id="btnPwmZero">Zero</button>
          </div>
        </div>

        <!-- Right: console output -->
        <div style="display: flex; flex-direction: column;">
          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">Console</h4>
          <div id="serialConsole" style="flex: 1; height: 260px; overflow:auto; font-family: ui-monospace, monospace; font-size:12px; background: var(--color-bg-inset); border:1px solid var(--glass-border); border-radius: 8px; padding: 12px; white-space: pre-wrap; color: var(--color-text-primary);"></div>
        </div>
      </div>
    </div>
  `;

  // Bind placeholder events
  const pwmRange = document.getElementById('pwmRange');
  const pwmVal = document.getElementById('pwmVal');
  if (pwmRange && pwmVal) {
    pwmRange.addEventListener('input', () => {
      pwmVal.textContent = pwmRange.value;
    });
  }

  // Control Mode Logic
  const btnAuto = document.getElementById('btn-mode-auto');
  const btnManual = document.getElementById('btn-mode-manual');
  
  window.setControllerMode = (mode) => {
    if (mode === 'auto') {
      btnAuto.style.background = 'hsla(150, 60%, 45%, 0.2)';
      btnAuto.style.color = 'var(--color-success)';
      btnManual.style.background = 'transparent';
      btnManual.style.color = 'var(--color-text-primary)';
      
      const pipMode = document.getElementById('pip-mode-status');
      if (pipMode) {
        pipMode.innerText = 'AUTO';
        pipMode.parentElement.classList.remove('pip-warning');
        pipMode.parentElement.classList.add('pip-success');
      }
    } else {
      btnManual.style.background = 'hsla(40, 90%, 50%, 0.2)';
      btnManual.style.color = 'var(--color-warning)';
      btnAuto.style.background = 'transparent';
      btnAuto.style.color = 'var(--color-text-primary)';
      
      const pipMode = document.getElementById('pip-mode-status');
      if (pipMode) {
        pipMode.innerText = 'MANUAL';
        pipMode.parentElement.classList.remove('pip-success');
        pipMode.parentElement.classList.add('pip-warning');
      }
    }
  };

  btnAuto.addEventListener('click', () => window.setControllerMode('auto'));
  btnManual.addEventListener('click', () => window.setControllerMode('manual'));

  // Trigger Manual mode automatically on PWM override
  document.getElementById('btnPwmSend')?.addEventListener('click', () => {
    window.setControllerMode('manual');
    // TODO: Send PWM command via API
  });
  
  document.getElementById('btnPwmZero')?.addEventListener('click', () => {
    if (pwmRange) pwmRange.value = 0;
    if (pwmVal) pwmVal.textContent = '0';
    window.setControllerMode('manual');
    // TODO: Send PWM 0 command via API
  });
}
