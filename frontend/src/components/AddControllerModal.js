import { api } from '../api.js';

const CONTROLLER_NAME_MAX = 24;

export function createAddControllerModal(onAdded) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center;
    z-index: 9999; backdrop-filter: blur(4px);
  `;

  const modal = document.createElement('div');
  modal.style.cssText = `
    width: 400px; padding: 32px; background: var(--color-bg-surface);
    border-radius: 16px; border: 1px solid var(--color-border);
    box-shadow: 0 16px 48px rgba(0,0,0,0.4);
  `;

  modal.innerHTML = `
    <h2 style="margin-top: 0; margin-bottom: 24px; font-size: 24px; color: var(--color-text-primary);">Add Controller</h2>

    <div class="form-group" style="margin-bottom: 20px;">
      <label style="display: block; margin-bottom: 8px; color: var(--color-text-secondary); font-size: 14px;">Controller Name</label>
      <input type="text" id="add-ctrl-name" placeholder="e.g. JBOD 1" maxlength="${CONTROLLER_NAME_MAX}" autocomplete="off" style="width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-inset); color: var(--color-text-primary); font-size: 14px; outline: none;">
    </div>

    <div class="form-group" style="margin-bottom: 24px;">
      <label style="display: block; margin-bottom: 8px; color: var(--color-text-secondary); font-size: 14px;">Port & Type Auto-Detect</label>
      <select id="add-ctrl-port" style="width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-inset); color: var(--color-text-primary); font-size: 14px; outline: none;">
        <option value="">Scanning ports...</option>
      </select>
      <div id="add-ctrl-identity" style="margin-top: 8px; min-height: 18px; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5;"></div>
    </div>

    <div style="display: flex; gap: 12px; justify-content: space-between;">
      <div style="display: flex; gap: 8px;">
        <button id="add-ctrl-scan" class="btn btn-secondary" style="border: 1px solid var(--color-border); background: transparent; color: var(--color-text-primary); padding: 8px 16px; border-radius: 6px; cursor: pointer;">Scan</button>
        <button id="add-ctrl-identify" class="btn btn-secondary" style="border: 1px solid var(--color-border); background: transparent; color: var(--color-text-primary); padding: 8px 16px; border-radius: 6px; cursor: not-allowed; opacity: 0.5;" disabled>Identify</button>
      </div>
      <div style="display: flex; gap: 12px;">
        <button id="add-ctrl-cancel" class="btn btn-secondary" style="border: 1px solid var(--color-border); background: transparent; color: var(--color-text-primary); padding: 8px 16px; border-radius: 6px; cursor: pointer;">Cancel</button>
        <button id="add-ctrl-submit" class="btn btn-primary" style="background: var(--color-accent); color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; opacity: 0.5;" disabled>Add Controller</button>
      </div>
    </div>
  `;

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const nameInput = modal.querySelector('#add-ctrl-name');
  const portSelect = modal.querySelector('#add-ctrl-port');
  const submitBtn = modal.querySelector('#add-ctrl-submit');
  const cancelBtn = modal.querySelector('#add-ctrl-cancel');
  const scanBtn = modal.querySelector('#add-ctrl-scan');
  const identifyBtn = modal.querySelector('#add-ctrl-identify');
  const identityInfo = modal.querySelector('#add-ctrl-identity');

  let portsData = [];
  let autoSuggestedName = '';

  const updateSubmitState = () => {
    const port = portSelect.value;
    const selectedPortData = portsData.find(p => p.port === port);
    const type = selectedPortData ? selectedPortData.type : 'unknown';

    const isValidType = type === 'official' || type === 'diy';
    const isReady = nameInput.value.trim() && port && isValidType && !selectedPortData?.configured_controller_id;

    submitBtn.disabled = !isReady;
    submitBtn.style.opacity = isReady ? '1' : '0.5';
    submitBtn.style.cursor = isReady ? 'pointer' : 'not-allowed';

    const canIdentify = Boolean(
      selectedPortData?.identify_supported
      && !selectedPortData?.configured_controller_id
    );
    identifyBtn.disabled = !canIdentify;
    identifyBtn.style.opacity = canIdentify ? '1' : '0.5';
    identifyBtn.style.cursor = canIdentify ? 'pointer' : 'not-allowed';

    if (!selectedPortData) {
      identityInfo.textContent = '';
    } else if (selectedPortData.hardware_uid) {
      const assigned = selectedPortData.configured_controller_id
        ? ` · Already assigned to ${selectedPortData.configured_controller_id}`
        : '';
      const identifyNote = selectedPortData.type === 'diy' && !selectedPortData.identify_supported
        ? ' · LED identify requires firmware 2.5.0'
        : '';
      identityInfo.textContent = `Persistent hardware ID: ${selectedPortData.hardware_uid}${assigned}${identifyNote}`;
    } else if (isValidType) {
      identityInfo.textContent = 'Legacy firmware: this controller remains tied to its USB path until firmware 2.4.0 is installed.';
    } else {
      identityInfo.textContent = 'This device did not identify as a FanBridge controller.';
    }
  };

  nameInput.addEventListener('input', () => {
    if (nameInput.value !== autoSuggestedName) autoSuggestedName = '';
    updateSubmitState();
  });
  portSelect.addEventListener('change', () => {
    const selected = portsData.find(p => p.port === portSelect.value);
    if (!nameInput.value.trim() || nameInput.value === autoSuggestedName) {
      autoSuggestedName = selected?.suggested_name || '';
      nameInput.value = autoSuggestedName;
    }
    updateSubmitState();
  });

  cancelBtn.addEventListener('click', () => {
    overlay.remove();
  });

  submitBtn.addEventListener('click', async () => {
    const port = portSelect.value;
    const selectedPortData = portsData.find(p => p.port === port);
    const type = selectedPortData ? selectedPortData.type : 'unknown';
    let id = nameInput.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    if (!/^[a-z]/.test(id)) id = `controller_${id || 'new'}`;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Adding...';

    try {
      await api.addController({
        id: id,
        name: nameInput.value.trim(),
        port: port,
        type: type,
        baud: 115200
      });
      overlay.remove();
      if (onAdded) onAdded();
    } catch (err) {
      alert(err.message);
      submitBtn.disabled = false;
      submitBtn.textContent = 'Add Controller';
    }
  });

  identifyBtn.addEventListener('click', async () => {
    const selected = portsData.find(p => p.port === portSelect.value);
    if (!selected?.identify_supported || selected.configured_controller_id) return;
    identifyBtn.disabled = true;
    identifyBtn.textContent = 'Starting…';
    try {
      const result = await api.identifyPort(selected.port);
      const seconds = Math.round(Number(result.duration_ms || 10000) / 1000);
      identityInfo.textContent = `Flashing ${selected.suggested_name || 'selected controller'} for ${seconds} seconds.`;
    } catch (err) {
      identityInfo.textContent = err.message || 'Could not identify this controller.';
    } finally {
      identifyBtn.textContent = 'Identify';
      updateSubmitState();
    }
  });

  const scanPorts = async () => {
    scanBtn.disabled = true;
    identifyBtn.disabled = true;
    scanBtn.textContent = 'Scanning...';
    portSelect.innerHTML = '<option value="">Scanning ports...</option>';
    identityInfo.textContent = '';
    try {
      const res = await api.getPorts();
      portsData = res.ports || [];
      portSelect.innerHTML = '';
      if (portsData.length === 0) {
        portSelect.innerHTML = '<option value="">No ports detected</option>';
        updateSubmitState();
        return;
      }
      portSelect.innerHTML = '<option value="">Select a port...</option>';
      portsData.forEach(p => {
        const typeLabel = p.suggested_name || (p.type === 'official' ? 'FanBridge-Link' : (p.type === 'diy' ? 'DIY-RP2040' : 'Unknown device'));
        const configuredLabel = p.configured_controller_id ? ` · Assigned to ${p.configured_controller_id}` : '';
        const opt = document.createElement('option');
        opt.value = p.port;
        opt.disabled = Boolean(p.configured_controller_id);
        opt.textContent = `${typeLabel} — ${p.port}${configuredLabel}`;
        portSelect.appendChild(opt);
      });
      updateSubmitState();
    } catch (err) {
      portsData = [];
      portSelect.innerHTML = '<option value="">Error scanning ports</option>';
      updateSubmitState();
    } finally {
      scanBtn.disabled = false;
      scanBtn.textContent = 'Scan';
    }
  };

  scanBtn.addEventListener('click', scanPorts);
  scanPorts();
}
