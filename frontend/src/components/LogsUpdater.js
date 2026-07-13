export function initLogs(container) {
  container.innerHTML = `
    <div class="glass-card" style="margin-top: 24px;">
      <h3 style="margin:0 0 16px; display:flex; justify-content: space-between; align-items:center;">
        <span>Controller Logs</span>
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

      <div id="logbox" style="height: 220px; overflow:auto; font-family: ui-monospace, monospace; font-size:12px; background: var(--color-bg-inset); border:1px solid var(--glass-border); border-radius: 8px; padding: 12px; white-space: pre-wrap; color: var(--color-text-primary);">
        <span style="color: var(--color-text-muted);">[INFO] Controller Initialized</span><br>
        <span style="color: var(--color-text-muted);">[INFO] Waiting for telemetry stream...</span>
      </div>
    </div>
  `;
}

export function initLinkUpdates(container) {
  container.innerHTML = `
    <div class="glass-card" style="margin-top: 24px;">
      <h3 style="margin:0 0 16px; display:flex; justify-content: space-between; align-items:center;">
        <span>Link Updates & Firmware</span>
      </h3>
      
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px;">
        <div>
          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">Device Information</h4>
          <div style="font-size: 12px; display: flex; flex-direction: column; gap: 6px; color: var(--color-text-muted);">
            <div>Port: <span id="rpPort" style="color: var(--color-text-primary);">/dev/ttyACM0</span></div>
            <div>USB: <span id="rpUsb" style="color: var(--color-text-primary);">1-1.3</span></div>
            <div>Manufacturer: <span id="rpMan" style="color: var(--color-text-primary);">Raspberry Pi</span></div>
            <div>Controller version: <span id="rpVer" style="color: var(--color-text-primary);">1.0.3</span></div>
          </div>
        </div>

        <div>
          <h4 style="margin: 0 0 8px; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 1px; font-size: 11px;">Firmware Management</h4>
          <div style="display: flex; gap: 8px; margin-bottom: 12px;">
            <input type="text" id="rpRepoUrl" class="input-base" placeholder="https://.../manifest.json base" style="flex: 1; font-size: 12px;" />
            <button class="btn" style="font-size: 12px;">Save URL</button>
          </div>
          
          <div style="font-size: 12px; color: var(--color-text-muted); margin-bottom: 16px;">
            Latest Available: <span id="rpLatest" style="color: var(--color-text-primary);">1.0.5</span>
          </div>

          <div style="display: flex; gap: 8px; align-items: center;">
            <button class="btn btn-primary" id="rpFlashLatest" style="font-size: 12px;">Install Latest</button>
            <span style="font-size: 12px; color: var(--color-text-muted);">or</span>
            <label class="btn" style="font-size: 12px; cursor: pointer;">
              <input type="file" id="rpUf2File" accept=".uf2" style="display:none;" />
              Upload .uf2
            </label>
          </div>
        </div>
      </div>
    </div>
  `;
}
