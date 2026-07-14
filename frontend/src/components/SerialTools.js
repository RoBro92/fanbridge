import { api } from '../api.js';

export function initSerialTools(container) {
  container.innerHTML = `
    <div class="glass-card" style="width: 100%; height: 100%; display: flex; flex-direction: column;">
      <div style="display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 16px; flex-wrap: wrap;">
        <h3 style="margin: 0; display: flex; align-items: center; gap: 10px;">Serial Tools</h3>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--color-text-secondary); font-weight: 600;">Control Mode</span>
          <div style="display: flex; background: var(--color-bg-inset); border: 1px solid var(--glass-border); border-radius: 6px; overflow: hidden;">
            <button class="btn" id="btn-mode-auto" style="border: none; border-radius: 0; background: hsla(150, 60%, 45%, 0.2); color: var(--color-success); font-weight: 600; padding: 4px 16px;">AUTO</button>
            <button class="btn" id="btn-mode-manual" style="border: none; border-radius: 0; background: transparent; color: var(--color-text-secondary); font-weight: 600; padding: 4px 16px;">MANUAL</button>
          </div>
        </div>
      </div>
      <div style="display: flex; flex-direction: column; gap: 16px; flex: 1;">
        <div>
          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">Quick Commands</h4>
          <div style="display: flex; gap: 8px; flex-wrap: wrap;">
            <button class="btn" id="btnPing">PING</button>
            <button class="btn" id="btnTest">TEST</button>
            <button class="btn" id="btnVer">VERSION</button>
            <button class="btn" id="btnStat">STATUS</button>
          </div>
        </div>
        <div>
          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">PWM Test</h4>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
            <input type="range" id="pwmRange" min="0" max="100" value="0" style="flex: 1;" />
            <span class="text-muted" style="font-size: 14px;"><strong id="pwmVal">0</strong>%</span>
            <button class="btn" id="btnPwmSend">Apply</button>
            <button class="btn" id="btnPwmZero">Zero</button>
          </div>
        </div>

        <p id="serial-action-status" class="text-muted" role="status" aria-live="polite" style="font-size: 12px; margin: auto 0 0;">
          Commands and confirmed controller replies appear in the Controller Console below.
        </p>
        <p style="font-size: 11px; margin: 6px 0 0; color: var(--color-warning);">
          Manual mode bypasses fan curves. Thermal, stale-data and control-health safety overrides remain active and force 100%.
        </p>
      </div>
    </div>
  `;

  const actionStatus = document.getElementById('serial-action-status');
  const setActionStatus = (message, level = 'info') => {
    if (!actionStatus) return;
    actionStatus.textContent = message;
    actionStatus.style.color = level === 'error'
      ? 'var(--color-error)'
      : (level === 'success' ? 'var(--color-success)' : 'var(--color-text-muted)');
  };
  const refreshControllerConsole = () => window.refreshControllerLogs?.(true);
  const activeController = () => String(window.activeControllerId || '');
  let selectedController = '';
  let currentMode = 'auto';
  const confirmManualRisk = (value = null) => {
    const outputWarning = value === 0
      ? 'You are about to request 0% fan output. '
      : '';
    return window.confirm(
      `${outputWarning}Manual mode bypasses temperature curves and can overheat or damage disks. `
      + 'FanBridge will force 100% if an assigned disk reaches its critical threshold, temperature data is missing or stale, or the control loop becomes unhealthy. '
      + 'The DIY controller has no tachometer feedback, so FanBridge cannot verify that the physical fan is spinning. Continue?',
    );
  };
  window.setSerialToolsController = (cid) => {
    const next = String(cid || '');
    if (next === selectedController) return;
    selectedController = next;
    setActionStatus(next
      ? 'Commands and confirmed controller replies appear in the Controller Console below.'
      : 'Select a controller to use serial tools.');
  };
  window.setManualPwmValue = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) return;
    if (document.activeElement !== pwmRange) pwmRange.value = String(Math.round(parsed));
    if (pwmVal) pwmVal.textContent = String(Math.round(parsed));
  };

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
    currentMode = mode === 'auto' ? 'auto' : 'manual';
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

  btnAuto.addEventListener('click', async () => {
    const cid = activeController();
    if (!cid) return setActionStatus('Select a controller first.', 'error');
    try {
      await api.setAutoApply(cid, true);
      window.setControllerMode('auto');
      setActionStatus('Automatic PWM control enabled.', 'success');
      refreshControllerConsole();
    } catch (error) {
      setActionStatus(error.message || 'Could not enable automatic control.', 'error');
    }
  });
  btnManual.addEventListener('click', async () => {
    const cid = activeController();
    if (!cid) return setActionStatus('Select a controller first.', 'error');
    if (currentMode !== 'manual' && !confirmManualRisk()) return;
    try {
      await api.setAutoApply(cid, false);
      window.setControllerMode('manual');
      setActionStatus('Manual control enabled; temperature curves will not write this controller.', 'success');
      refreshControllerConsole();
    } catch (error) {
      setActionStatus(error.message || 'Could not enter manual mode.', 'error');
    }
  });

  const diagnosticButtons = {
    btnPing: 'PING',
    btnVer: 'VERSION',
    btnStat: 'STATUS',
  };
  Object.entries(diagnosticButtons).forEach(([buttonId, command]) => {
    document.getElementById(buttonId)?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      const cid = activeController();
      if (!cid) {
        setActionStatus('Select a controller first.', 'error');
        return;
      }
      button.disabled = true;
      setActionStatus(`Sending ${command}…`);
      try {
        await api.serialSend(cid, command);
        setActionStatus(`${command} was confirmed by the controller and logged.`, 'success');
      } catch (error) {
        setActionStatus(error.message || `${command} failed.`, 'error');
      } finally {
        button.disabled = false;
        refreshControllerConsole();
      }
    });
  });

  document.getElementById('btnTest')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const cid = activeController();
    if (!cid || !window.confirm('Run the controller fan test? Fan output will change during the test sequence.')) return;
    button.disabled = true;
    setActionStatus('Sending TEST…');
    try {
      await api.serialTest(cid);
      setActionStatus('TEST was confirmed by the controller and logged.', 'success');
    } catch (error) {
      setActionStatus(error.message || 'Fan test failed.', 'error');
    } finally {
      button.disabled = false;
      refreshControllerConsole();
    }
  });

  document.getElementById('btnPwmSend')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const cid = activeController();
    if (!cid) return setActionStatus('Select a controller first.', 'error');
    const requestedValue = Number(pwmRange?.value || 0);
    if ((currentMode !== 'manual' || requestedValue === 0) && !confirmManualRisk(requestedValue)) return;
    button.disabled = true;
    try {
      const result = await api.serialPwm(cid, requestedValue);
      window.setControllerMode('manual');
      setActionStatus(
        result.safety_override
          ? `Safety override: ${result.requested_value}% was requested; the controller confirmed 100%.`
          : `PWM ${result.value}% was confirmed by the controller and logged.`,
        result.safety_override ? 'error' : 'success',
      );
    } catch (error) {
      setActionStatus(error.message || 'PWM command failed.', 'error');
    } finally {
      button.disabled = false;
      refreshControllerConsole();
    }
  });

  document.getElementById('btnPwmZero')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const cid = activeController();
    if (!cid) return setActionStatus('Select a controller first.', 'error');
    if (!confirmManualRisk(0)) return;
    if (pwmRange) pwmRange.value = 0;
    if (pwmVal) pwmVal.textContent = '0';
    button.disabled = true;
    try {
      const result = await api.serialPwm(cid, 0);
      window.setControllerMode('manual');
      setActionStatus(
        result.safety_override
          ? 'Safety override: 0% was requested; the controller confirmed 100%.'
          : `PWM ${result.value}% was confirmed by the controller and logged.`,
        result.safety_override ? 'error' : 'success',
      );
    } catch (error) {
      setActionStatus(error.message || 'PWM command failed.', 'error');
    } finally {
      button.disabled = false;
      refreshControllerConsole();
    }
  });
}
