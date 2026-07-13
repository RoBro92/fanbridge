import './styles/variables.css';
import './styles/layout.css';
import './styles/components.css';

import { api, initApi } from './api.js';
import { initDashboardContainer, updateDashboardData } from './components/Dashboard.js';
import { initFanChart, updateFanChart } from './components/Charts.js';
import { initSettingsContainer, loadSettings } from './components/Settings.js';
import { createAddControllerModal } from './components/AddControllerModal.js';

document.addEventListener('DOMContentLoaded', () => {
  const app = document.getElementById('app');
  
  // Read meta tags injected by Flask
  initApi();
  const pollInterval = parseInt(document.querySelector('meta[name="poll-interval"]')?.content || '7', 10);
  const version = document.querySelector('meta[name="app-version"]')?.content || 'dev';
  
  // Create shell with Sidebar layout
  app.innerHTML = `
    <div class="app-wrapper">
      <aside class="sidebar">
        <div class="sidebar-header" id="nav-logo-home" style="justify-content: center; padding: 24px 16px 16px; flex-direction: column; align-items: center; cursor: pointer; transition: opacity 0.2s;">
          <div style="display: flex; align-items: center; justify-content: center;">
            <img src="/static/fanbridge.png" alt="FanBridge Logo" style="width: 48px; height: 48px; object-fit: contain;">
            <h1 style="font-size: 24px; letter-spacing: -0.5px;">FanBridge</h1>
          </div>
        </div>
        <nav class="sidebar-nav">
          <div class="nav-section">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
              <div class="nav-label" style="margin-bottom: 0;">Controllers</div>
            </div>
            <div id="sidebar-controllers-list">
              <!-- Controllers rendered dynamically -->
            </div>
          </div>
          <div class="nav-section">
            <div class="nav-label">System</div>
            <a href="#" class="nav-item" id="nav-settings">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
              Settings
            </a>
            <a href="#" class="nav-item text-error" style="margin-top: auto; border-top: 1px solid var(--glass-border); padding-top: 16px;">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
              Logout
            </a>
            <div style="text-align: center; margin-top: 16px; padding-bottom: 8px;">
              <span class="text-muted" style="font-size: 11px; display: block; margin-bottom: 6px;">FanBridge v${version}</span>
              <span style="background: hsla(200, 50%, 50%, 0.15); color: #7dd3fc; border-radius: 4px; padding: 4px 10px; font-size: 10px; cursor: pointer; display: inline-block; font-weight: 600; letter-spacing: 0.5px; border: 1px solid hsla(200, 50%, 50%, 0.3);" title="Update Available">UPDATE AVAILABLE ↑</span>
            </div>
          </div>
        </nav>
      </aside>
      
      <main class="main-content">
        <header class="header" style="display: none;">
          <div class="header-brand">
            <h2 id="page-title">Dashboard</h2>
          </div>
          <div id="connection-status"></div>
        </header>
        
        <div id="dashboard-container"></div>
        <div id="settings-container" style="display: none;"></div>
      </main>
    </div>
  `;

  const dashboardContainer = document.getElementById('dashboard-container');
  const settingsContainer = document.getElementById('settings-container');
  const connectionStatus = document.getElementById('connection-status');
  
  // Apply Theme
  function applyTheme() {
    const savedTheme = localStorage.getItem('fanbridge-theme') || 'system';
    if (savedTheme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else if (savedTheme === 'dark') {
      document.documentElement.removeAttribute('data-theme');
    } else {
      // System
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        document.documentElement.setAttribute('data-theme', 'light');
      } else {
        document.documentElement.removeAttribute('data-theme');
      }
    }
  }
  applyTheme();

  // Expose applyTheme globally so Settings.js can trigger it
  window.applyTheme = applyTheme;
  
  // Initialize DOM
  initDashboardContainer(dashboardContainer);
  initSettingsContainer(settingsContainer);
  
  // Navigation State
  window.activeControllerId = null;
  const navSettings = document.getElementById('nav-settings');
  const pageTitle = document.getElementById('page-title');
  const sidebarControllersList = document.getElementById('sidebar-controllers-list');

  // Initialize Charts
  for(let i=0; i<6; i++) {
    initFanChart(`fan-chart-${i}`);
  }

  let sidebarRendered = false;

  function renderSidebar(controllers) {
    if (!controllers || controllers.length === 0) {
      if (!sidebarRendered) {
        sidebarControllersList.innerHTML = `
          <a href="#" class="nav-item active" id="nav-add-controller">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            Add Controller
          </a>
        `;
        document.getElementById('nav-add-controller').addEventListener('click', (e) => {
          e.preventDefault();
          createAddControllerModal(() => { pollStatus(); });
        });
        // Auto-open modal if no controllers on first load
        createAddControllerModal(() => { pollStatus(); });
        sidebarRendered = true;
      }
      return;
    }

    // Set default active if null
    if (!window.activeControllerId && controllers.length > 0) {
      window.activeControllerId = controllers[0].id;
    }

    let html = '';
    controllers.forEach(c => {
      const isActive = c.id === window.activeControllerId;
      html += `
        <a href="#" class="nav-item ${isActive ? 'active' : ''} controller-nav-item" data-id="${c.id}" data-name="${c.name}">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
          <span>${c.name}</span>
        </a>
      `;
    });

    html += `
      <a href="#" class="nav-item" id="nav-add-controller">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
        Add Controller
      </a>
    `;

    sidebarControllersList.innerHTML = html;
    sidebarRendered = true;

    // Bind clicks
    sidebarControllersList.querySelectorAll('.controller-nav-item').forEach(el => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        window.activeControllerId = el.getAttribute('data-id');
        
        // Update UI state
        document.querySelectorAll('.controller-nav-item').forEach(n => n.classList.remove('active'));
        el.classList.add('active');
        navSettings.classList.remove('active');
        
        dashboardContainer.style.display = 'block';
        settingsContainer.style.display = 'none';
        pageTitle.textContent = el.getAttribute('data-name') || 'Dashboard';
        
        // Immediate UI refresh if data is cached (pollStatus will catch up soon)
        pollStatus();
      });
    });

    document.getElementById('nav-add-controller').addEventListener('click', (e) => {
      e.preventDefault();
      createAddControllerModal(() => { pollStatus(); });
    });
  }

  const navLogoHome = document.getElementById('nav-logo-home');
  if (navLogoHome) {
    navLogoHome.addEventListener('click', (e) => {
      e.preventDefault();
      dashboardContainer.style.display = 'block';
      settingsContainer.style.display = 'none';
      navSettings.classList.remove('active');
      
      let activeName = 'Dashboard';
      document.querySelectorAll('.controller-nav-item').forEach(el => {
        if (el.getAttribute('data-id') === window.activeControllerId) {
          el.classList.add('active');
          activeName = el.getAttribute('data-name') || 'Dashboard';
        }
      });
      pageTitle.textContent = activeName;
    });
  }

  navSettings.addEventListener('click', (e) => {
    e.preventDefault();
    navSettings.classList.add('active');
    document.querySelectorAll('.controller-nav-item').forEach(n => n.classList.remove('active'));
    dashboardContainer.style.display = 'none';
    settingsContainer.style.display = 'block';
    pageTitle.textContent = 'Global Settings';
    loadSettings(); // Fetch fresh settings when opened
  });
  
  async function pollStatus() {
    try {
      const data = await api.getStatus();
      renderSidebar(data.controllers);
      updateDashboardData(data, window.activeControllerId);
      
      const serialPip = document.getElementById('pip-serial-status');
      if (serialPip) {
        serialPip.innerText = 'Connected';
        serialPip.parentElement.classList.remove('pip-error');
        serialPip.parentElement.classList.add('pip-success');
        serialPip.parentElement.style.color = '';
      }
      
      const lastUpdatePip = document.getElementById('pip-last-updated');
      if (lastUpdatePip) {
        const now = new Date();
        lastUpdatePip.innerText = now.toLocaleTimeString();
        lastUpdatePip.parentElement.classList.remove('pip-error');
        lastUpdatePip.parentElement.classList.add('pip-success');
      }

      // Placeholder logic for new pips until backend is connected
      const thermalPip = document.getElementById('pip-thermal-status');
      if (thermalPip) {
        thermalPip.innerText = 'OK';
        thermalPip.parentElement.classList.remove('pip-error', 'pip-warning');
        thermalPip.parentElement.classList.add('pip-success');
      }

      const healthPip = document.getElementById('pip-health-status');
      if (healthPip) {
        healthPip.innerText = 'OK';
        healthPip.parentElement.classList.remove('pip-error');
        healthPip.parentElement.classList.add('pip-success');
      }
    } catch (e) {
      console.error('Polling error:', e);
      const serialPip = document.getElementById('pip-serial-status');
      if (serialPip) {
        serialPip.innerText = 'Disconnected';
        serialPip.parentElement.classList.remove('pip-success');
        serialPip.parentElement.classList.add('pip-error');
        serialPip.parentElement.style.color = '';
      }
      
      const lastUpdatePip = document.getElementById('pip-last-updated');
      if (lastUpdatePip) {
        lastUpdatePip.innerText = 'Failed';
        lastUpdatePip.parentElement.classList.remove('pip-success');
        lastUpdatePip.parentElement.classList.add('pip-error');
      }
    }
  }

  async function pollHistory() {
    try {
      // Fetch last 1 hour of history
      const history = await api.getHistory(1);
      if (history && history.history) {
        for(let i=0; i<6; i++) {
          updateFanChart(`fan-chart-${i}`, history.history, i);
        }
      }
    } catch (e) {
      console.error('History error:', e);
    }
  }

  // Initial fetch and start loop
  pollStatus();
  pollHistory();
  
  let statusTimer = setInterval(pollStatus, pollInterval * 1000);
  setInterval(pollHistory, 15000); // Update charts every 15s

  window.updatePollInterval = (seconds) => {
    clearInterval(statusTimer);
    statusTimer = setInterval(pollStatus, seconds * 1000);
  };
});
