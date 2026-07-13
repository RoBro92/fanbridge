export function initDriveTable(container) {
  container.innerHTML = `
    <div class="glass-card" style="margin-top: 24px;">
      <h3 style="margin:0 0 16px; display:flex; justify-content: space-between; align-items:center;">
        <span>Assigned Drives</span>
        <button class="btn" style="font-size: 12px; padding: 4px 12px;">Rescan Drives</button>
      </h3>
      
      <div style="overflow-x: auto;">
        <table style="width: 100%; border-collapse: collapse; font-size: 14px; text-align: left;">
          <thead>
            <tr style="border-bottom: 1px solid var(--color-border);">
              <th style="padding: 12px 8px; color: var(--color-text-secondary); font-weight: 500;">Device</th>
              <th style="padding: 12px 8px; color: var(--color-text-secondary); font-weight: 500;">Type</th>
              <th style="padding: 12px 8px; color: var(--color-text-secondary); font-weight: 500;">State</th>
              <th style="padding: 12px 8px; color: var(--color-text-secondary); font-weight: 500;">Temp (°C)</th>
            </tr>
          </thead>
          <tbody id="drive-rows">
            <!-- Simulated rows until API is fully wired -->
            <tr style="border-bottom: 1px solid var(--glass-border);">
              <td style="padding: 12px 8px;">/dev/sda</td>
              <td style="padding: 12px 8px;"><span style="background: hsla(200, 50%, 50%, 0.2); color: #7dd3fc; padding: 2px 6px; border-radius: 4px; font-size: 11px;">HDD</span></td>
              <td style="padding: 12px 8px;">Active</td>
              <td style="padding: 12px 8px;">34°C</td>
            </tr>
            <tr style="border-bottom: 1px solid var(--glass-border);">
              <td style="padding: 12px 8px;">/dev/nvme0n1</td>
              <td style="padding: 12px 8px;"><span style="background: hsla(280, 50%, 50%, 0.2); color: #d8b4fe; padding: 2px 6px; border-radius: 4px; font-size: 11px;">SSD</span></td>
              <td style="padding: 12px 8px;">Standby</td>
              <td style="padding: 12px 8px;">42°C</td>
            </tr>
          </tbody>
        </table>
      </div>
      
      <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--glass-border); display: flex; justify-content: space-between; font-size: 12px; color: var(--color-text-secondary);">
        <span>HDD Avg/Min/Max: <strong style="color: var(--color-text-primary);">34 / 32 / 36</strong></span>
        <span>SSD Avg/Min/Max: <strong style="color: var(--color-text-primary);">42 / 41 / 44</strong></span>
        <span>Recommended PWM: <strong style="color: var(--color-accent); font-size: 14px;">50%</strong></span>
      </div>
    </div>
  `;
}
