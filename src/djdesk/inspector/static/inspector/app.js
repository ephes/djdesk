const WIZARD_STORAGE_KEY = 'djdesk.wizard.projectPath';

class InspectorUI {
  constructor(root) {
    this.root = root;
    this.statusUrl = root?.dataset.statusEndpoint;
    this.taskEndpoint = root?.dataset.taskEndpoint;
    this.workspaceSlug = root?.dataset.workspaceSlug || '';
    this.taskDetailTemplate = root?.dataset.taskDetailTemplate || '';
    this.dataLabExportUrl = root?.dataset.dataLabExport || '';
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
    this.schemaSignature = null;
    this.schemaTooltip = document.createElement('div');
    this.schemaTooltip.className = 'schema-tooltip';
    this.taskDrawer = root?.querySelector('[data-task-drawer]');
    this.taskDrawerTitle = this.taskDrawer?.querySelector('[data-task-drawer-title]');
    this.taskDrawerStatus = this.taskDrawer?.querySelector('[data-task-drawer-status]');
    this.taskDrawerProgress = this.taskDrawer?.querySelector('[data-task-drawer-progress]');
    this.taskDrawerLog = this.taskDrawer?.querySelector('[data-task-drawer-log]');
    this.taskDrawerClose = this.taskDrawer?.querySelector('[data-task-drawer-close]');
    this.taskDrawerPoll = null;
    this.schemaStateDebounce = null;
    this.tabs = [];
    this.tabPanels = [];
    this.docsDrawer = root?.querySelector('[data-docs-drawer]');
    this.docsFrame = this.docsDrawer?.querySelector('[data-docs-frame]');
    this.docsDrawerClose = this.docsDrawer?.querySelector('[data-docs-close]');
    this.docsToggle = this.root?.querySelector('[data-docs-toggle]');
    this.docLinks = Array.from(this.root?.querySelectorAll('[data-doc-link]') ?? []);
    this.docsBaseUrl = root?.dataset.docsBaseUrl || '';
    this.dataLabForm = root?.querySelector('[data-data-lab-form]');
    this.dataLabList = root?.querySelector('[data-data-lab-list]');
    this.dataLabViewer = root?.querySelector('[data-data-lab-viewer]');
    this.dataLabFrame = root?.querySelector('[data-data-lab-frame]');
    this.dataLabEmpty = root?.querySelector('[data-data-lab-empty]');
    this.activeNotebookSlug = '';
    this.dataLabExportPending = false;
    this.notebookSelectionGeneration = 0;
    this.dataLabIncomplete = root?.querySelector('[data-data-lab-incomplete]');
    this.nativeBridge = window.djdeskNative || null;
  }

  init() {
    if (!this.root) return;
    this.bindTabs();
    this.bindDropzone();
    this.bindTaskForm();
    this.bindTaskHistory();
    this.bindTaskDrawer();
    this.bindDocLinks();
    this.bindDocsDrawer();
    this.bindDataLab();
    this.observeConnection();
    this.startPolling();
    this.requestNotificationPermission();
    this.loadInitialSchema();
    this.ensureSchemaTooltip();
    this.refreshIcons();
  }

  bindTabs() {
    this.tabs = Array.from(this.root.querySelectorAll('[data-tab-target]'));
    this.tabPanels = Array.from(this.root.querySelectorAll('.tab-panel'));
    this.tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        this.activateTab(tab.dataset.tabTarget);
      });
    });
  }

  activateTab(target) {
    if (!target) return;
    this.tabs.forEach((tab) => {
      tab.classList.toggle('is-active', tab.dataset.tabTarget === target);
    });
    this.tabPanels.forEach((panel) => {
      panel.classList.toggle('is-visible', panel.id === `tab-${target}`);
    });
  }

  bindDropzone() {
    const dropzone = this.root.querySelector('[data-dropzone]');
    if (!dropzone) return;

    const toggle = (state) => {
      dropzone.classList.toggle('is-active', state);
    };

    const message = dropzone.querySelector('p');
    const defaultMessage = message?.textContent || 'Drop files from your editor to auto-import.';

    const queuePath = (path) => {
      if (!path) {
        return;
      }
      this.persistWizardPath(path);
      if (message) {
        message.textContent = `Queued ${path}`;
      }
      this.showToast(`Primed wizard with ${path}`, 'success');
    };

    const nativeDropHandler = (event) => {
      const paths = event.detail?.paths ?? [];
      const [first] = paths;
      if (!first) {
        return;
      }
      toggle(false);
      queuePath(first);
    };

    window.addEventListener('djdesk:workspace-drop', nativeDropHandler);

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
      if (this.nativeBridge) {
        // Native bridge dispatches drop events via preload; avoid double-handling.
        return;
      }
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (!files.length) {
        if (message) {
          message.textContent = defaultMessage;
        }
        return;
      }
      const first = files[0];
      const path = first.path || first.name;
      queuePath(path);
    });
  }

  persistWizardPath(path) {
    if (!path) return;
    if (this.nativeBridge?.stageProjectPath) {
      this.nativeBridge.stageProjectPath(path);
      return;
    }
    try {
      localStorage.setItem(WIZARD_STORAGE_KEY, path);
    } catch {
      // Ignore storage failures.
    }
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
          this.showToast(error.message || 'Unable to run task.', 'danger');
        });
    });
  }

  bindTaskHistory() {
    if (!this.taskHistory) return;
    this.taskHistory.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-task-run]');
      if (!trigger?.dataset.taskRun) {
        return;
      }
      event.preventDefault();
      this.openTaskDrawer(trigger.dataset.taskRun);
    });
  }

  bindTaskDrawer() {
    if (!this.taskDrawer) return;
    this.taskDrawer.addEventListener('click', (event) => {
      if (event.target === this.taskDrawer) {
        this.closeTaskDrawer();
      }
    });
    this.taskDrawerClose?.addEventListener('click', () => this.closeTaskDrawer());
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        this.closeTaskDrawer();
      }
    });
  }

  bindDocLinks() {
    if (!this.docLinks.length) return;
    this.docLinks.forEach((link) => {
      link.addEventListener('click', (event) => {
        if (event.metaKey || event.ctrlKey) {
          return;
        }
        event.preventDefault();
        const url = link.dataset.docUrl || link.href;
        const external = link.dataset.docExternal === 'true';
        this.openDocsDrawer(url);
        this.navigateToPane(link.dataset.paneTarget);
        if (external) {
          this.openExternal(url);
        }
      });
    });
  }

  bindDocsDrawer() {
    this.docsToggle?.addEventListener('click', () => {
      this.openDocsDrawer(this.docsBaseUrl);
    });
    this.docsDrawerClose?.addEventListener('click', () => this.closeDocsDrawer());
    this.docsDrawer?.addEventListener('click', (event) => {
      if (event.target === this.docsDrawer) {
        this.closeDocsDrawer();
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        this.closeDocsDrawer();
      }
    });
  }

  openDocsDrawer(url) {
    if (!this.docsDrawer) return;
    this.docsDrawer.classList.add('is-visible');
    if (this.docsFrame && url) {
      this.docsFrame.src = url;
    }
  }

  closeDocsDrawer() {
    this.docsDrawer?.classList.remove('is-visible');
  }

  openExternal(url) {
    if (!url) return;
    if (this.nativeBridge?.openExternal) {
      this.nativeBridge.openExternal(url);
    }
  }

  bindDataLab() {
    if (this.dataLabForm && this.dataLabExportUrl) {
      this.dataLabForm.addEventListener('change', () => {
        this.notebookSelectionGeneration += 1;
      });
      this.dataLabForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (this.dataLabExportPending) return;
        const formData = new FormData(this.dataLabForm);
        const template = formData.get('template');
        if (!template) {
          this.showToast('Choose a notebook template first.', 'danger');
          return;
        }
        this.dataLabExportPending = true;
        const generation = this.notebookSelectionGeneration;
        const controls = Array.from(this.dataLabForm.querySelectorAll('button, input, select'))
          .map((control) => [control, control.disabled]);
        controls.forEach(([control]) => { control.disabled = true; });
        try {
          const response = await fetch(this.dataLabExportUrl, {
            method: 'POST',
            body: formData,
            headers: {
              'X-Requested-With': 'XMLHttpRequest',
              'X-CSRFToken': this.getCsrfToken(),
            },
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(this.formatErrors(payload.errors));
          this.showToast('Notebook exported.', 'success');
          // Export responses own the new notebook, never a stale workspace snapshot.
          if (generation === this.notebookSelectionGeneration) {
            this.updateDataLab(payload.data_lab || null);
          }
        } catch (error) {
          this.showToast(error.message || 'Unable to export notebook.', 'danger');
        } finally {
          this.dataLabExportPending = false;
          controls.forEach(([control, disabled]) => { control.disabled = disabled; });
        }
      });
    }

    if (this.dataLabList) {
      this.dataLabList.addEventListener('click', (event) => {
        const trigger = event.target.closest('[data-notebook-url]');
        if (!trigger) return;
        this.notebookSelectionGeneration += 1;
        this.openNotebook(trigger.dataset.notebookUrl, trigger.dataset.notebookSlug);
      });
    }
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
    this.applySchema(payload.schema || null);
    this.updateDataLab(payload.data_lab || null);
    this.refreshIcons();
  }

  updateScans(scans) {
    if (!this.scanBoard) return;
    if (!scans.length) {
      this.scanBoard.innerHTML = this.renderScanSkeleton();
      return;
    }
    this.scanBoard.innerHTML = scans
      .map(
        (scan) => `
        <article class="scan-card scan-card--${this.allowedValue(scan.status, ['pending', 'running', 'completed', 'failed'], 'pending')}">
          <header>
            <span class="scan-kind">
              <span data-lucide="${this.getScanIcon(scan.kind)}"></span>
              <span>${this.escape(scan.kind_label)}</span>
            </span>
            <span>${this.progressPercent(scan.progress)}%</span>
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
      this.insightGrid.innerHTML = this.renderInsightSkeleton();
      return;
    }
    this.insightGrid.innerHTML = insights
      .map(
        (insight) => `
        <article class="insight-card insight-card--${this.allowedValue(insight.severity, ['info', 'success', 'warning', 'danger'], 'info')}">
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
          <span class="activity-status activity-status--${this.allowedValue(event.status, ['info', 'success', 'warning', 'danger'], 'info')}">${this.escape(
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
          <span class="log-level log-level--${this.allowedValue(entry.level, ['debug', 'info', 'success', 'warning', 'error', 'critical'], 'info')}">${this.escape(
          String(entry.level || 'info').toUpperCase()
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
      this.taskHistory.innerHTML = this.renderTaskSkeleton();
      return;
    }

    this.taskHistory.innerHTML = tasks
      .map(
        (task) => `
        <li class="task-history-item task-history-item--${this.allowedValue(task.status, ['requested', 'running', 'succeeded', 'failed', 'cancelled'], 'requested')}" data-task-run="${this.escapeAttribute(task.id)}">
          <button type="button" class="task-history-trigger" data-task-run="${this.escapeAttribute(task.id)}">
            <div>
              <strong>${this.escape(task.label)}</strong>
              <small>${this.escape(task.status)}</small>
            </div>
            <div class="task-progress"><span style="width:${this.progressPercent(task.progress)}%"></span></div>
          </button>
        </li>
      `
      )
      .join('');

    tasks.forEach((task) => {
      const previousStatus = this.previousTaskStatuses.get(task.id);
      if (previousStatus && previousStatus !== task.status && ['succeeded', 'failed'].includes(task.status)) {
        this.pushNotification(task.label, `Task ${task.status}`);
        this.showToast(`${task.label} ${task.status}`, task.status === 'succeeded' ? 'success' : 'danger');
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
    if (this.nativeBridge?.notify) {
      return;
    }
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }

  pushNotification(title, body) {
    if (this.nativeBridge?.notify) {
      this.nativeBridge.notify({ title: title || 'DJDesk Task', body: body || '' });
      return;
    }
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title || 'DJDesk Task', { body });
    }
  }

  showToast(message, tone = 'info') {
    const toast = document.createElement('div');
    toast.className = `inspector-message inspector-message--${tone}`;
    toast.innerHTML = `
      <span class="inspector-message-icon" data-lucide="${this.getToastIcon(tone)}"></span>
      <span>${this.escape(message)}</span>
    `;
    const container = document.querySelector('.inspector-messages') || this.createMessageContainer();
    container.appendChild(toast);
    this.refreshIcons();
    setTimeout(() => toast.remove(), 4200);
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

  escapeAttribute(value) {
    return this.escape(value).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  allowedValue(value, allowed, fallback) {
    return allowed.includes(value) ? value : fallback;
  }

  progressPercent(value) {
    return typeof value === 'number' && Number.isFinite(value)
      ? Math.max(0, Math.min(100, value)) : 0;
  }

  loadInitialSchema() {
    const data = this.getSchemaDataFromScript();
    if (data) {
      this.applySchema(data);
    }
  }

  // A failed rebuild keeps the previous view (updateSchema restores it before
  // rethrowing); it must not abort the other panels or the connection state.
  applySchema(schema) {
    try {
      this.updateSchema(schema);
    } catch (error) {
      console.warn('Schema view update failed; keeping the previous view.', error);
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
    this.ensureSchemaTooltip();
    const hasNodes = schema && Array.isArray(schema.nodes) && schema.nodes.length > 0;
    if (!hasNodes) {
      this.schemaCanvas.classList.add('is-empty');
      this.schemaCanvas.innerHTML = this.renderSchemaSkeleton();
      if (this.schemaGraphInstance) {
        this.schemaGraphInstance.destroy();
        this.schemaGraphInstance = null;
      }
      this.schemaSignature = null;
      return;
    }
    if (!window.cytoscape) {
      if (!this.schemaGraphInstance) {
        this.schemaCanvas.textContent = 'Schema view unavailable (visualization library missing).';
      }
      return;
    }
    const elements = this.buildSchemaElements(schema);
    const signature = this.schemaDataSignature(elements);
    if (this.schemaGraphInstance && signature === this.schemaSignature) return;
    const previous = this.schemaGraphInstance;
    const viewport = previous ? { zoom: previous.zoom(), pan: { ...previous.pan() } } : null;
    const container = document.createElement('div');
    Object.assign(container.style, { position: 'absolute', inset: '0', visibility: 'hidden' });
    this.schemaCanvas.appendChild(container);
    let graph = null;
    try {
      graph = window.cytoscape({
        container,
        elements: previous ? previous.elements().jsons() : [],
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
        layout: { name: 'preset' },
        wheelSensitivity: 0.15,
      });
      this.syncSchemaElements(graph, elements);
      // A synchronous layout finishes staging before the live generation is retired.
      graph.layout({ name: 'cose', padding: 30, animate: false, fit: !previous }).run();
      this.attachSchemaTooltip(schema, graph);
      graph.on('pan zoom', () => this.scheduleSchemaStatePersist(graph));
      this.schemaGraphInstance = graph;
      this.restoreSchemaState(graph, viewport);
    } catch (error) {
      this.schemaGraphInstance = previous;
      graph?.destroy();
      container.remove();
      throw error;
    }
    previous?.destroy();
    Array.from(this.schemaCanvas.children).forEach((child) => {
      if (child !== container && child !== this.schemaTooltip) child.remove();
    });
    this.schemaCanvas.classList.remove('is-empty');
    this.schemaTooltip.classList.remove('is-visible');
    container.style.visibility = 'visible';
    this.schemaSignature = signature;
  }

  schemaDataSignature(value) {
    const canonical = (item) => {
      if (Array.isArray(item)) return item.map(canonical);
      if (item && typeof item === 'object') {
        return Object.fromEntries(Object.keys(item).sort().map((key) => [key, canonical(item[key])]));
      }
      return item;
    };
    return JSON.stringify(canonical(value));
  }

  syncSchemaElements(graph, elements) {
    const desired = new Map(elements.map((element) => [element.data.id, element]));
    graph.batch(() => {
      graph.elements().forEach((element) => {
        if (!desired.has(element.id())) element.remove();
      });
      elements.forEach((element) => {
        const current = graph.getElementById(element.data.id);
        if (!current.length) graph.add(element);
        else if (this.schemaDataSignature(current.data()) !== this.schemaDataSignature(element.data)) {
          current.data(element.data);
        }
      });
    });
  }

  updateDataLab(data) {
    if (!data) return;
    this.updateTemplateOptions(data.templates || []);
    if (this.dataLabIncomplete) this.dataLabIncomplete.hidden = !data.notebooks_incomplete;
    const selected = data.notebooks?.find((nb) => nb.slug === data.selected_notebook);
    if (data.selected_notebook && (data.selected_notebook_url || selected?.viewer_url)) {
      this.openNotebook(data.selected_notebook_url || selected.viewer_url, data.selected_notebook);
    }
    if (!data.notebooks?.length) {
      if (this.activeNotebookSlug) {
        this.renderDataLabList([]);
        return;
      }
      this.activeNotebookSlug = '';
      this.renderDataLabList([]);
      if (this.dataLabViewer) {
        this.dataLabViewer.setAttribute('hidden', 'hidden');
      }
      return;
    }
    this.renderDataLabList(data.notebooks || []);
    const existing = data.notebooks.find((nb) => nb.slug === this.activeNotebookSlug);
    if (existing || this.activeNotebookSlug) {
      this.highlightNotebook();
    } else {
      const first = data.notebooks[0];
      this.openNotebook(first.viewer_url, first.slug);
    }
  }

  ensureSchemaTooltip() {
    if (!this.schemaCanvas || !this.schemaTooltip) return;
    if (!this.schemaTooltip.parentElement) {
      this.schemaCanvas.appendChild(this.schemaTooltip);
    }
  }

  attachSchemaTooltip(schema, graph = this.schemaGraphInstance) {
    if (!graph || !this.schemaTooltip) return;
    const schemaNodes = new Map();
    (schema.nodes || []).forEach((node) => {
      schemaNodes.set(node.name, node);
    });

    graph.off('mouseover');
    graph.off('mouseout');
    graph.off('mousemove');

    graph.on('mouseover', 'node', (event) => {
      if (graph !== this.schemaGraphInstance) return;
      const node = schemaNodes.get(event.target.id());
      if (!node) return;
      this.schemaTooltip.innerHTML = this.buildTooltipContent(node);
      this.schemaTooltip.classList.add('is-visible');
      this.positionSchemaTooltip(event.renderedPosition);
    });

    graph.on('mouseout', 'node', () => {
      if (graph !== this.schemaGraphInstance) return;
      this.schemaTooltip.classList.remove('is-visible');
    });

    graph.on('mousemove', 'node', (event) => {
      if (graph !== this.schemaGraphInstance) return;
      this.positionSchemaTooltip(event.renderedPosition);
    });
  }

  buildTooltipContent(node) {
    const fields = Array.isArray(node.fields) ? node.fields.length : 0;
    const relations = Array.isArray(node.relations) ? node.relations.length : 0;
    const pending = Number(node.pending_migrations || 0);
    return `
      <strong>${this.escape(node.name || 'Model')}</strong>
      <dl>
        <dt>Fields</dt><dd>${fields}</dd>
        <dt>Relations</dt><dd>${relations}</dd>
        <dt>Pending</dt><dd>${pending}</dd>
      </dl>
    `;
  }

  positionSchemaTooltip(position) {
    if (!this.schemaTooltip) return;
    const offset = 12;
    const x = position?.x ?? 0;
    const y = position?.y ?? 0;
    this.schemaTooltip.style.left = `${x + offset}px`;
    this.schemaTooltip.style.top = `${y + offset}px`;
  }

  scheduleSchemaStatePersist(graph = this.schemaGraphInstance) {
    if (!graph || graph !== this.schemaGraphInstance) return;
    if (this.schemaStateDebounce) {
      clearTimeout(this.schemaStateDebounce);
    }
    this.schemaStateDebounce = window.setTimeout(() => this.persistSchemaState(graph), 300);
  }

  persistSchemaState(graph = this.schemaGraphInstance) {
    if (!graph || graph !== this.schemaGraphInstance || !this.workspaceSlug) return;
    const payload = {
      zoom: graph.zoom(),
      pan: graph.pan(),
    };
    try {
      localStorage.setItem(`djdesk.schema.${this.workspaceSlug}`, JSON.stringify(payload));
    } catch {
      // Ignore storage failures.
    }
  }

  restoreSchemaState(graph = this.schemaGraphInstance, viewport = null) {
    if (!graph) return;
    let payload = viewport;
    try {
      if (!payload && this.workspaceSlug) {
        payload = JSON.parse(localStorage.getItem(`djdesk.schema.${this.workspaceSlug}`));
      }
    } catch {
      payload = null;
    }
    if (!payload) return;
    const apply = () => {
      if (graph !== this.schemaGraphInstance) return;
      if (payload.zoom) {
        graph.zoom(payload.zoom);
      }
      if (payload.pan) {
        graph.pan(payload.pan);
      }
    };
    apply();
    if (typeof graph.ready === 'function') {
      graph.ready(apply);
    }
  }

  updateTemplateOptions(templates) {
    if (!this.dataLabForm || !templates.length) return;
    const select = this.dataLabForm.querySelector('select[name="template"]');
    if (!select) return;
    const current = select.value;
    select.innerHTML = templates
      .map((template) => `<option value="${this.escapeAttribute(template.slug)}">${this.escape(template.title)}</option>`)
      .join('');
    if (templates.some((template) => template.slug === current)) {
      select.value = current;
    }
  }

  renderDataLabList(notebooks) {
    if (!this.dataLabList) return;
    if (!notebooks.length) {
      this.dataLabList.innerHTML = '<p class="empty-note">No exported notebooks yet.</p>';
      this.highlightNotebook();
      return;
    }
    this.dataLabList.innerHTML = notebooks
      .map(
        (notebook) => `
        <button
          type="button"
          class="data-lab-list-item ${notebook.slug === this.activeNotebookSlug ? 'is-active' : ''}"
          data-notebook-url="${this.escapeAttribute(notebook.viewer_url)}"
          data-notebook-slug="${this.escapeAttribute(notebook.slug)}"
        >
          <strong>${this.escape(notebook.title)}</strong>
          <small>${this.escape(notebook.file || '')}</small>
        </button>
      `
      )
      .join('');
  }

  openNotebook(url, slug) {
    if (!this.dataLabViewer || !this.dataLabFrame || !url) return;
    this.activeNotebookSlug = slug || '';
    this.dataLabViewer.removeAttribute('hidden');
    if (this.dataLabFrame.src !== url) {
      this.dataLabFrame.src = url;
    }
    this.highlightNotebook();
  }

  highlightNotebook() {
    if (!this.dataLabList) return;
    this.dataLabList.querySelectorAll('[data-notebook-slug]').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.notebookSlug === this.activeNotebookSlug);
    });
  }

  buildSchemaElements(schema) {
    const nodes = [...(Array.isArray(schema.nodes) ? schema.nodes : [])]
      .sort((a, b) => String(a.name ?? '').localeCompare(String(b.name ?? '')));
    const edges = [...(Array.isArray(schema.connections) ? schema.connections : [])]
      .sort((a, b) => JSON.stringify([a.source, a.target]).localeCompare(JSON.stringify([b.source, b.target])));
    const edgeCounts = new Map();
    return [
      ...nodes.map((node) => ({
        data: {
          id: node.name,
          label: node.name,
          badge: node.badge || '',
          fields: Array.isArray(node.fields) ? node.fields : [],
          relations: Array.isArray(node.relations) ? node.relations : [],
          pending_migrations: node.pending_migrations || 0,
        },
      })),
      ...edges.map((edge) => {
        const key = JSON.stringify([edge.source, edge.target]);
        const occurrence = edgeCounts.get(key) || 0;
        edgeCounts.set(key, occurrence + 1);
        return {
          data: {
            id: `edge:${key}:${occurrence}`,
            source: edge.source,
            target: edge.target,
          },
        };
      }),
    ];
  }

  renderScanSkeleton() {
    return Array.from({ length: 3 })
      .map(
        () => `
        <article class="scan-card">
          <header>
            <span class="scan-kind">${this.skeletonLine('80%', '0.8rem')}</span>
            ${this.skeletonLine('40px', '0.8rem')}
          </header>
          ${this.skeletonLine('100%', '0.75rem')}
          ${this.skeletonLine('70%', '0.75rem')}
        </article>
      `
      )
      .join('');
  }

  renderInsightSkeleton() {
    return Array.from({ length: 4 })
      .map(
        () => `
        <article class="insight-card">
          <header>
            ${this.skeletonLine('60%', '0.8rem')}
          </header>
          ${this.skeletonLine('90%', '2.2rem')}
          ${this.skeletonLine('80%', '0.8rem')}
        </article>
      `
      )
      .join('');
  }

  renderTaskSkeleton() {
    return Array.from({ length: 3 })
      .map(
        () => `
        <li class="task-history-item">
          ${this.skeletonLine('70%', '0.9rem')}
          <div class="task-progress">${this.skeletonLine('100%', '4px')}</div>
        </li>
      `
      )
      .join('');
  }

  renderSchemaSkeleton() {
    return `
      <div class="schema-skeleton">
        ${this.skeletonLine('30%', '140px')}
        ${this.skeletonLine('30%', '140px')}
        ${this.skeletonLine('30%', '140px')}
      </div>
    `;
  }

  skeletonLine(width = '100%', height = '1rem') {
    return `<span class="skeleton" style="display:block;width:${width};height:${height};"></span>`;
  }

  openTaskDrawer(taskId) {
    if (!this.taskDrawer || !taskId) return;
    this.taskDrawer.classList.add('is-visible');
    this.setTaskDrawerLoading();
    this.loadTaskDetails(taskId);
  }

  closeTaskDrawer() {
    if (!this.taskDrawer) return;
    this.taskDrawer.classList.remove('is-visible');
    this.clearTaskDrawerPolling();
  }

  setTaskDrawerLoading() {
    if (!this.taskDrawerTitle || !this.taskDrawerStatus || !this.taskDrawerLog) return;
    this.taskDrawerTitle.textContent = 'Loading task…';
    this.taskDrawerStatus.textContent = 'Fetching logs';
    this.taskDrawerStatus.className = 'task-drawer-status';
    if (this.taskDrawerProgress) {
      this.taskDrawerProgress.style.width = '5%';
    }
    this.taskDrawerLog.innerHTML = '<p class="empty-note">Preparing log stream…</p>';
  }

  loadTaskDetails(taskId) {
    const url = this.getTaskDetailUrl(taskId);
    if (!url) return;
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then((response) => {
        if (!response.ok) throw new Error('Request failed');
        return response.json();
      })
      .then((data) => {
        this.renderTaskDrawer(data);
        if (['running', 'requested'].includes(data.status)) {
          this.scheduleTaskDrawerRefresh(taskId);
        } else {
          this.clearTaskDrawerPolling();
        }
      })
      .catch(() => {
        this.showToast('Unable to load task log.', 'danger');
        this.closeTaskDrawer();
      });
  }

  renderTaskDrawer(data) {
    if (!this.taskDrawer) return;
    if (this.taskDrawerTitle) {
      this.taskDrawerTitle.textContent = data.label || data.preset || 'Task';
    }
    if (this.taskDrawerStatus) {
      this.taskDrawerStatus.textContent = data.status || 'unknown';
      this.taskDrawerStatus.className = `task-drawer-status task-drawer-status--${data.status || 'info'}`;
    }
    if (this.taskDrawerProgress) {
      this.taskDrawerProgress.style.width = `${data.progress ?? 0}%`;
    }
    if (this.taskDrawerLog) {
      this.taskDrawerLog.textContent = data.log || 'Awaiting log output…';
    }
  }

  scheduleTaskDrawerRefresh(taskId) {
    this.clearTaskDrawerPolling();
    this.taskDrawerPoll = window.setTimeout(() => this.loadTaskDetails(taskId), 3000);
  }

  clearTaskDrawerPolling() {
    if (this.taskDrawerPoll) {
      clearTimeout(this.taskDrawerPoll);
      this.taskDrawerPoll = null;
    }
  }

  getTaskDetailUrl(taskId) {
    if (!this.taskDetailTemplate) return '';
    if (!taskId) return '';
    if (this.taskDetailTemplate.includes('0/')) {
      return this.taskDetailTemplate.replace(/0\/?$/, `${taskId}/`);
    }
    return `${this.taskDetailTemplate}${taskId}/`;
  }

  navigateToPane(target) {
    if (!target) return;
    switch (target) {
      case 'schema':
        this.activateTab('schema');
        this.schemaCanvas?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        break;
      case 'tasks':
        this.root.querySelector('[data-task-form]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        break;
      case 'wizard':
        this.root.querySelector('[data-dropzone]')?.scrollIntoView({ behavior: 'smooth' });
        break;
      default:
        break;
    }
  }

  refreshIcons() {
    if (window.lucide?.createIcons) {
      window.lucide.createIcons();
    }
  }

  getToastIcon(tone) {
    switch (tone) {
      case 'success':
        return 'check-circle-2';
      case 'danger':
        return 'alert-triangle';
      default:
        return 'info';
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

class WizardUI {
  constructor(form) {
    this.form = form;
    this.projectInput = form?.querySelector('input[name="project_path"]');
    this.nativeBridge = window.djdeskNative || null;
  }

  init() {
    if (!this.form || !this.projectInput) return;
    this.prefillFromStagedPath();
  }

  prefillFromStagedPath() {
    const path = this.getStagedPath();
    if (!path || this.projectInput.value) return;
    this.projectInput.value = path;
    this.renderHint(path);
    this.clearStagedPath();
  }

  getStagedPath() {
    if (this.nativeBridge?.getStagedProjectPath) {
      return this.nativeBridge.getStagedProjectPath();
    }
    try {
      return localStorage.getItem(WIZARD_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  clearStagedPath() {
    if (this.nativeBridge?.clearStagedProjectPath) {
      this.nativeBridge.clearStagedProjectPath();
      return;
    }
    try {
      localStorage.removeItem(WIZARD_STORAGE_KEY);
    } catch {
      // Ignore storage failures.
    }
  }

  renderHint(path) {
    let hint = this.form.querySelector('[data-wizard-drop-hint]');
    if (!hint) {
      hint = document.createElement('p');
      hint.dataset.wizardDropHint = '1';
      hint.className = 'wizard-drop-hint';
      this.projectInput.insertAdjacentElement('afterend', hint);
    }
    hint.textContent = `Auto-filled from drag & drop: ${path}`;
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
  const wizardForm = document.querySelector('.wizard-form');
  if (wizardForm) {
    const wizard = new WizardUI(wizardForm);
    wizard.init();
  }
});
