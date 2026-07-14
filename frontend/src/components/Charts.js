import { Chart, registerables } from 'chart.js';

Chart.register(...registerables);

// Store chart instances
const fanCharts = {};

export function initFanChart(canvasId) {
  const ctx = document.getElementById(canvasId).getContext('2d');

  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [], // Timestamps
      datasets: [{
        label: 'RPM',
        data: [],
        borderColor: 'hsl(25, 95%, 53%)', // Accent color
        backgroundColor: 'hsla(25, 95%, 53%, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.4, // Smooth curves
        pointRadius: 0, // Hide points for a cleaner sparkline look
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 400,
        easing: 'easeOutQuart'
      },
      scales: {
        x: { display: false },
        y: {
          display: false,
          min: 0, // RPM always starts at 0
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false }
      },
      interaction: {
        intersect: false,
        mode: 'index',
      },
    }
  });

  fanCharts[canvasId] = chart;
  return chart;
}

export function updateFanChart(canvasId, historyData, fanIndex) {
  const chart = fanCharts[canvasId];
  if (!chart) return;

  const labels = [];
  const data = [];

  // Extract the RPM history for this specific fan
  for (const snapshot of historyData) {
    if (!snapshot || !snapshot.rp2040 || !snapshot.rp2040.fans) continue;
    labels.push(snapshot.timestamp || '');
    const rpm = snapshot.rp2040.fans[fanIndex]?.rpm || 0;
    data.push(rpm);
  }

  chart.data.labels = labels;
  chart.data.datasets[0].data = data;
  chart.update('none'); // Update without full animation to prevent stuttering on every poll
}
