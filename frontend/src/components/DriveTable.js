export function initDriveTable(container) {
  container.innerHTML = `
    <div class="glass-card" style="margin-top: 24px;">
      <h3 style="margin:0 0 16px; display:flex; justify-content: space-between; align-items:center;">
        <span>Assigned Drives</span>
        <button class="btn" id="btn-rescan-drives" style="font-size: 12px; padding: 4px 12px;">Rescan Drives</button>
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
            <!-- Dynamically populated -->
          </tbody>
        </table>
      </div>
      
      <div id="drive-summary" style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--glass-border); display: flex; justify-content: space-between; font-size: 12px; color: var(--color-text-secondary);">
        <!-- Dynamically populated -->
      </div>
    </div>
  `;
}

export function updateDriveTable(data) {
  const tbody = document.getElementById('drive-rows');
  const summary = document.getElementById('drive-summary');
  if (!tbody || !summary) return;

  if (!data.drives || data.drives.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 16px; color: var(--color-text-secondary);">No drives assigned or detected</td></tr>';
    summary.innerHTML = '';
    return;
  }

  tbody.innerHTML = data.drives.map(d => {
    const isHDD = d.type === 'HDD';
    const typeColor = isHDD ? '#7dd3fc' : '#d8b4fe';
    const typeBg = isHDD ? 'hsla(200, 50%, 50%, 0.2)' : 'hsla(280, 50%, 50%, 0.2)';
    
    return `
      <tr style="border-bottom: 1px solid var(--glass-border);">
        <td style="padding: 12px 8px;">\${d.dev} \${d.slot ? '<span style="color:var(--color-text-secondary); font-size:11px;">(' + d.slot + ')</span>' : ''}</td>
        <td style="padding: 12px 8px;"><span style="background: \${typeBg}; color: \${typeColor}; padding: 2px 6px; border-radius: 4px; font-size: 11px;">\${d.type}</span></td>
        <td style="padding: 12px 8px;">\${d.state || 'Unknown'}</td>
        <td style="padding: 12px 8px;">\${d.temp !== null ? d.temp + '°C' : '--'}</td>
      </tr>
    `;
  }).join('');

  const formatStats = (stats) => {
    if (!stats || stats.cnt === 0) return 'N/A';
    return `\${stats.avg} / \${stats.min} / \${stats.max}`;
  };

  summary.innerHTML = `
    <span>HDD Avg/Min/Max: <strong style="color: var(--color-text-primary);">\${formatStats(data.hdd)}</strong></span>
    <span>SSD Avg/Min/Max: <strong style="color: var(--color-text-primary);">\${formatStats(data.ssd)}</strong></span>
    <span>Recommended PWM: <strong style="color: var(--color-accent); font-size: 14px;">\${data.recommended_pwm || 0}%</strong></span>
  `;
}
