class InspectorUI {
  constructor(root) {
    this.root = root;
    this.statusUrl = root?.dataset.statusEndpoint;
    this.taskEndpoint = root?.dataset.taskEndpoint;
    this.scanBoard = root?.querySelector('#scan-board');
    this.insightGrid = root?.querySelector('.insight-grid');
    this.activityFeed = root?.querySelector('[data-activity-feed]');
    this.logStream = root?.querySelector('[data-log-stream]');
    this.taskHistory = root?.querySelector('[data-task-history]');
    this.connectionIndicator = root?.querySelector('[data-connection-indicator]');
    this.offlineBadge = root?.querySelector('[data-offline-indicator]');
    this.schemaCanvas = root?.querySelector('#schema-canvas');
    this.previousTaskStatuses = new Map();
    this.schemaGraphInstance = null;
  }

  init() {
    if (!this.root) return;
    this.bindTabs();
    this.bindDropzone();
    this.bindTaskForm();
    this.observeConnection();
    this.startPolling();
    this.requestNotificationPermission();
    this.loadInitialSchema();
    this.refreshIcons();
  }

  bindTabs() {
    const tabs = this.root.querySelectorAll('[data-tab-target]');
    const panels = this.root.querySelectorAll('.tab-panel');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((t) => t.classList.remove('is-active'));
        tab.classList.add('is-active');
        const target = tab.dataset.tabTarget;
        panels.forEach((panel) => {
          panel.classList.toggle('is-visible', panel.id === `tab-${target}`);
        });
      });
    });
  }

  bindDropzone() {
    const dropzone = this.root.querySelector('[data-dropzone]');
    if (!dropzone) return;

    const toggle = (state) => {
      dropzone.classList.toggle('is-active', state);
    };

    dropzone.addEventListener('dragenter', (event) => {
      event.preventDefault();
      toggle(true);
    });
    dropzone.addEventListener('dragover', (event) => {
      event.preventDefault();
      toggle(true);
    });
    dropzone.addEventListener('dragleave', (event) => {
      event.preventDefault();
      toggle(false);
    });
    dropzone.addEventListener('drop', (event) => {
      event.preventDefault();
      toggle(false);
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (!files.length) {
        dropzone.querySelector('p').textContent = 'Drop files from your editor to auto-import.';
        return;
      }
      const first = files[0];
      const path = first.path || first.name;
      dropzone.querySelector('p').textContent = `Queued ${path}`;
    });
  }

  bindTaskForm() {
    const form = this.root.querySelector('[data-task-form]');
    if (!form || !this.taskEndpoint) return;

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      fetch(this.taskEndpoint, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': this.getCsrfToken(),
        },
      })
        .then((response) => {
          if (!response.ok) {
            return response.json().then((data) => {
              throw new Error(this.formatErrors(data.errors));
            });
          }
          return response.json();
        })
        .then((payload) => {
          this.updateFromPayload(payload);
          form.reset();
        })
        .catch((error) => {
          this.showToast(error.message || 'Unable to run task.');
        });
    });
  }

  startPolling() {
    if (!this.statusUrl) return;
    const fetchStatus = () => {
      fetch(this.statusUrl, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then((response) => response.json())
        .then((payload) => this.updateFromPayload(payload))
        .catch(() => {
          this.toggleConnection(false);
        });
    };
    fetchStatus();
    this.polling = window.setInterval(fetchStatus, 6000);
  }

  updateFromPayload(payload) {
    if (!payload) return;
    this.toggleConnection(true);
    this.updateScans(payload.scans || []);
    this.updateInsights(payload.insights || []);
    this.updateActivity(payload.activity || []);
    this.updateLogs(payload.log_excerpt || []);
    this.updateTasks(payload.tasks || []);
    this.updateSchema(payload.schema || null);
    this.refreshIcons();
  }

  updateScans(scans) {
    if (!this.scanBoard) return;
    if (!scans.length) {
      this.scanBoard.innerHTML = '<p class="empty-note">No scans queued yet.</p>';
      return;
    }
    this.scanBoard.innerHTML = scans
      .map(
        (scan) => `
        <article class="scan-card scan-card--${scan.status}">
          <header>
            <span class="scan-kind">
              <span data-lucide="${this.getScanIcon(scan.kind)}"></span>
              <span>${this.escape(scan.kind_label)}</span>
            </span>
            <span>${scan.progress ?? 0}%</span>
          </header>
          <p>${this.escape(scan.summary || '')}</p>
        </article>
      `
      )
      .join('');
    this.refreshIcons();
  }

  updateInsights(insights) {
    if (!this.insightGrid) return;
    if (!insights.length) {
      this.insightGrid.innerHTML = '<p class="empty-note">Run a scan to populate insights.</p>';
      return;
    }
    this.insightGrid.innerHTML = insights
      .map(
        (insight) => `
        <article class="insight-card insight-card--${insight.severity || 'info'}">
          <header>
            <span>${this.escape(insight.title)}</span>
            <small>${this.escape(insight.delta || '')}</small>
          </header>
          <div class="insight-value">${this.escape(insight.value)}</div>
          <p>${this.escape(insight.caption || '')}</p>
        </article>
      `
      )
      .join('');
  }

  updateActivity(activity) {
    if (!this.activityFeed) return;
    this.activityFeed.innerHTML = activity
      .map(
        (event) => `
        <li>
          <span class="activity-time">${this.escape(event.timestamp || '')}</span>
          <span class="activity-message">${this.escape(event.message || '')}</span>
          <span class="activity-status activity-status--${event.status || 'info'}">${this.escape(
          event.kind || ''
        )}</span>
        </li>
      `
      )
      .join('');
  }

  updateLogs(entries) {
    if (!this.logStream) return;
    this.logStream.innerHTML = entries
      .map(
        (entry) => `
        <li>
          <span>${this.escape(entry.timestamp || '')}</span>
          <span class="log-level log-level--${entry.level || 'info'}">${this.escape(
          (entry.level || 'info').toUpperCase()
        )}</span>
          <span>${this.escape(entry.message || '')}</span>
        </li>
      `
      )
      .join('');
  }

  updateTasks(tasks) {
    if (!this.taskHistory) return;
    if (!tasks.length) {
      this.taskHistory.innerHTML = '<li class="task-history-item is-empty">No tasks so far.</li>';
      return;
    }

    this.taskHistory.innerHTML = tasks
      .map(
        (task) => `
        <li class="task-history-item task-history-item--${task.status}">
          <div>
            <strong>${this.escape(task.label)}</strong>
            <small>${this.escape(task.status)}</small>
          </div>
          <div class="task-progress"><span style="width:${task.progress || 0}%"></span></div>
        </li>
      `
      )
      .join('');

    tasks.forEach((task) => {
      const previousStatus = this.previousTaskStatuses.get(task.id);
      if (previousStatus && previousStatus !== task.status && ['succeeded', 'failed'].includes(task.status)) {
        this.pushNotification(task.label, `Task ${task.status}`);
      }
      this.previousTaskStatuses.set(task.id, task.status);
    });
  }

  observeConnection() {
    const syncState = () => this.toggleConnection(navigator.onLine);
    window.addEventListener('online', syncState);
    window.addEventListener('offline', syncState);
    syncState();
  }

  toggleConnection(isOnline) {
    if (this.connectionIndicator) {
      this.connectionIndicator.textContent = isOnline ? 'Online' : 'Offline';
      this.connectionIndicator.classList.toggle('is-offline', !isOnline);
    }
    if (this.offlineBadge) {
      this.offlineBadge.textContent = isOnline ? 'Synced' : 'Offline-ready';
    }
  }

  requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }

  pushNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title || 'DJDesk Task', { body });
    }
  }

  showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'inspector-message';
    toast.textContent = message;
    const container = document.querySelector('.inspector-messages') || this.createMessageContainer();
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  }

  createMessageContainer() {
    const container = document.createElement('div');
    container.className = 'inspector-messages';
    document.body.appendChild(container);
    return container;
  }

  getCsrfToken() {
    const name = 'csrftoken';
    const cookies = document.cookie.split(';');
    for (const cookie of cookies) {
      const trimmed = cookie.trim();
      if (trimmed.startsWith(`${name}=`)) {
        return decodeURIComponent(trimmed.slice(name.length + 1));
      }
    }
    return '';
  }

  formatErrors(errors) {
    if (!errors) return 'Unknown error';
    return Object.entries(errors)
      .map(([field, messages]) => `${field}: ${messages.join(', ')}`)
      .join(' · ');
  }

  escape(value) {
    if (value === null || value === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(value);
    return div.innerHTML;
  }

  loadInitialSchema() {
    const data = this.getSchemaDataFromScript();
    if (data) {
      this.updateSchema(data);
    }
  }

  getSchemaDataFromScript() {
    const script = document.getElementById('schema-graph-data');
    if (!script) return null;
    try {
      return JSON.parse(script.textContent);
    } catch {
      return null;
    }
  }

  updateSchema(schema) {
    if (!this.schemaCanvas) return;
    const hasNodes = schema && Array.isArray(schema.nodes) && schema.nodes.length > 0;
    if (!hasNodes) {
      this.schemaCanvas.classList.add('is-empty');
      this.schemaCanvas.innerHTML = '<p class="empty-note">Run a schema scan to visualize relationships.</p>';
      if (this.schemaGraphInstance) {
        this.schemaGraphInstance.destroy();
        this.schemaGraphInstance = null;
      }
      return;
    }
    if (!window.cytoscape) {
      this.schemaCanvas.textContent = 'Schema view unavailable (visualization library missing).';
      return;
    }
    this.schemaCanvas.classList.remove('is-empty');
    this.schemaCanvas.innerHTML = '';
    const elements = this.buildSchemaElements(schema);
    if (this.schemaGraphInstance) {
      this.schemaGraphInstance.destroy();
    }
    this.schemaGraphInstance = window.cytoscape({
      container: this.schemaCanvas,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': '#0f172a',
            'border-color': '#3b82f6',
            'border-width': 2,
            'width': 120,
            'height': 60,
            'shape': 'round-rectangle',
            'label': 'data(label)',
            'color': '#f8fafc',
            'font-size': 12,
            'font-weight': 600,
            'text-valign': 'center',
            'text-wrap': 'wrap',
            'text-max-width': 100,
          },
        },
        {
          selector: 'edge',
          style: {
            'width': 2,
            'curve-style': 'bezier',
            'line-color': '#7dd3fc',
            'target-arrow-color': '#7dd3fc',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 1,
          },
        },
      ],
      layout: { name: 'cose', padding: 30, animate: true, fit: true },
      wheelSensitivity: 0.15,
    });
  }

  buildSchemaElements(schema) {
    const nodes = Array.isArray(schema.nodes) ? schema.nodes : [];
    const edges = Array.isArray(schema.connections) ? schema.connections : [];
    return [
      ...nodes.map((node) => ({
        data: {
          id: node.name,
          label: node.name,
          badge: node.badge || '',
        },
      })),
      ...edges.map((edge, index) => ({
        data: {
          id: `${edge.source}-${edge.target}-${index}`,
          source: edge.source,
          target: edge.target,
        },
      })),
    ];
  }

  refreshIcons() {
    if (window.lucide?.createIcons) {
      window.lucide.createIcons();
    }
  }

  getScanIcon(kind) {
    switch (kind) {
      case 'migrations':
        return 'git-commit';
      case 'logs':
        return 'activity';
      case 'fixtures':
        return 'archive';
      case 'schema':
      default:
        return 'database';
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
  const shell = document.querySelector('[data-inspector-shell]');
  if (shell) {
    const ui = new InspectorUI(shell);
    ui.init();
  }
});
