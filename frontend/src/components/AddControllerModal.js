import { api } from '../api.js';

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
      <input type="text" id="add-ctrl-name" placeholder="e.g. JBOD 1" style="width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-inset); color: var(--color-text-primary); font-size: 14px; outline: none;">
    </div>
    
    <div class="form-group" style="margin-bottom: 24px;">
      <label style="display: block; margin-bottom: 8px; color: var(--color-text-secondary); font-size: 14px;">Port & Type Auto-Detect</label>
      <select id="add-ctrl-port" style="width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-inset); color: var(--color-text-primary); font-size: 14px; outline: none;">
        <option value="">Scanning ports...</option>
      </select>
    </div>
    
    <div style="display: flex; gap: 12px; justify-content: flex-end;">
      <button id="add-ctrl-cancel" class="btn btn-secondary" style="border: 1px solid var(--color-border); background: transparent; color: var(--color-text-primary); padding: 8px 16px; border-radius: 6px; cursor: pointer;">Cancel</button>
      <button id="add-ctrl-submit" class="btn btn-primary" style="background: var(--color-accent); color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; opacity: 0.5;" disabled>Add Controller</button>
    </div>
  `;

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const nameInput = modal.querySelector('#add-ctrl-name');
  const portSelect = modal.querySelector('#add-ctrl-port');
  const submitBtn = modal.querySelector('#add-ctrl-submit');
  const cancelBtn = modal.querySelector('#add-ctrl-cancel');

  let portsData = [];

  const updateSubmitState = () => {
    const port = portSelect.value;
    const selectedPortData = portsData.find(p => p.port === port);
    const type = selectedPortData ? selectedPortData.type : 'unknown';
    
    const isValidType = type === 'official' || type === 'diy';
    const isReady = nameInput.value.trim() && port && isValidType;
    
    submitBtn.disabled = !isReady;
    submitBtn.style.opacity = isReady ? '1' : '0.5';
    submitBtn.style.cursor = isReady ? 'pointer' : 'not-allowed';
  };

  nameInput.addEventListener('input', updateSubmitState);
  portSelect.addEventListener('change', updateSubmitState);

  cancelBtn.addEventListener('click', () => {
    overlay.remove();
  });

  submitBtn.addEventListener('click', async () => {
    const port = portSelect.value;
    const selectedPortData = portsData.find(p => p.port === port);
    const type = selectedPortData ? selectedPortData.type : 'unknown';
    const id = nameInput.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_');
    
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

  // Fetch ports
  api.getPorts().then(res => {
    portsData = res.ports || [];
    portSelect.innerHTML = '';
    
    if (portsData.length === 0) {
      portSelect.innerHTML = '<option value="">No ports detected</option>';
      return;
    }
    
    portSelect.innerHTML = '<option value="">Select a port...</option>';
    portsData.forEach(p => {
      let typeLabel = p.type === 'official' ? 'Official FanBridge' : (p.type === 'diy' ? 'DIY RP2040' : 'Unknown Type');
      const opt = document.createElement('option');
      opt.value = p.port;
      opt.textContent = `${p.port} (${typeLabel})`;
      portSelect.appendChild(opt);
    });
  }).catch(err => {
    portSelect.innerHTML = '<option value="">Error scanning ports</option>';
  });
}
