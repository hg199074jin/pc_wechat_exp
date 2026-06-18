/* Knowledge Radar — inbox UI */
const KnowledgeApp = {
  cards: [],
  tags: [],
  selectedIds: new Set(),
  _nameMap: null,
  _editingScheduleId: null,

  init() {
    this.bindEvents();
    this.loadStats();
    this.loadCards();
    this.loadTags();
    this.loadSchedules();
    // Set default date range to yesterday
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const ds = yesterday.toISOString().slice(0, 10);
    const el = document.getElementById('scan-date-from');
    const el2 = document.getElementById('scan-date-to');
    if (el) el.value = ds;
    if (el2) el2.value = ds;
  },

  // -----------------------------------------------------------------------
  // Events
  // -----------------------------------------------------------------------

  bindEvents() {
    document.getElementById('btn-refresh').onclick = () => this.loadCards();
    document.getElementById('filter-type').onchange = () => this.loadCards();
    document.getElementById('filter-tag-path').onchange = () => this.loadCards();
    document.getElementById('filter-min-score').onchange = () => this.loadCards();
    document.getElementById('knowledge-search').oninput = this._debounce(() => this.loadCards(), 300);

    // Status nav
    document.getElementById('status-nav').onclick = (e) => {
      const li = e.target.closest('li[data-status]');
      if (!li) return;
      document.querySelectorAll('#status-nav li').forEach(l => l.classList.remove('active'));
      li.classList.add('active');
      this.loadCards();
    };

    // Card list actions
    document.getElementById('knowledge-list').onclick = (e) => this.handleCardAction(e);

    // Scan
    document.getElementById('btn-scan').onclick = () => this.openScanModal();
    document.getElementById('btn-start-scan').onclick = () => this.runScan();
    document.querySelectorAll('[data-scan-days]').forEach(btn => {
      btn.onclick = () => this.setScanDateRange(btn.dataset.scanDays);
    });

    // Export
    document.getElementById('btn-export-md').onclick = () => this.exportCards('md');
    document.getElementById('btn-export-docx').onclick = () => this.exportCards('docx');

    // Obsidian sync
    document.getElementById('btn-sync-obsidian').onclick = () => this.syncToObsidian();
    document.getElementById('btn-obsidian-path').onclick = () => this.configureObsidianPath();
    this.loadObsidianConfig();

    // Bulk
    document.getElementById('btn-bulk-select').onclick = () => this.toggleBulkSelect();
    document.getElementById('btn-bulk-archive').onclick = () => this.bulkAction('archived');
    document.getElementById('btn-bulk-delete').onclick = () => this.bulkAction('delete');

    // Schedule
    document.getElementById('btn-add-schedule').onclick = () => this.openScheduleModal();
    document.getElementById('btn-save-schedule').onclick = () => this.saveSchedule();
  },

  // -----------------------------------------------------------------------
  // Data loading
  // -----------------------------------------------------------------------

  async loadStats() {
    try {
      const r = await fetch('/api/knowledge/stats');
      const data = await r.json();
      document.getElementById('count-all').textContent = data.total || 0;
      const bs = data.by_status || {};
      document.getElementById('count-inbox').textContent = bs.inbox || 0;
      document.getElementById('count-saved').textContent = bs.saved || 0;
      document.getElementById('count-archived').textContent = bs.archived || 0;
      document.getElementById('count-rejected').textContent = bs.rejected || 0;
    } catch (e) { /* ignore */ }
  },

  async loadCards() {
    const params = new URLSearchParams();
    const activeLi = document.querySelector('#status-nav li.active');
    const status = activeLi ? activeLi.dataset.status : '';
    const type = document.getElementById('filter-type').value;
    const tagPath = document.getElementById('filter-tag-path')?.value || '';
    const q = document.getElementById('knowledge-search').value.trim();
    const minScore = document.getElementById('filter-min-score').value;
    if (status) params.set('status', status);
    if (type) params.set('type', type);
    if (tagPath) params.set('tag_path', tagPath);
    if (q) params.set('q', q);
    if (minScore) params.set('min_score', minScore);
    params.set('limit', '200');

    try {
      const r = await fetch('/api/knowledge/cards?' + params.toString());
      const data = await r.json();
      this.cards = data.cards || [];
      this.renderCards();
    } catch (e) {
      document.getElementById('knowledge-list').innerHTML =
        '<div class="empty-state">加载失败: ' + this.esc(e.message) + '</div>';
    }
  },

  async loadTags() {
    try {
      const r = await fetch('/api/knowledge/tags');
      const data = await r.json();
      this.tags = data.tags || [];
      this.renderTagFilter();
    } catch (e) { this.tags = []; }
  },

  renderTagFilter() {
    const select = document.getElementById('filter-tag-path');
    if (!select) return;
    const current = select.value || '';
    const items = this._flattenTagPaths(this.tags);
    select.innerHTML = '<option value="">全部群标签</option>' + items.map(item => {
      const label = `${'  '.repeat(item.depth)}${item.path} (${item.count})`;
      return `<option value="${this.esc(item.path)}">${this.esc(label)}</option>`;
    }).join('');
    if (items.some(item => item.path === current)) select.value = current;
  },

  async loadSchedules() {
    try {
      const r = await fetch('/api/knowledge/schedules');
      const data = await r.json();
      this.renderSchedules(data.schedules || []);
    } catch (e) { /* ignore */ }
  },

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------

  renderCards() {
    const root = document.getElementById('knowledge-list');
    this.selectedIds.clear();
    if (!this.cards.length) {
      root.innerHTML = '<div class="empty-state">暂无知识卡片<br><small>点击左侧"扫描知识"开始提取</small></div>';
      return;
    }
    root.innerHTML = this.cards.map(c => this.renderCard(c)).join('');
    this._syncBulkCheckboxes();
  },

  renderCard(c) {
    const scoreClass = c.score >= 80 ? 'score-high' : c.score >= 60 ? 'score-mid' : 'score-low';
    const typeLabels = {
      audit_case: '审计案例', sop: 'SOP', prompt: '提示词', faq: 'FAQ',
      article: '文章素材', tool: '工具经验', risk: '风险线索',
      methodology: '方法论', note: '笔记',
    };
    const statusLabels = {
      inbox: '⏳ 待沉淀', saved: '⭐ 已收藏', archived: '📦 已归档', rejected: '🚫 已忽略',
    };
    return `
      <article class="knowledge-card" data-card-id="${this.esc(c.id)}">
        <div class="knowledge-card-header">
          <div style="flex:1;min-width:0">
            <h3 data-action="detail">${this.esc(c.title)}</h3>
            <div class="knowledge-meta">
              <span>${this.esc(typeLabels[c.type] || c.type)}</span>
              <span>${this.esc(c.date || '')}</span>
              <span>${this.esc(statusLabels[c.status] || c.status)}</span>
              <span>${this.esc((c.sources || []).length ? (c.sources[0].chat_name || '') : '')}</span>
            </div>
          </div>
          <div class="knowledge-score ${scoreClass}">${c.score}</div>
        </div>
        <p class="knowledge-summary">${this.esc(c.summary || '')}</p>
        ${c.why_valuable ? `<p class="knowledge-why">💡 ${this.esc(c.why_valuable)}</p>` : ''}
        ${this.renderTagPaths(c.tag_paths || [])}
        <div class="knowledge-tags">${(c.tags || []).map(t =>
          `<span class="knowledge-tag" data-tag="${this.esc(t)}">${this.esc(t)}</span>`
        ).join('')}</div>
        <div class="knowledge-actions">
          <button class="btn-tiny" data-action="detail">详情</button>
          <button class="btn-tiny" data-action="save">${c.status === 'saved' ? '取消收藏' : '收藏'}</button>
          <button class="btn-tiny" data-action="archive">${c.status === 'archived' ? '取消归档' : '归档'}</button>
          <div class="convert-dropdown">
            <button class="btn-tiny" data-action="convert-menu">转化 ▾</button>
            <div class="convert-menu" data-card-id="${this.esc(c.id)}">
              <a href="#" data-convert="audit_case">审计案例</a>
              <a href="#" data-convert="sop">SOP</a>
              <a href="#" data-convert="prompt">提示词</a>
              <a href="#" data-convert="faq">FAQ</a>
              <a href="#" data-convert="article">文章素材</a>
            </div>
          </div>
          <button class="btn-tiny btn-danger" data-action="delete">删除</button>
          <label class="bulk-card-check"><input type="checkbox" class="bulk-cb" data-card-id="${this.esc(c.id)}" ${this.selectedIds.has(c.id) ? 'checked' : ''}> 批量</label>
        </div>
      </article>`;
  },

  renderTagPaths(paths) {
    if (!paths || !paths.length) {
      return '<div class="knowledge-tag-paths"><span class="knowledge-tag-path">未匹配群标签</span></div>';
    }
    return `<div class="knowledge-tag-paths">${paths.map(p =>
      `<span class="knowledge-tag-path" title="${this.esc(p)}">${this.esc(p)}</span>`
    ).join('')}</div>`;
  },

  renderSchedules(schedules) {
    const root = document.getElementById('schedule-list');
    if (!schedules.length) {
      root.innerHTML = '<div style="font-size:12px;color:#8b949e">暂无定时任务</div>';
      return;
    }
    root.innerHTML = schedules.map(s => `
      <div class="schedule-item">
        <div>
          <strong>${this.esc(s.name || '未命名')}</strong>
          <div style="font-size:11px;color:#8b949e">${this.esc(s.time || '08:00')} | ${this.esc(s.domain || 'general')}</div>
        </div>
        <div style="display:flex;gap:4px">
          <button class="btn-tiny" onclick="KnowledgeApp.toggleSchedule('${s.id}', ${!s.enabled})">${s.enabled ? '禁用' : '启用'}</button>
          <button class="btn-tiny" onclick="KnowledgeApp.editSchedule('${s.id}')">编辑</button>
          <button class="btn-tiny btn-danger" onclick="KnowledgeApp.deleteSchedule('${s.id}')">删除</button>
        </div>
      </div>
    `).join('');
  },

  // -----------------------------------------------------------------------
  // Card actions
  // -----------------------------------------------------------------------

  async handleCardAction(e) {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const article = btn.closest('.knowledge-card');
    if (!article) return;
    const cardId = article.dataset.cardId;
    const action = btn.dataset.action;

    if (action === 'detail') {
      this.showDetail(cardId);
    } else if (action === 'save') {
      const card = this.cards.find(c => c.id === cardId);
      const newStatus = card && card.status === 'saved' ? 'inbox' : 'saved';
      await this.updateCard(cardId, { status: newStatus });
    } else if (action === 'archive') {
      const card = this.cards.find(c => c.id === cardId);
      const newStatus = card && card.status === 'archived' ? 'inbox' : 'archived';
      await this.updateCard(cardId, { status: newStatus });
    } else if (action === 'delete') {
      if (!confirm('确定删除此知识卡片？')) return;
      await fetch('/api/knowledge/cards/' + cardId, { method: 'DELETE' });
      this.loadCards();
      this.loadStats();
    } else if (action === 'convert-menu') {
      e.stopPropagation();
      const menu = btn.nextElementSibling;
      document.querySelectorAll('.convert-menu.show').forEach(m => {
        if (m !== menu) m.classList.remove('show');
      });
      menu.classList.toggle('show');
    }
  },

  // Convert click handler (delegated)
  _handleConvert(e) {
    const link = e.target.closest('[data-convert]');
    if (!link) return;
    e.preventDefault();
    e.stopPropagation();
    const menu = link.closest('.convert-menu');
    const cardId = menu.dataset.cardId;
    const targetType = link.dataset.convert;
    menu.classList.remove('show');
    this.convertCard(cardId, targetType);
  },

  async updateCard(cardId, updates) {
    await fetch('/api/knowledge/cards/' + cardId, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    this.loadCards();
    this.loadStats();
  },

  async convertCard(cardId, targetType) {
    const typeLabels = {
      audit_case: '审计案例', sop: 'SOP', prompt: '提示词', faq: 'FAQ', article: '文章素材', script: '话术',
    };
    try {
      const r = await fetch('/api/knowledge/cards/' + cardId + '/convert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_type: targetType }),
      });
      const data = await r.json();
      if (data.error) {
        alert('转化失败: ' + data.error);
      } else {
        alert('已转化为 ' + (typeLabels[targetType] || targetType));
        this.loadCards();
      }
    } catch (e) {
      alert('转化失败: ' + e.message);
    }
  },

  // -----------------------------------------------------------------------
  // Detail modal
  // -----------------------------------------------------------------------

  async showDetail(cardId) {
    try {
      const r = await fetch('/api/knowledge/cards/' + cardId);
      const card = await r.json();
      if (card.error) { alert('加载失败'); return; }
      const typeLabels = {
        audit_case: '审计案例', sop: 'SOP', prompt: '提示词', faq: 'FAQ',
        article: '文章素材', tool: '工具经验', risk: '风险线索',
        methodology: '方法论', note: '笔记',
      };
      let html = `<h3>${this.esc(card.title)}</h3>`;
      html += `<div class="knowledge-meta" style="margin-bottom:12px">`;
      html += `<span>评分: <strong>${card.score}</strong></span>`;
      html += `<span>类型: ${this.esc(typeLabels[card.type] || card.type)}</span>`;
      html += `<span>日期: ${this.esc(card.date || '')}</span>`;
      html += `</div>`;
      if (card.summary) html += `<p><strong>摘要:</strong> ${this.esc(card.summary)}</p>`;
      if (card.why_valuable) html += `<p><strong>价值:</strong> ${this.esc(card.why_valuable)}</p>`;
      if (card.tags && card.tags.length) {
        html += `<div class="knowledge-tags">${card.tags.map(t =>
          `<span class="knowledge-tag">${this.esc(t)}</span>`
        ).join('')}</div>`;
      }
      if (card.content_md) {
        html += `<div class="knowledge-detail"><h4>正文</h4>`;
        html += `<div style="white-space:pre-wrap;font-size:13px;line-height:1.6">${this.esc(card.content_md)}</div></div>`;
      }
      const sources = card.sources || [];
      if (sources.length) {
        html += `<div class="knowledge-detail"><h4>来源 (${sources.length})</h4>`;
        for (const src of sources) {
          html += `<div class="source-item">`;
          html += `<div class="source-meta">[${this.esc(src.chat_name || '')}] ${this.esc(src.sender || '')}`;
          if (src.create_time) {
            html += ` (${new Date(src.create_time * 1000).toLocaleString()})`;
          }
          html += `</div>`;
          if (src.quote) html += `<div class="source-quote">${this.esc(src.quote)}</div>`;
          html += `</div>`;
        }
        html += `</div>`;
      }
      document.getElementById('detail-content').innerHTML = html;
      document.getElementById('detail-modal').classList.add('show');
    } catch (e) {
      alert('加载详情失败: ' + e.message);
    }
  },

  closeDetailModal() {
    document.getElementById('detail-modal').classList.remove('show');
  },

  // -----------------------------------------------------------------------
  // Scan
  // -----------------------------------------------------------------------

  openScanModal() {
    this.ensureScanModeControls();
    this.ensureKnowledgeSourceControls();
    this._renderTagPicker('scan-tag-picker');
    document.getElementById('scan-modal').classList.add('show');
  },

  ensureScanModeControls() {
    if (document.getElementById('scan-mode-controls')) return;
    const dateTo = document.getElementById('scan-date-to');
    if (!dateTo) return;
    const box = document.createElement('div');
    box.id = 'scan-mode-controls';
    box.className = 'scan-mode-controls';
    box.innerHTML = `
      <div class="scan-mode-title">扫描方式</div>
      <label><input type="radio" name="scan-mode" value="range" checked> 按时间段扫描</label>
      <label><input type="radio" name="scan-mode" value="daily"> 按天扫描</label>`;
    const row = dateTo.closest('.form-row') || dateTo.parentNode;
    row.insertAdjacentElement('afterend', box);

    if (!document.getElementById('scan-mode-style')) {
      const style = document.createElement('style');
      style.id = 'scan-mode-style';
      style.textContent = `
        .scan-mode-controls { margin: 0 0 12px; padding: 8px; border: 1px solid #30363d; border-radius: 6px; background: #0d1117; }
        .scan-mode-title { color: #8b949e; font-size: 12px; margin-bottom: 6px; }
        .scan-mode-controls label { display: flex; align-items: center; gap: 6px; color: #c9d1d9; font-size: 12px; line-height: 1.8; }
        .scan-mode-controls input { margin: 0; }
      `;
      document.head.appendChild(style);
    }
  },

  ensureKnowledgeSourceControls() {
    if (document.getElementById('knowledge-source-controls')) return;
    const mode = document.getElementById('scan-mode-controls');
    if (!mode) return;
    const box = document.createElement('div');
    box.id = 'knowledge-source-controls';
    box.className = 'scan-mode-controls';
    box.innerHTML = `
      <div class="scan-mode-title">知识来源</div>
      <label><input type="radio" name="knowledge-source" value="llm" checked> 高质量扫描原始消息</label>
      <label><input type="radio" name="knowledge-source" value="auto"> 优先复用 AI 分析结果</label>
      <label><input type="radio" name="knowledge-source" value="artifact_only"> 只使用已有 AI 分析结果</label>`;
    mode.insertAdjacentElement('afterend', box);
  },

  closeScanModal() {
    document.getElementById('scan-modal').classList.remove('show');
  },

  setScanDateRange(days) {
    const n = Math.max(1, parseInt(days, 10) || 1);
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - n + 1);
    document.getElementById('scan-date-from').value = start.toISOString().slice(0, 10);
    document.getElementById('scan-date-to').value = end.toISOString().slice(0, 10);
  },

  async runScan() {
    const chatIds = this._collectSelectedChatIds('scan-tag-picker');
    const dateFrom = document.getElementById('scan-date-from').value;
    const dateTo = document.getElementById('scan-date-to').value;
    const minScore = Number(document.getElementById('scan-min-score').value || 70);
    const maxCards = Number(document.getElementById('scan-max-cards').value || 30);
    const domain = document.getElementById('scan-domain').value || 'general';
    const modeEl = document.querySelector('input[name="scan-mode"]:checked');
    const scanMode = modeEl ? modeEl.value : 'range';
    const sourceEl = document.querySelector('input[name="knowledge-source"]:checked');
    const knowledgeSource = sourceEl ? sourceEl.value : 'llm';

    if (!dateFrom || !dateTo) { alert('请选择日期范围'); return; }
    if (!chatIds.length) { alert('请至少选择一个群聊'); return; }

    this.closeScanModal();
    const progressDiv = document.getElementById('scan-progress');
    const statusEl = document.getElementById('scan-status');
    const barEl = document.getElementById('scan-bar');
    progressDiv.style.display = 'block';
    statusEl.textContent = '准备扫描...';
    barEl.style.width = '5%';

    try {
      const resp = await fetch('/api/knowledge/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_ids: chatIds,
          date_range: [dateFrom, dateTo],
          min_score: minScore,
          max_cards: maxCards,
          domain: domain,
          scan_mode: scanMode,
          knowledge_source: knowledgeSource,
        }),
      });

      if (resp.status === 409) {
        statusEl.textContent = '❌ 已有扫描任务在运行';
        return;
      }
      if (resp.status !== 200) {
        const data = await resp.json();
        statusEl.textContent = '❌ ' + (data.error || '请求失败');
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.stage === 'progress') {
              statusEl.textContent = ev.detail || '处理中...';
              barEl.style.width = Math.round((ev.progress || 0) * 100) + '%';
            } else if (ev.stage === 'done') {
              const reused = ev.result?.reused_artifacts || 0;
              const suffix = reused ? `，复用 ${reused} 个 AI 分析结果` : '';
              statusEl.textContent = `✅ 完成！${ev.result?.card_count || 0} 条知识卡片${suffix}`;
              barEl.style.width = '100%';
              this.loadCards();
              this.loadStats();
            } else if (ev.stage === 'error') {
              statusEl.textContent = '❌ ' + (ev.message || '扫描失败');
            }
          } catch (pe) { /* ignore parse errors */ }
        }
      }
    } catch (e) {
      statusEl.textContent = '❌ ' + e.message;
    }
  },

  // -----------------------------------------------------------------------
  // Export
  // -----------------------------------------------------------------------

  exportCards(fmt) {
    const activeLi = document.querySelector('#status-nav li.active');
    const status = activeLi ? activeLi.dataset.status : '';
    const minScore = document.getElementById('filter-min-score').value;
    const tagPath = document.getElementById('filter-tag-path')?.value || '';
    const params = new URLSearchParams({ format: fmt });
    if (status) params.set('status', status);
    if (minScore) params.set('min_score', minScore);
    if (tagPath) params.set('tag_path', tagPath);
    window.open('/api/knowledge/export?' + params.toString(), '_blank');
  },

  // -----------------------------------------------------------------------
  // Obsidian sync
  // -----------------------------------------------------------------------

  async loadObsidianConfig() {
    const info = document.getElementById('obsidian-vault-info');
    const syncBtn = document.getElementById('btn-sync-obsidian');
    if (!info) return;
    try {
      const r = await fetch('/api/knowledge/obsidian-config');
      const data = await r.json();
      const path = data.vault_path || '';
      this._obsidianVaultPath = path;
      if (path) {
        info.textContent = '📁 ' + path;
        info.style.color = '#7ee787';
        if (syncBtn) syncBtn.disabled = false;
      } else {
        info.textContent = '未配置 Obsidian vault 路径';
        info.style.color = '#8b949e';
        if (syncBtn) syncBtn.disabled = false;
      }
    } catch (e) {
      info.textContent = '加载配置失败';
    }
  },

  async configureObsidianPath() {
    const current = this._obsidianVaultPath || '';
    const input = prompt('请输入 Obsidian vault 的本地绝对路径（例如 D:\\\\Obsidian\\\\MyVault）：', current);
    if (input === null) return; // user cancelled
    const path = String(input).trim();
    const resultEl = document.getElementById('obsidian-sync-result');
    try {
      const r = await fetch('/api/knowledge/obsidian-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vault_path: path }),
      });
      const data = await r.json();
      if (!r.ok) {
        alert('保存失败: ' + (data.error || '未知错误'));
        return;
      }
      await this.loadObsidianConfig();
      if (resultEl) resultEl.textContent = path ? '✓ 路径已保存' : '✓ 已清除路径';
    } catch (e) {
      alert('保存失败: ' + e.message);
    }
  },

  async syncToObsidian() {
    if (!this._obsidianVaultPath) {
      alert('请先点击「⚙ 设置路径」配置 Obsidian vault 路径');
      return;
    }
    const resultEl = document.getElementById('obsidian-sync-result');
    const btn = document.getElementById('btn-sync-obsidian');
    if (resultEl) resultEl.textContent = '⏳ 同步中...';
    if (btn) btn.disabled = true;

    // Mirror the current list filters so the sync scope matches what the user
    // sees (status/type/tag_path/q/min_score).
    const params = new URLSearchParams();
    const activeLi = document.querySelector('#status-nav li.active');
    const status = activeLi ? activeLi.dataset.status : '';
    const type = document.getElementById('filter-type').value;
    const tagPath = document.getElementById('filter-tag-path')?.value || '';
    const q = document.getElementById('knowledge-search').value.trim();
    const minScore = document.getElementById('filter-min-score').value;
    if (status) params.set('status', status);
    if (type) params.set('type', type);
    if (tagPath) params.set('tag_path', tagPath);
    if (q) params.set('q', q);
    if (minScore) params.set('min_score', minScore);

    try {
      const r = await fetch('/api/knowledge/sync-obsidian?' + params.toString(), { method: 'POST' });
      const data = await r.json();
      if (!r.ok) {
        if (resultEl) resultEl.textContent = '✗ ' + (data.error || '同步失败');
        alert(data.error || '同步失败');
        return;
      }
      const errs = data.errors && data.errors.length ? `，${data.errors.length} 个错误` : '';
      if (resultEl) {
        resultEl.innerHTML = `✓ 同步完成：写入 <b>${data.written}</b>，跳过 <b>${data.skipped}</b>${errs}`;
      }
    } catch (e) {
      if (resultEl) resultEl.textContent = '✗ 同步失败: ' + e.message;
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  // -----------------------------------------------------------------------
  // Bulk actions
  // -----------------------------------------------------------------------

  toggleBulkSelect() {
    const ids = this.cards.map(c => c.id).filter(Boolean);
    const allSelected = ids.length > 0 && ids.every(id => this.selectedIds.has(id));
    if (allSelected) {
      ids.forEach(id => this.selectedIds.delete(id));
    } else {
      ids.forEach(id => this.selectedIds.add(id));
    }
    this._syncBulkCheckboxes();
  },

  _syncBulkCheckboxes() {
    document.querySelectorAll('.bulk-cb').forEach(cb => {
      cb.checked = this.selectedIds.has(cb.dataset.cardId);
      cb.onchange = () => {
        if (cb.checked) this.selectedIds.add(cb.dataset.cardId);
        else this.selectedIds.delete(cb.dataset.cardId);
        this._updateBulkButton();
      };
    });
    this._updateBulkButton();
  },

  _updateBulkButton() {
    const btn = document.getElementById('btn-bulk-select');
    if (!btn) return;
    btn.textContent = this.selectedIds.size ? `已选 ${this.selectedIds.size} 条` : '全选当前列表';
  },

  async bulkAction(action) {
    const ids = Array.from(this.selectedIds);
    if (!ids.length) { alert('请先点击“全选当前列表”或勾选要处理的卡片'); return; }
    if (action === 'delete' && !confirm(`确定删除 ${ids.length} 条卡片？`)) return;
    await fetch('/api/knowledge/cards/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_ids: ids, action }),
    });
    this.selectedIds.clear();
    this.loadCards();
    this.loadStats();
  },

  // -----------------------------------------------------------------------
  // Schedules
  // -----------------------------------------------------------------------

  openScheduleModal(schedule) {
    this._editingScheduleId = schedule ? schedule.id : null;
    document.getElementById('sched-name').value = schedule ? (schedule.name || '') : '每日知识雷达';
    document.getElementById('sched-time').value = schedule ? (schedule.time || '08:00') : '08:00';
    document.getElementById('sched-domain').value = schedule ? (schedule.domain || 'general') : 'general';
    document.getElementById('sched-min-score').value = schedule ? (schedule.min_score || 70) : 70;
    this._renderTagPicker('sched-tag-picker', schedule ? schedule.tag_paths : []);
    document.getElementById('schedule-modal').classList.add('show');
  },

  closeScheduleModal() {
    document.getElementById('schedule-modal').classList.remove('show');
    this._editingScheduleId = null;
  },

  async saveSchedule() {
    const payload = {
      name: document.getElementById('sched-name').value.trim() || '每日知识雷达',
      time: document.getElementById('sched-time').value || '08:00',
      domain: document.getElementById('sched-domain').value || 'general',
      min_score: Number(document.getElementById('sched-min-score').value || 70),
      tag_paths: this._collectSelectedTagPaths('sched-tag-picker'),
      chat_ids: [],
      enabled: true,
    };

    const url = this._editingScheduleId
      ? '/api/knowledge/schedules/' + this._editingScheduleId
      : '/api/knowledge/schedules';
    const method = this._editingScheduleId ? 'PUT' : 'POST';

    await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    this.closeScheduleModal();
    this.loadSchedules();
  },

  async toggleSchedule(id, enabled) {
    await fetch('/api/knowledge/schedules/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    this.loadSchedules();
  },

  async editSchedule(id) {
    try {
      const r = await fetch('/api/knowledge/schedules');
      const data = await r.json();
      const sched = (data.schedules || []).find(s => s.id === id);
      if (sched) this.openScheduleModal(sched);
    } catch (e) { /* ignore */ }
  },

  async deleteSchedule(id) {
    if (!confirm('确定删除此定时任务？')) return;
    await fetch('/api/knowledge/schedules/' + id, { method: 'DELETE' });
    this.loadSchedules();
  },

  // -----------------------------------------------------------------------
  // Tag picker (shared between scan and schedule modals)
  // -----------------------------------------------------------------------

  _renderTagPicker(containerId, preselectedPaths) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!this.tags || !this.tags.length) {
      container.innerHTML = '<div style="color:#8b949e;font-size:12px">暂无标签，请先在群管理中创建</div>';
      return;
    }
    container.innerHTML = this._buildTagPickerHtml(this.tags, '', preselectedPaths || []);
  },

  _buildTagPickerHtml(nodes, prefix, preselected) {
    let html = '';
    for (const node of nodes) {
      const path = prefix ? `${prefix}/${node.name}` : node.name;
      const checked = preselected.includes(path) ? 'checked' : '';
      const chatIds = node.chat_ids || [];
      html += `<label><input type="checkbox" data-tag-path="${this.esc(path)}" ${checked}><span class="tag-picker-label" title="${this.esc(path)}">${this.esc(node.name)} (${chatIds.length})</span></label>`;
      const children = node.children || [];
      if (children.length) {
        html += `<div class="tag-children">${this._buildTagPickerHtml(children, path, preselected)}</div>`;
      }
    }
    return html;
  },

  _flattenTagPaths(nodes, prefix = '', depth = 0, out = []) {
    for (const node of nodes || []) {
      const path = prefix ? `${prefix}/${node.name}` : node.name;
      out.push({path, depth, count: (node.chat_ids || []).length});
      this._flattenTagPaths(node.children || [], path, depth + 1, out);
    }
    return out;
  },

  _collectSelectedChatIds(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    // Collect checked tag paths, then resolve to chat_ids
    const paths = [];
    container.querySelectorAll('input[data-tag-path]:checked').forEach(cb => {
      paths.push(cb.dataset.tagPath);
    });
    // Flatten tag tree to find chat_ids for selected paths
    const chatIds = new Set();
    const walk = (nodes, prefix) => {
      for (const node of nodes) {
        const path = prefix ? `${prefix}/${node.name}` : node.name;
        if (paths.includes(path)) {
          (node.chat_ids || []).forEach(id => chatIds.add(id));
        }
        walk(node.children || [], path);
      }
    };
    walk(this.tags, '');
    return Array.from(chatIds);
  },

  _collectSelectedTagPaths(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    const paths = [];
    container.querySelectorAll('input[data-tag-path]:checked').forEach(cb => {
      paths.push(cb.dataset.tagPath);
    });
    return paths;
  },

  // -----------------------------------------------------------------------
  // Utilities
  // -----------------------------------------------------------------------

  esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  },

  _debounce(fn, ms) {
    let t;
    return function() {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, arguments), ms);
    };
  },
};

// Global convert click handler
document.addEventListener('click', (e) => KnowledgeApp._handleConvert(e));

// Close menus on outside click
document.addEventListener('click', (e) => {
  if (!e.target.closest('.convert-dropdown')) {
    document.querySelectorAll('.convert-menu.show').forEach(m => m.classList.remove('show'));
  }
});

document.addEventListener('DOMContentLoaded', () => KnowledgeApp.init());
