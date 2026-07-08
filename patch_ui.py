import os

html_path = 'container/templates/index.html'
with open(html_path, 'r') as f:
    content = f.read()

# 1. Add Chart.js to head
head_inject = """
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    /* Premium dark mode glassmorphism */
    :root {
      --bg: #0f172a;
      --fg: #e2e8f0;
      --cardbg: rgba(30, 41, 59, 0.7);
      --pillbg: rgba(51, 65, 85, 0.5);
      --border: rgba(255, 255, 255, 0.1);
      --accent: #38bdf8;
    }
    body {
      background: linear-gradient(135deg, #0f172a, #1e1b4b);
      color: var(--fg);
      font-family: 'Inter', system-ui, sans-serif;
    }
    .panel, .serial-box, .card, .modal-content {
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
      border: 1px solid rgba(255, 255, 255, 0.18);
    }
    .pill, button {
      transition: all 0.2s ease-in-out;
    }
    .pill:hover, button:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }
    .chart-container {
      width: 100%;
      max-width: 905px;
      margin: 20px 0;
      padding: 15px;
      background: var(--cardbg);
      border-radius: 8px;
      border: 1px solid var(--border);
    }
  </style>
</head>"""
content = content.replace('</head>', head_inject)

# 2. Add Chart canvas
chart_html = """
  <div class="chart-container">
    <canvas id="historyChart" height="100"></canvas>
  </div>
  <table id="tbl">
"""
content = content.replace('<table id="tbl">', chart_html)

# 3. Add polling for history
js_inject = """
    function startPolling(){
      if (pollTimer) clearInterval(pollTimer);
      if (pollTimerSerial) clearInterval(pollTimerSerial);
      const ms = Math.max(3, Math.min(60, pollSeconds))*1000;
      // Replaced normal polling with SSE
      if (window.evtSource) window.evtSource.close();
      window.evtSource = new EventSource('/api/stream');
      window.evtSource.onmessage = function(e) {
         // SSE updates
         refresh(JSON.parse(e.data));
         refreshSerial();
      };
      
      // Still fetch history periodically
      setInterval(fetchHistory, 30000);
      fetchHistory();
      
      const pb = document.getElementById('pollbtn');
      if (pb) pb.textContent = `live updates: SSE`;
      if (pb) pb.title = 'SSE stream active';
    }
    
    let historyChart = null;
    async function fetchHistory() {
      try {
        const res = await fetch('/api/history?hours=1');
        const j = await res.json();
        if(j.ok && j.history) {
           renderChart(j.history);
        }
      } catch(e) {
        console.error("history err", e);
      }
    }
    
    function renderChart(historyData) {
      const ctx = document.getElementById('historyChart').getContext('2d');
      const labels = historyData.map(d => new Date(d.ts * 1000).toLocaleTimeString());
      const hddData = historyData.map(d => d.hdd_avg);
      const ssdData = historyData.map(d => d.ssd_avg);
      const pwmData = historyData.map(d => d.pwm);
      
      if(historyChart) {
         historyChart.data.labels = labels;
         historyChart.data.datasets[0].data = hddData;
         historyChart.data.datasets[1].data = ssdData;
         historyChart.data.datasets[2].data = pwmData;
         historyChart.update();
         return;
      }
      
      historyChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [
            { label: 'HDD Avg (°C)', data: hddData, borderColor: '#38bdf8', backgroundColor: 'rgba(56, 189, 248, 0.1)', fill: true, tension: 0.4 },
            { label: 'SSD Avg (°C)', data: ssdData, borderColor: '#a78bfa', backgroundColor: 'rgba(167, 139, 250, 0.1)', fill: true, tension: 0.4 },
            { label: 'PWM %', data: pwmData, borderColor: '#f472b6', borderDash: [5, 5], tension: 0.4, yAxisID: 'y1' }
          ]
        },
        options: {
          responsive: true,
          interaction: { mode: 'index', intersect: false },
          scales: {
            y: { beginAtZero: false, title: { display: true, text: 'Temp °C' } },
            y1: { beginAtZero: true, max: 100, position: 'right', title: { display: true, text: 'PWM %' } }
          }
        }
      });
    }
"""
content = content.replace('function startPolling(){', js_inject + '\n    function _startPolling_old(){')

# Refactor refresh to accept data
content = content.replace('async function refresh() {', 'async function refresh(sseData) {')
content = content.replace("const r = await fetchWithTimeout('/api/status'", "// const r = await fetchWithTimeout('/api/status'")
content = content.replace("const j = await r.json();", "const j = sseData || await (await fetch('/api/status')).json();")

with open(html_path, 'w') as f:
    f.write(content)

print("UI Patched successfully")
