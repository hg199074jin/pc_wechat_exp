/**
 * AI 群聊分析页面逻辑.
 * 依赖: marked.js (CDN), 原生 fetch API.
 */
const AnalysisApp = {
  schedules: [],
  results: [],
  currentScheduleId: null,
  tags: [],
  untagged: [],
  groupNameMap: {},
  selectedChatIds: new Set(),
  activeTagPath: '',
  groupSearchQuery: '',

  init() {
    this.loadGroupNameCache();
    this.ensureGroupPickerUi();
    this.ensureResultArtifactUi();
    this.ensureDateQuickControls();
    this.ensureAnalysisModeControls();
    this.loadConfig();
    this.loadTagTree();
    this.loadResults();
    this.loadSchedules();
    this.bindEvents();
  },

  ensureDateQuickControls() {
    if (document.getElementById('date-quick-actions')) return;
    const dateTo = document.getElementById('date-to');
    if (!dateTo || !dateTo.parentNode) return;
    const actions = document.createElement('div');
    actions.id = 'date-quick-actions';
    actions.className = 'date-quick-actions';
    actions.innerHTML = `
      <button class="btn-tiny" type="button" data-days="1">最近1天</button>
      <button class="btn-tiny" type="button" data-days="3">最近3天</button>
      <button class="btn-tiny" type="button" data-days="7">最近7天</button>`;
    dateTo.parentNode.appendChild(actions);

    if (!document.getElementById('analysis-date-quick-style')) {
      const style = document.createElement('style');
      style.id = 'analysis-date-quick-style';
      style.textContent = `
        .date-quick-actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
        .date-quick-actions .btn-tiny { flex: 1 1 72px; }
      `;
      document.head.appendChild(style);
    }
  },

  setQuickDateRange(days) {
    const n = Math.max(1, parseInt(days, 10) || 1);
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - n + 1);
    document.getElementById('date-from').value = start.toISOString().slice(0, 10);
    document.getElementById('date-to').value = end.toISOString().slice(0, 10);
  },

  ensureAnalysisModeControls() {
    if (document.getElementById('analysis-mode-controls')) return;
    const dateTo = document.getElementById('date-to');
    if (!dateTo || !dateTo.parentNode) return;
    const box = document.createElement('div');
    box.id = 'analysis-mode-controls';
    box.className = 'analysis-mode-controls';
    box.innerHTML = `
      <div class="analysis-mode-title">分析方式</div>
      <label class="analysis-mode-option"><input type="radio" name="analysis-mode" value="range" checked><span>按时间段分析</span></label>
      <label class="analysis-mode-option"><input type="radio" name="analysis-mode" value="daily"><span>按天分析</span></label>`;
    const quick = document.getElementById('date-quick-actions');
    (quick || dateTo).insertAdjacentElement('afterend', box);

    if (!document.getElementById('analysis-mode-style')) {
      const style = document.createElement('style');
      style.id = 'analysis-mode-style';
      style.textContent = `
        .analysis-mode-controls { margin-top: 10px; padding: 8px; border: 1px solid #30363d; border-radius: 6px; background: #0d1117; }
        .analysis-mode-title { color: #8b949e; font-size: 12px; margin-bottom: 6px; }
        .analysis-mode-controls .analysis-mode-option { display: flex; flex-direction: row; align-items: center; padding-left: 0; color: #c9d1d9; font-size: 12px; line-height: 1.8; cursor: pointer; }
        .analysis-mode-controls .analysis-mode-option + .analysis-mode-option { margin-top: 2px; }
        .analysis-mode-controls .analysis-mode-option input { flex: 0 0 auto; width: 12px; height: 12px; margin: 0 8px 0 0; }
        .analysis-mode-controls .analysis-mode-option span { flex: 1 1 auto; }
      `;
      document.head.appendChild(style);
    }
  },

  ensureGroupPickerUi() {
    if (!document.getElementById('analysis-group-picker-style')) {
      const style = document.createElement('style');
      style.id = 'analysis-group-picker-style';
      style.textContent = `
        .group-picker { margin-top: 8px; border: 1px solid #30363d; border-radius: 8px; background: #0d1117; overflow: hidden; width: 100%; box-sizing: border-box; }
        .group-picker-v2 { max-height: none; padding: 8px; }
        .group-picker-shell { display: flex; flex-direction: column; gap: 8px; width: 100%; min-width: 0; }
        .group-picker-category { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .group-picker-category select { height: 34px; }
        .group-picker-list { width: 100%; max-height: 300px; overflow-y: auto; overflow-x: hidden; scrollbar-gutter: stable; }
        .group-picker-row { display: flex; justify-content: flex-start; align-items: center; gap: 8px; height: 38px; padding: 0 10px; border-radius: 8px; box-sizing: border-box; width: 100%; min-width: 0; cursor: pointer; }
        .group-picker-row input[type="checkbox"] { flex: 0 0 14px; width: 14px; min-width: 14px; max-width: 14px; height: 14px; margin: 0; }
        .group-picker-row .label { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .group-picker-row.group { display: flex; justify-content: flex-start; align-items: center; margin: 0; color: #c9d1d9; font-size: 12px; }
        .group-picker-row.group:hover { background: #161b22; }
        .group-picker-row.group:has(input:checked) { background: rgba(88, 166, 255, 0.14); outline: 1px solid rgba(88, 166, 255, 0.28); }
        .group-picker-empty { color: #8b949e; font-size: 12px; line-height: 1.6; padding: 24px 10px; text-align: center; }
        .group-picker-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
        .group-picker-actions { display: flex; gap: 6px; }
        .group-picker-bulk-row { display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; }
        .link-btn { background: transparent; border: 0; color: #58a6ff; cursor: pointer; font-size: 12px; padding: 2px 4px; text-decoration: none; }
        .link-btn:hover { text-decoration: underline; }
      `;
      document.head.appendChild(style);
    }

    const tree = document.getElementById('tag-tree');
    if (!tree) return;
    tree.className = 'group-picker';
    if (!document.getElementById('selected-group-count')) {
      const toolbar = document.createElement('div');
      toolbar.className = 'group-picker-toolbar';
      toolbar.innerHTML = `
        <span class="hint" id="selected-group-count">已选 0 个群</span>
        <span class="group-picker-actions">
          <button class="link-btn" id="refresh-groups" type="button">刷新</button>
          <a class="link-btn" href="/groups">群管理</a>
        </span>`;
      tree.parentNode.insertBefore(toolbar, tree);
    }
    if (!document.getElementById('select-visible-groups')) {
      const bulk = document.createElement('div');
      bulk.className = 'group-picker-bulk-row';
      bulk.innerHTML = `
        <button class="link-btn" id="select-visible-groups" type="button">全选</button>
        <button class="link-btn" id="clear-visible-groups" type="button">清空</button>`;
      tree.parentNode.insertBefore(bulk, tree);
    }
  },

  ensureResultArtifactUi() {
    if (document.getElementById('analysis-result-artifact-style')) return;
    const style = document.createElement('style');
    style.id = 'analysis-result-artifact-style';
    style.textContent = `
      .result-meta-row { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; color: #8b949e; font-size: 12px; }
      .result-pill { border: 1px solid #30363d; border-radius: 999px; padding: 2px 8px; background: #0d1117; }
      .result-tabs { display: flex; gap: 6px; margin: 8px 0; border-bottom: 1px solid #30363d; padding-bottom: 6px; }
      .result-tab { background: transparent; border: 1px solid #30363d; color: #8b949e; border-radius: 6px; padding: 3px 8px; cursor: pointer; font-size: 12px; }
      .result-tab.active { color: #58a6ff; border-color: rgba(88,166,255,.55); background: rgba(88,166,255,.1); }
      .result-panel[hidden] { display: none; }
      .candidate-item, .evidence-item { border: 1px solid #30363d; border-radius: 6px; padding: 10px; margin: 8px 0; background: #0d1117; }
      .candidate-title { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
      .candidate-score { color: #f0c674; font-weight: 700; }
      .evidence-quote { margin-top: 6px; color: #c9d1d9; border-left: 3px solid #30363d; padding-left: 8px; white-space: pre-wrap; }
      .verify-ok { color: #3fb950; }
      .verify-warn { color: #f0c674; }
    `;
    document.head.appendChild(style);
  },

  // -------------------------------------------------------------------
  // Config
  // -------------------------------------------------------------------
  async loadConfig() {
    try {
      const r = await fetch('/api/analysis/config');
      const data = await r.json();
      const cfg = data.llm || {};
      document.getElementById('cfg-base-url').value = cfg.base_url || '';
      // Never put the masked key back into the input; an empty field means
      // "leave unchanged", and the placeholder shows the masked value for
      // reference. This prevents saving the masked string over the real key
      // (which broke non-'sk-' prefixed keys from 智谱/MiniMax/Kimi/通义).
      const apiKeyEl = document.getElementById('cfg-api-key');
      apiKeyEl.value = '';
      apiKeyEl.placeholder = cfg.api_key ? `${cfg.api_key}（已配置，留空保持不变）` : 'sk-...';
      document.getElementById('cfg-model').value = cfg.model || '';
      document.getElementById('cfg-temperature').value = cfg.temperature || 0.3;
      document.getElementById('cfg-max-tokens').value = cfg.max_tokens || 4096;
      const timeoutEl = document.getElementById('cfg-timeout');
      if (timeoutEl) timeoutEl.value = cfg.timeout || 120;
      this._loadProxyIntoUi(cfg.proxy || 'auto');
      document.getElementById('llm-status').textContent =
        cfg.base_url ? `当前: ${cfg.model || '?'} @ ${cfg.base_url}` : '未配置';
    } catch (e) { /* ignore */ }
  },

  async saveConfig() {
    const llm = {
      base_url: document.getElementById('cfg-base-url').value.trim(),
      api_key: document.getElementById('cfg-api-key').value.trim(),
      model: document.getElementById('cfg-model').value.trim(),
      temperature: parseFloat(document.getElementById('cfg-temperature').value) || 0.3,
      max_tokens: parseInt(document.getElementById('cfg-max-tokens').value, 10) || 4096,
      timeout: parseInt(document.getElementById('cfg-timeout')?.value, 10) || 120,
      proxy: this._collectProxyFromUi(),
    };
    if (!llm.base_url || !llm.api_key || !llm.model) {
      alert('请填写完整 LLM 配置'); return;
    }
    const r = await fetch('/api/analysis/config', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({llm}),
    });
    if (r.ok) {
      document.getElementById('settings-modal').hidden = true;
      this.loadConfig();
    } else { alert('保存失败'); }
  },

  async testConnection() {
    const out = document.getElementById('test-result');
    out.textContent = '测试中...';
    try {
      const r = await fetch('/api/analysis/test', {method: 'POST'});
      const data = await r.json();
      out.textContent = data.ok ? `✅ 成功: ${data.reply || ''}` : `❌ 失败: ${data.error}`;
    } catch (e) { out.textContent = `❌ 请求失败: ${e.message}`; }
  },

  _loadProxyIntoUi(proxy) {
    const modeSel = document.getElementById('cfg-proxy-mode');
    const customWrap = document.getElementById('cfg-proxy-custom-wrap');
    const customInput = document.getElementById('cfg-proxy-custom');
    if (!modeSel) return;
    if (proxy === 'auto' || proxy === 'none' || !proxy) {
      modeSel.value = proxy || 'auto';
      if (customWrap) customWrap.hidden = true;
    } else {
      modeSel.value = 'custom';
      if (customWrap) customWrap.hidden = false;
      if (customInput) customInput.value = proxy;
    }
    // Toggle custom input visibility when the mode changes.
    if (!modeSel.dataset.bound) {
      modeSel.addEventListener('change', () => {
        const wrap = document.getElementById('cfg-proxy-custom-wrap');
        if (wrap) wrap.hidden = modeSel.value !== 'custom';
      });
      modeSel.dataset.bound = '1';
    }
  },

  _collectProxyFromUi() {
    const modeSel = document.getElementById('cfg-proxy-mode');
    if (!modeSel) return 'auto';
    const mode = modeSel.value;
    if (mode === 'custom') {
      const custom = document.getElementById('cfg-proxy-custom')?.value.trim();
      return custom || 'auto';
    }
    return mode;  // 'auto' or 'none'
  },

  // -------------------------------------------------------------------
  // Tag tree (replaces flat group list)
  // -------------------------------------------------------------------
  async loadTagTree() {
    try {
      const r = await fetch('/api/analysis/tags');
      const data = await r.json();
      this.tags = data.tags || [];
      this.untagged = data.untagged || [];
      (this.untagged || []).forEach(g => {
        if (g.wxid) this.groupNameMap[g.wxid] = g.display_name || g.wxid;
      });
      this.renderTagTree();
      this.loadGroupNames();
    } catch (e) { /* ignore */ }
  },

  loadGroupNameCache() {
    try {
      const raw = localStorage.getItem('wechat_group_names');
      if (!raw) return;
      const cached = JSON.parse(raw);
      if (!cached || !Array.isArray(cached.groups)) return;
      cached.groups.forEach(g => {
        if (g && g.wxid) this.groupNameMap[g.wxid] = g.display_name || g.wxid;
      });
    } catch (e) { /* ignore */ }
  },

  async loadGroupNames() {
    try {
      const r = await fetch('/api/address-book/groups');
      if (!r.ok) return;
      const data = await r.json();
      (data.groups || []).forEach(g => {
        if (g.wxid) this.groupNameMap[g.wxid] = g.display_name || g.wxid;
      });
      this.renderTagTree();
      this.renderResults();
    } catch (e) { /* ignore */ }
  },

  renderTagTree() {
    const root = document.getElementById('tag-tree');
    if (!root) return;
    root.className = 'group-picker group-picker-v2';
    root.innerHTML = '';
    this._syncSelectionWithTags();
    const tagItems = this._flattenTags(this.tags);
    if (!tagItems.length) {
      root.innerHTML = '<div class="group-picker-empty">还没有已分类标签，请先到“群管理”给群聊打标签。</div>';
      this.updateSelectedCount();
      return;
    }
    if (!this.activeTagPath || !tagItems.some(item => item.pathStr === this.activeTagPath)) {
      const firstWithGroups = tagItems.find(item => item.count > 0) || tagItems[0];
      this.activeTagPath = firstWithGroups.pathStr;
    }

    const shell = document.createElement('div');
    shell.className = 'group-picker-shell';
    shell.appendChild(this._renderTagSelector(tagItems));
    shell.appendChild(this._renderGroupPane());
    root.appendChild(shell);
    this._updateTagCheckboxStates();
    this.updateSelectedCount();
  },

  _renderTagSelector(tagItems) {
    const wrap = document.createElement('div');
    wrap.className = 'group-picker-category';
    const select = document.createElement('select');
    select.className = 'text-input';
    select.setAttribute('aria-label', '选择标签');
    for (const item of tagItems) {
      const option = document.createElement('option');
      option.value = item.pathStr;
      option.textContent = `${'  '.repeat(item.depth)}${item.label} (${item.count})`;
      option.selected = item.pathStr === this.activeTagPath;
      select.appendChild(option);
    }
    select.addEventListener('change', (e) => {
      this.activeTagPath = e.target.value || '';
      this.groupSearchQuery = '';
      const search = document.getElementById('group-search');
      if (search) search.value = '';
      this.renderTagTree();
    });
    wrap.appendChild(select);
    return wrap;
  },

  _renderTagPane(tagItems) {
    const pane = document.createElement('div');
    pane.className = 'group-picker-tags';
    for (const item of tagItems) pane.appendChild(this._renderTagRow(item));
    return pane;
  },

  _renderTagRow(item) {
    const row = document.createElement('div');
    row.className = 'group-picker-row tag';
    if (item.pathStr === this.activeTagPath && !this.groupSearchQuery) row.classList.add('active');
    row.style.paddingLeft = (6 + item.depth * 14) + 'px';
    row.dataset.tagPath = item.pathStr;
    row.dataset.searchText = `${item.label} ${item.count}`.toLowerCase();
    row.innerHTML = `
      <input type="checkbox" data-tag-path="${this._esc(item.pathStr)}" aria-label="选择 ${this._esc(item.label)}">
      <span class="label" title="${this._esc(item.pathStr)}">${this._esc(item.tag.name)}</span>
      <span class="count-badge">${item.count}</span>`;
    row.querySelector('input').addEventListener('change', (e) => {
      this._setTagSelection(item.pathStr, e.target.checked);
      this._updateTagCheckboxStates();
      this.updateSelectedCount();
    });
    row.addEventListener('click', (e) => {
      if (e.target.matches('input')) return;
      this.activeTagPath = item.pathStr;
      this.groupSearchQuery = '';
      const search = document.getElementById('group-search');
      if (search) search.value = '';
      this.renderTagTree();
    });
    return row;
  },

  _renderGroupPane() {
    const pane = document.createElement('div');
    pane.className = 'group-picker-list';
    const q = this.groupSearchQuery.trim().toLowerCase();
    const rows = q ? this._searchClassifiedGroups(q) : this._groupsForPath(this.activeTagPath);

    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'group-picker-empty';
      empty.textContent = q ? '没有匹配的已分类群。' : '这个标签下还没有群。';
      pane.appendChild(empty);
      return pane;
    }

    for (const g of rows) pane.appendChild(this._renderGroupRow(g.wxid, g.pathStr));
    return pane;
  },

  _currentVisibleGroups() {
    const q = this.groupSearchQuery.trim().toLowerCase();
    return q ? this._searchClassifiedGroups(q) : this._groupsForPath(this.activeTagPath);
  },

  selectVisibleGroups() {
    this._currentVisibleGroups().forEach(g => this.selectedChatIds.add(g.wxid));
    this.renderTagTree();
  },

  clearVisibleGroups() {
    this._currentVisibleGroups().forEach(g => this.selectedChatIds.delete(g.wxid));
    this.renderTagTree();
  },

  _renderGroupRow(chatId, pathStr = '') {
    const label = this.groupNameMap[chatId] || chatId;
    const row = document.createElement('label');
    row.className = 'group-picker-row group';
    row.dataset.searchText = `${label} ${chatId} ${pathStr}`.toLowerCase();
    row.innerHTML = `
      <input type="checkbox" data-chat-id="${this._esc(chatId)}" ${this.selectedChatIds.has(chatId) ? 'checked' : ''}>
      <span class="label" title="${this._esc(label)}">${this._esc(label)}</span>`;
    row.querySelector('input').addEventListener('change', (e) => {
      if (e.target.checked) this.selectedChatIds.add(chatId);
      else this.selectedChatIds.delete(chatId);
      this._updateTagCheckboxStates();
      this.updateSelectedCount();
    });
    return row;
  },

  _flattenTags(tags, depth = 0, prefix = [], out = []) {
    for (const tag of tags || []) {
      const path = [...prefix, tag.name];
      const pathStr = path.join('/');
      out.push({tag, path, pathStr, depth, label: pathStr, count: this._countInTag(tag)});
      this._flattenTags(tag.children || [], depth + 1, path, out);
    }
    return out;
  },

  _countInTag(tag) {
    let n = (tag.chat_ids || []).length;
    for (const c of tag.children || []) n += this._countInTag(c);
    return n;
  },

  _findTagByPath(tags, pathArr) {
    if (!pathArr || pathArr.length === 0) return null;
    const [name, ...rest] = pathArr;
    for (const t of tags) {
      if (t.name === name) return rest.length === 0 ? t : this._findTagByPath(t.children || [], rest);
    }
    return null;
  },

  _collectIdsFromTag(tag, ids) {
    (tag.chat_ids || []).forEach(c => ids.add(c));
    (tag.children || []).forEach(c => this._collectIdsFromTag(c, ids));
  },

  _groupsForPath(pathStr) {
    const tag = this._findTagByPath(this.tags, (pathStr || '').split('/').filter(Boolean));
    if (!tag) return [];
    const rows = [];
    this._collectGroupRows(tag, pathStr, rows, new Set());
    return rows;
  },

  _collectGroupRows(tag, pathStr, rows, seen) {
    (tag.chat_ids || []).forEach(cid => {
      if (seen.has(cid)) return;
      seen.add(cid);
      rows.push({wxid: cid, display_name: this.groupNameMap[cid] || cid, pathStr});
    });
    (tag.children || []).forEach(child => {
      const childPath = pathStr ? `${pathStr}/${child.name}` : child.name;
      this._collectGroupRows(child, childPath, rows, seen);
    });
  },

  _allClassifiedGroups() {
    const rows = [];
    const seen = new Set();
    for (const item of this._flattenTags(this.tags)) {
      (item.tag.chat_ids || []).forEach(cid => {
        if (seen.has(cid)) return;
        seen.add(cid);
        rows.push({wxid: cid, display_name: this.groupNameMap[cid] || cid, pathStr: item.pathStr});
      });
    }
    return rows;
  },

  _searchClassifiedGroups(q) {
    return this._allClassifiedGroups().filter(g => {
      const haystack = `${g.display_name || ''} ${g.wxid} ${g.pathStr}`.toLowerCase();
      return haystack.includes(q);
    });
  },

  _setTagSelection(pathStr, checked) {
    this._groupsForPath(pathStr).forEach(g => {
      if (checked) this.selectedChatIds.add(g.wxid);
      else this.selectedChatIds.delete(g.wxid);
    });
  },

  _syncSelectionWithTags() {
    const available = new Set(this._allClassifiedGroups().map(g => g.wxid));
    this.selectedChatIds.forEach(id => {
      if (!available.has(id)) this.selectedChatIds.delete(id);
    });
  },

  _updateTagCheckboxStates() {
    document.querySelectorAll('#tag-tree input[data-tag-path]').forEach(cb => {
      const groups = this._groupsForPath(cb.dataset.tagPath);
      const total = groups.length;
      const selected = groups.filter(g => this.selectedChatIds.has(g.wxid)).length;
      cb.checked = total > 0 && selected === total;
      cb.indeterminate = selected > 0 && selected < total;
    });
  },

  collectSelectedChatIds() {
    return [...this.selectedChatIds];
  },

  updateSelectedCount() {
    const el = document.getElementById('selected-group-count');
    if (el) el.textContent = `已选 ${this.collectSelectedChatIds().length} 个群`;
  },

  // -------------------------------------------------------------------
  // Results
  // -------------------------------------------------------------------
  async loadResults(options = {}) {
    try {
      const url = options.cacheBust ? `/api/analysis/results?t=${Date.now()}` : '/api/analysis/results';
      const r = await fetch(url, {cache: 'no-store'});
      const data = await r.json();
      this.results = data.results || [];
      this.renderResults();
      return true;
    } catch (e) { /* ignore */ }
    return false;
  },

  renderResults() {
    const container = document.getElementById('results-list');
    if (this.results.length === 0) {
      container.innerHTML = '<div class="empty-state">选择群聊和日期，点击"开始分析"查看结果</div>';
      return;
    }
    container.innerHTML = '';
    this.results.forEach(r => {
      const groupName = this._groupName(r.chat_id);
      const card = document.createElement('div');
      card.className = 'result-card';
      const verifyText = r.verify_pass_rate == null
        ? '证据未核查'
        : `证据通过率 ${Math.round(r.verify_pass_rate * 100)}%`;
      card.innerHTML = `
        <div class="result-header">
          <strong title="${this._esc(r.chat_id)}">${this._esc(groupName)}</strong>
          <span class="hint">${r.date}</span>
          <button class="btn-tiny btn-danger" data-del-chat="${this._esc(r.chat_id)}" data-del-date="${r.date}">删除</button>
        </div>
        <div class="result-meta-row">
          <span class="result-pill">${this._esc(r.artifact_status === 'available' ? 'Artifact 已生成' : '旧版报告')}</span>
          <span class="result-pill">${this._esc(verifyText)}</span>
          <span class="result-pill">知识候选 ${r.knowledge_candidate_count || 0}</span>
        </div>
        <div class="result-tabs" data-chat-id="${this._esc(r.chat_id)}" data-date="${r.date}">
          <button class="result-tab active" data-result-tab="report">分析报告</button>
          <button class="result-tab" data-result-tab="candidates">知识候选</button>
          <button class="result-tab" data-result-tab="evidence">证据来源</button>
        </div>
        <div class="result-panel" data-panel="report"><div class="result-body" data-md-empty="true">加载中...</div></div>
        <div class="result-panel" data-panel="candidates" hidden>加载中...</div>
        <div class="result-panel" data-panel="evidence" hidden>加载中...</div>`;
      container.appendChild(card);
      this._loadMd(r.chat_id, r.date, card.querySelector('.result-body'));
    });
    container.onclick = e => {
      const btn = e.target.closest('button[data-del-chat]');
      if (btn) {
        if (!confirm(`删除 ${btn.dataset.delDate} 的分析结果？`)) return;
        fetch(`/api/analysis/result/${encodeURIComponent(btn.dataset.delChat)}/${btn.dataset.delDate}`, {method: 'DELETE'})
          .then(() => this.loadResults());
        return;
      }
      const tab = e.target.closest('button[data-result-tab]');
      if (tab) {
        this.activateResultTab(tab);
        return;
      }
      const importBtn = e.target.closest('button[data-import-candidates]');
      if (importBtn) this.importKnowledgeCandidates(importBtn.dataset.chatId, importBtn.dataset.date);
    };
  },

  activateResultTab(tab) {
    const tabs = tab.closest('.result-tabs');
    const card = tab.closest('.result-card');
    const target = tab.dataset.resultTab;
    tabs.querySelectorAll('.result-tab').forEach(btn => btn.classList.toggle('active', btn === tab));
    card.querySelectorAll('.result-panel').forEach(panel => {
      panel.hidden = panel.dataset.panel !== target;
    });
    if (target !== 'report') {
      const panel = card.querySelector(`.result-panel[data-panel="${target}"]`);
      if (panel && !panel.dataset.loaded) {
        this._loadArtifactPanel(tabs.dataset.chatId, tabs.dataset.date, card, target);
      }
    }
  },

  async _loadMd(chatId, date, target) {
    try {
      const r = await fetch(`/api/analysis/result/${encodeURIComponent(chatId)}/${date}`);
      if (!r.ok) { target.textContent = '加载失败'; return; }
      const data = await r.json();
      target.innerHTML = this._sanitizeHtml(marked.parse(this.sanitizeMarkdown(data.content || '', this._groupName(chatId))));
    } catch (e) { target.textContent = '加载失败'; }
  },

  async _loadArtifactPanel(chatId, date, card, panelName) {
    const panel = card.querySelector(`.result-panel[data-panel="${panelName}"]`);
    if (!panel) return;
    try {
      const r = await fetch(`/api/analysis/result/${encodeURIComponent(chatId)}/${date}/artifact`);
      if (!r.ok) {
        panel.innerHTML = '<div class="empty-state">这个结果没有结构化 artifact，重新分析后可查看。</div>';
        panel.dataset.loaded = '1';
        return;
      }
      const data = await r.json();
      const artifact = data.artifact || {};
      panel.innerHTML = panelName === 'candidates'
        ? this.renderKnowledgeCandidates(artifact, chatId, date)
        : this.renderEvidenceSources(artifact);
      panel.dataset.loaded = '1';
    } catch (e) {
      panel.textContent = '加载失败';
    }
  },

  renderKnowledgeCandidates(artifact, chatId, date) {
    const candidates = [];
    (artifact.topics || []).forEach(topic => {
      (topic.knowledge_candidates || []).forEach(c => candidates.push({...c, topic: topic.title || ''}));
    });
    if (!candidates.length) return '<div class="empty-state">没有发现值得沉淀的知识候选。</div>';
    const items = candidates.map(c => `
      <div class="candidate-item">
        <div class="candidate-title">
          <strong>${this._esc(c.title || '未命名知识')}</strong>
          <span class="candidate-score">${parseInt(c.score || 0, 10)}</span>
        </div>
        <div class="hint">${this._esc(c.type || 'note')} · ${this._esc(c.topic || '')}</div>
        <p>${this._esc(c.summary || '')}</p>
        <p class="hint">${this._esc(c.why_valuable || '')}</p>
      </div>`).join('');
    return `
      <div style="display:flex;justify-content:flex-end;margin:6px 0">
        <button class="btn-tiny" data-import-candidates="1" data-chat-id="${this._esc(chatId)}" data-date="${date}">沉淀高分知识</button>
      </div>
      ${items}`;
  },

  renderEvidenceSources(artifact) {
    const rows = [];
    (artifact.topics || []).forEach(topic => {
      (topic.evidence || []).forEach(e => rows.push({...e, topic: topic.title || ''}));
    });
    const warnings = (artifact.verify && artifact.verify.warnings) || [];
    if (!rows.length) return '<div class="empty-state">没有可显示的证据来源。</div>';
    const items = rows.map(e => `
      <div class="evidence-item">
        <div><strong>${this._esc(e.topic || '未命名话题')}</strong>
          <span class="${e.verified ? 'verify-ok' : 'verify-warn'}">${e.verified ? '已验证' : '待核实'}</span>
        </div>
        <div class="hint">msg_id=${this._esc(String(e.msg_id || ''))} · ${this._esc(e.time || '')} · ${this._esc(e.sender || '')}</div>
        <div class="evidence-quote">${this._esc(e.quote || '')}</div>
      </div>`).join('');
    const warnHtml = warnings.length
      ? `<div class="hint">核查提示：${warnings.length} 条证据需要人工复核。</div>`
      : '';
    return warnHtml + items;
  },

  async importKnowledgeCandidates(chatId, date) {
    try {
      const r = await fetch(`/api/analysis/result/${encodeURIComponent(chatId)}/${date}/knowledge-candidates/import`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({min_score: 80}),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || '导入失败');
      alert(`已沉淀 ${data.created || 0} 条知识卡片，可在知识雷达查看。`);
    } catch (e) {
      alert(`沉淀失败：${e.message}`);
    }
  },

  renderRunSummary(result) {
    const summary = result?.summary || {};
    const rows = result?.results || [];
    const skipped = rows.filter(r => r.status === 'skip');
    const errors = rows.filter(r => r.status === 'error');
    const lines = [
      `✅ 分析完成：成功 ${summary.ok || 0} 个，跳过 ${summary.skip || 0} 个，失败 ${summary.error || 0} 个`,
    ];
    skipped.slice(0, 8).forEach(r => {
      lines.push(`跳过「${r.group_name || r.chat_id}」：${r.error || '无可分析内容'}`);
    });
    errors.slice(0, 5).forEach(r => {
      lines.push(`失败「${r.group_name || r.chat_id}」：${r.error || '未知错误'}`);
    });
    return lines.map(line => this._esc(line)).join('<br>');
  },

  sanitizeMarkdown(content, groupName) {
    let lines = String(content || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim().split('\n');
    const start = lines.findIndex(line => {
      const text = line.trim();
      return text.startsWith('#') || text.includes('群聊分析报告') || text === '总体摘要' || text === '## 总体摘要';
    });
    if (start > 0) lines = lines.slice(start);
    lines = lines.filter(line => {
      const text = line.trim().toLowerCase();
      return !/^(let me|i will|i'll|here is|here's|now i|based on|this group chat|i need to|let's|the main topics)\b/.test(text);
    });
    let result = lines.join('\n').trim();
    if (result.startsWith('群聊分析报告')) result = '# ' + result;
    if (groupName && result && !result.startsWith('#') && !result.slice(0, 80).includes('群聊分析报告')) {
      result = `# 群聊分析报告：${groupName}\n\n${result}`;
    }
    return result;
  },

  // -------------------------------------------------------------------
  // Schedules
  // -------------------------------------------------------------------
  async loadSchedules() {
    try {
      const r = await fetch('/api/analysis/schedules');
      const data = await r.json();
      this.schedules = data.schedules || [];
      this.renderSchedules();
    } catch (e) { /* ignore */ }
  },

  renderSchedules() {
    const container = document.getElementById('schedule-list');
    container.innerHTML = '';
    this.schedules.forEach(s => {
      const div = document.createElement('div');
      div.className = 'schedule-item';
      div.innerHTML = `
        <div>${this._esc(s.name)}</div>
        <div class="hint">${s.time} · ${(s.chat_ids||[]).length} 群 · ${s.enabled ? '✅ 启用' : '⏸ 暂停'}</div>
        <div style="margin-top:4px">
          <button class="btn-tiny" data-edit-sched="${s.id}">编辑</button>
          <button class="btn-tiny btn-danger" data-del-sched="${s.id}">删除</button>
        </div>`;
      container.appendChild(div);
    });
    container.onclick = e => {
      const editBtn = e.target.closest('button[data-edit-sched]');
      const delBtn = e.target.closest('button[data-del-sched]');
      if (editBtn) this.openScheduleModal(editBtn.dataset.editSched);
      if (delBtn) {
        if (confirm('删除该定时任务？'))
          fetch(`/api/analysis/schedules/${delBtn.dataset.delSched}`, {method: 'DELETE'}).then(() => this.loadSchedules());
      }
    };
  },

  openScheduleModal(id) {
    this.currentScheduleId = id || null;
    const s = id ? this.schedules.find(x => x.id === id) : null;
    document.getElementById('schedule-modal-title').textContent = s ? '编辑定时任务' : '新建定时任务';
    document.getElementById('sched-name').value = s ? s.name : '';
    document.getElementById('sched-time').value = s ? s.time : '08:00';
    document.getElementById('sched-enabled').checked = s ? s.enabled : true;

    const list = document.getElementById('sched-group-list');
    list.innerHTML = '';
    // Collect all groups from tags + untagged
    const allGroups = [];
    (this.untagged || []).forEach(g => allGroups.push(g));
    const collectFromTag = (tag) => {
      (tag.chat_ids || []).forEach(cid => allGroups.push({wxid: cid, display_name: this.groupNameMap[cid] || cid}));
      (tag.children || []).forEach(collectFromTag);
    };
    (this.tags || []).forEach(collectFromTag);
    allGroups.forEach(g => {
      const checked = s && (s.chat_ids || []).includes(g.wxid);
      const label = document.createElement('label');
      label.className = 'checkbox-item';
      label.innerHTML = `<input type="checkbox" value="${this._esc(g.wxid)}" ${checked ? 'checked' : ''}><span>${this._esc(g.display_name || g.wxid)}</span>`;
      list.appendChild(label);
    });
    document.getElementById('schedule-modal').hidden = false;
  },

  async saveSchedule() {
    const chatIds = [...document.querySelectorAll('#sched-group-list input:checked')].map(cb => cb.value);
    if (chatIds.length === 0) { alert('请至少选择一个群'); return; }
    const data = {
      name: document.getElementById('sched-name').value.trim() || '未命名任务',
      time: document.getElementById('sched-time').value.trim() || '08:00',
      enabled: document.getElementById('sched-enabled').checked,
      chat_ids: chatIds,
    };
    const url = this.currentScheduleId ? `/api/analysis/schedules/${this.currentScheduleId}` : '/api/analysis/schedules';
    const method = this.currentScheduleId ? 'PUT' : 'POST';
    await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    document.getElementById('schedule-modal').hidden = true;
    this.loadSchedules();
  },

  // -------------------------------------------------------------------
  // Run analysis
  // -------------------------------------------------------------------
  async runAnalysis() {
    const chatIds = this.collectSelectedChatIds();
    if (chatIds.length === 0) { alert('请至少选择一个群'); return; }
    const dateFrom = document.getElementById('date-from').value;
    const dateTo = document.getElementById('date-to').value;
    const modeEl = document.querySelector('input[name="analysis-mode"]:checked');
    const analysisMode = modeEl ? modeEl.value : 'range';
    if (!dateFrom || !dateTo) { alert('请选择日期范围'); return; }

    document.getElementById('run-progress').hidden = false;
    document.getElementById('run-btn').disabled = true;
    const fill = document.getElementById('progress-fill');
    const text = document.getElementById('progress-text');

    try {
      const r = await fetch('/api/analysis/run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          chat_ids: chatIds,
          date_range: [dateFrom, dateTo],
          analysis_mode: analysisMode,
        }),
      });
      if (!r.ok) {
        text.textContent = '启动失败: ' + (await r.text());
        document.getElementById('run-btn').disabled = false;
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buf += decoder.decode(value);
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.stage === 'progress') {
              fill.style.width = (ev.progress * 100) + '%';
              text.textContent = ev.detail || '';
            } else if (ev.stage === 'done') {
              text.innerHTML = this.renderRunSummary(ev.result || {});
              await this.loadResults({cacheBust: true});
              setTimeout(() => this.loadResults({cacheBust: true}), 600);
            } else if (ev.stage === 'error') {
              text.textContent = '❌ 错误: ' + (ev.message || '');
            }
          } catch (e) { /* ignore parse errors */ }
        }
      }
    } catch (e) {
      text.textContent = '请求失败: ' + e.message;
    }
    document.getElementById('run-btn').disabled = false;
  },

  // -------------------------------------------------------------------
  // Events
  // -------------------------------------------------------------------
  bindEvents() {
    document.getElementById('open-settings').onclick = () => { document.getElementById('settings-modal').hidden = false; };
    document.getElementById('close-settings').onclick = () => { document.getElementById('settings-modal').hidden = true; };
    document.getElementById('save-config').onclick = () => this.saveConfig();
    document.getElementById('test-connection').onclick = () => this.testConnection();
    document.getElementById('run-btn').onclick = () => this.runAnalysis();
    document.getElementById('add-schedule').onclick = () => this.openScheduleModal(null);
    document.getElementById('close-schedule').onclick = () => { document.getElementById('schedule-modal').hidden = true; };
    document.getElementById('save-schedule').onclick = () => this.saveSchedule();
    document.getElementById('refresh-groups').onclick = () => this.loadTagTree();
    const selectVisible = document.getElementById('select-visible-groups');
    if (selectVisible) selectVisible.onclick = () => this.selectVisibleGroups();
    const clearVisible = document.getElementById('clear-visible-groups');
    if (clearVisible) clearVisible.onclick = () => this.clearVisibleGroups();
    const quickDates = document.getElementById('date-quick-actions');
    if (quickDates) {
      quickDates.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-days]');
        if (btn) this.setQuickDateRange(btn.dataset.days);
      });
    }

    document.getElementById('group-search').oninput = (e) => {
      this.groupSearchQuery = e.target.value || '';
      this.renderTagTree();
    };

    // Default dates: yesterday
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const ds = yesterday.toISOString().slice(0, 10);
    document.getElementById('date-from').value = ds;
    document.getElementById('date-to').value = ds;
  },

  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  },

  _sanitizeHtml(html) {
    // LLM-generated Markdown is rendered to HTML and inserted via innerHTML.
    // Sanitise it (no external CDN dependency — keeps the tool fully offline)
    // by dropping script/style/iframe/object/embed and stripping on* event
    // handlers and javascript:/data: URLs that would allow XSS via prompt
    // injection from analysed group chats.
    if (!html) return '';
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    tpl.content.querySelectorAll('script,style,iframe,object,embed,link,meta,base,form').forEach(el => el.remove());
    tpl.content.querySelectorAll('*').forEach(el => {
      [...el.attributes].forEach(attr => {
        const name = attr.name.toLowerCase();
        const val = (attr.value || '').trim().toLowerCase();
        if (name.startsWith('on') || val.startsWith('javascript:') || val.startsWith('data:text/html')) {
          el.removeAttribute(attr.name);
        }
      });
    });
    return tpl.innerHTML;
  },

  _groupName(chatId) {
    return this.groupNameMap[chatId] || chatId;
  },
};

document.addEventListener('DOMContentLoaded', () => AnalysisApp.init());
