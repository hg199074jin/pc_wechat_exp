/**
 * 群管理页面逻辑.
 */
function _gf(url, opts = {}) {
  opts.headers = Object.assign({ 'X-Requested-With': 'XMLHttpRequest' }, opts.headers || {});
  return fetch(url, opts);
}

const GroupsApp = {
  CACHE_KEY: 'wechat_group_names',
  CACHE_TTL: 7 * 24 * 3600 * 1000,

  tags: [],
  untagged: [],
  selectedTagPath: null,
  sortKey: 'name',
  sortDir: 'asc',
  loadWindowDays: '3',
  blacklist: [],
  activityStatsLoaded: false,
  activityStatsLoading: false,
  dirty: false,
  _saveTimer: null,

  async init() {
    this._nameMap = {};
    this._groupStats = {};
    this._allGroups = [];
    this.loadWindowDays = localStorage.getItem('groups_load_window_days') || '3';
    this.ensureSortControls();
    this.bindEvents();
    await this._loadBlacklist();

    const hasLocalCache = this._loadNameMapCache();
    this._updateLoading(
      hasLocalCache ? `已读取本地缓存 ${this._allGroups.length}/${this._allGroups.length} 个群名称` : '正在加载标签...',
      hasLocalCache ? 100 : 5
    );

    try {
      if (hasLocalCache) {
        await this._loadTagsData();
        this._syncUntaggedWithTags();
        this.render();
        this._hideLoading();
        this._refreshGroupData({silent: true});
        return;
      }

      this._updateLoading('正在解析群名称 0/0...', 10);
      await this._buildNameMap();
      this._updateLoading('正在加载标签...', 95);
      await this._loadTagsData();
      this._syncUntaggedWithTags();
      this.render();
    } catch (e) {
      console.error('init failed:', e);
      this.render();
    }
    this._hideLoading();
  },

  ensureSortControls() {
    if (!document.getElementById('groups-sort-style')) {
      const style = document.createElement('style');
      style.id = 'groups-sort-style';
      style.textContent = `
        .group-list .header { gap: 10px; flex-wrap: wrap; align-items: flex-start; }
        .group-list .search { width: min(260px, 100%); }
        .group-load-controls { display: flex; align-items: center; gap: 8px; color: #8b949e; font-size: 12px; margin-bottom: 8px; }
        .group-load-controls .text-input { width: 150px; }
        .group-sort-controls { display: flex; gap: 8px; align-items: center; }
        .group-sort-controls .text-input { width: 130px; }
        .group-item span:nth-child(2) { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .group-meta { margin-left: auto; color: #8b949e; font-size: 12px; white-space: nowrap; }
      `;
      document.head.appendChild(style);
    }
    if (document.getElementById('group-sort-key')) return;
    const search = document.getElementById('group-search');
    if (!search || !search.parentNode) return;
    if (!document.getElementById('group-load-days')) {
      const header = document.querySelector('.group-list .header');
      if (header && header.parentNode) {
        const loadControls = document.createElement('div');
        loadControls.className = 'group-load-controls';
        loadControls.innerHTML = `
          <span>加载范围</span>
          <select id="group-load-days" class="text-input">
            <option value="1">最近1天有会话</option>
            <option value="3">最近3天有会话</option>
            <option value="7">最近7天有会话</option>
            <option value="30">最近30天有会话</option>
            <option value="0">全部群</option>
          </select>`;
        header.parentNode.insertBefore(loadControls, header.nextSibling);
      }
    }
    const controls = document.createElement('div');
    controls.className = 'group-sort-controls';
    controls.innerHTML = `
      <select id="group-sort-key" class="text-input">
        <option value="name">按名称</option>
        <option value="last_time">按最近对话</option>
        <option value="active_3d">按近3天活跃度</option>
      </select>
      <select id="group-sort-dir" class="text-input">
        <option value="asc">正序</option>
        <option value="desc">倒序</option>
      </select>`;
    search.parentNode.appendChild(controls);
    const loadDays = document.getElementById('group-load-days');
    if (loadDays) loadDays.value = this.loadWindowDays;
  },

  _updateLoading(text, pct) {
    const t = document.getElementById('loading-text');
    const b = document.getElementById('loading-bar');
    if (t) t.textContent = text;
    if (b) b.style.width = Math.max(0, Math.min(100, pct || 0)) + '%';
  },

  _hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.style.display = 'none';
  },

  _loadNameMapCache() {
    try {
      const cached = localStorage.getItem(this.CACHE_KEY);
      if (!cached) return false;
      const parsed = JSON.parse(cached);
      if (!parsed || !parsed.ts || Date.now() - parsed.ts >= this.CACHE_TTL) return false;

      const groups = Array.isArray(parsed.groups) ? parsed.groups : [];
      this._nameMap = parsed.map || {};
      this._rememberGroups(groups);
      this._allGroups = this._filterByLoadWindow(this._filterBlacklisted(groups.filter(g => g && g.wxid)));
      return this._allGroups.length > 0;
    } catch (e) {
      return false;
    }
  },

  _rememberGroups(groups) {
    for (const g of groups || []) {
      if (!g || !g.wxid) continue;
      this._nameMap[g.wxid] = g.display_name || this._nameMap[g.wxid] || g.wxid;
      this._groupStats[g.wxid] = {
        ...(this._groupStats[g.wxid] || {}),
        wxid: g.wxid,
        display_name: g.display_name || this._nameMap[g.wxid] || g.wxid,
        msg_count: Number(g.msg_count || 0),
        last_msg_time: g.last_msg_time || null,
        active_3d: Number(g.active_3d || this._groupStats[g.wxid]?.active_3d || 0),
      };
    }
  },

  _loadWindowQuery(extra = {}) {
    const params = new URLSearchParams();
    params.set('recent_days', this.loadWindowDays || '3');
    Object.entries(extra).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') params.set(k, v);
    });
    return params.toString();
  },

  _filterByLoadWindow(groups) {
    const days = parseInt(this.loadWindowDays || '3', 10);
    if (!days) return groups || [];
    const cutoff = Math.floor(Date.now() / 1000) - days * 86400;
    return (groups || []).filter(g => this._toTs(g.last_msg_time) >= cutoff);
  },

  _saveNameMapCache() {
    try {
      const map = {};
      for (const g of this._allGroups || []) {
        if (g.wxid) map[g.wxid] = g.display_name || this._nameMap[g.wxid] || g.wxid;
      }
      localStorage.setItem(this.CACHE_KEY, JSON.stringify({
        ts: Date.now(),
        groups: this._allGroups || [],
        map,
      }));
      this._nameMap = {...this._nameMap, ...map};
      this._rememberGroups(this._allGroups);
    } catch (e) { /* ignore */ }
  },

  _blacklistIds() {
    return new Set((this.blacklist || []).map(g => g.wxid).filter(Boolean));
  },

  _filterBlacklisted(groups) {
    const ids = this._blacklistIds();
    if (!ids.size) return groups || [];
    return (groups || []).filter(g => g && g.wxid && !ids.has(g.wxid));
  },

  async _loadBlacklist() {
    try {
      const r = await _gf('/api/address-book/groups/blacklist');
      if (!r.ok) return;
      const data = await r.json();
      this.blacklist = data.blacklist || [];
    } catch (e) {
      console.error('_loadBlacklist failed:', e);
    }
  },

  async _loadTagsData() {
    try {
      const r = await _gf('/api/analysis/tags');
      if (!r.ok) return;
      const data = await r.json();
      this.tags = data.tags || [];
      this.untagged = this._filterBlacklisted(data.untagged || []);
      for (const g of this.untagged) {
        if (g.display_name) this._nameMap[g.wxid] = g.display_name;
      }
      this._rememberGroups(this.untagged);
      this.dirty = false;
    } catch (e) {
      console.error('_loadTagsData failed:', e);
    }
  },

  async loadTags() {
    try {
      await this._loadTagsData();
      this._syncUntaggedWithTags();
      this.render();
    } catch (e) {
      console.error('loadTags failed:', e);
      this.render();
    }
  },

  render() {
    this.renderTree();
    this.renderCurrent();
    this._populateMoveTargets();
  },

  renderTree() {
    const root = document.getElementById('tag-tree');
    root.innerHTML = '';
    root.appendChild(this._renderTagNode({name: '未分类', isUntagged: true, chat_ids: []}, []));
    for (const tag of this.tags) {
      root.appendChild(this._renderTagNode(tag, [tag.name]));
    }
  },

  _renderTagNode(tag, path) {
    const div = document.createElement('div');
    div.className = 'tag-node';
    const pathStr = path.join('/');
    const isUntagged = !!tag.isUntagged;
    const isSelected = (this.selectedTagPath === null && isUntagged) || (this.selectedTagPath === pathStr);
    if (isSelected) div.classList.add('selected');

    const count = isUntagged ? this.untagged.length : this._countInTag(tag);
    const nameSpan = document.createElement('span');
    nameSpan.className = 'name';
    nameSpan.textContent = isUntagged ? '未分类' : tag.name;
    const countSpan = document.createElement('span');
    countSpan.className = 'count';
    countSpan.textContent = `(${count})`;
    div.appendChild(nameSpan);
    div.appendChild(countSpan);

    if (!isUntagged) {
      const addBtn = document.createElement('span');
      addBtn.textContent = '+';
      addBtn.style.cssText = 'margin-left:6px;color:#58a6ff;cursor:pointer;font-weight:bold;font-size:14px;';
      addBtn.title = '新建子标签';
      addBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._addChildTag(path);
      });
      div.appendChild(addBtn);
    }

    div.dataset.path = pathStr;
    div.addEventListener('click', (e) => {
      e.stopPropagation();
      const search = document.getElementById('group-search');
      if (search) search.value = '';
      this.selectedTagPath = isUntagged ? null : pathStr;
      this.render();
    });

    if (!isUntagged) {
      div.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.showContextMenu(e, tag, path);
      });
    }

    if (tag.children && tag.children.length) {
      const ch = document.createElement('div');
      ch.className = 'children';
      for (const c of tag.children) {
        ch.appendChild(this._renderTagNode(c, [...path, c.name]));
      }
      div.appendChild(ch);
    }
    return div;
  },

  _countInTag(tag) {
    let n = (tag.chat_ids || []).length;
    for (const c of tag.children || []) n += this._countInTag(c);
    return n;
  },

  renderCurrent() {
    const isUntagged = this.selectedTagPath === null;
    const nameEl = document.getElementById('current-tag-name');
    const countEl = document.getElementById('current-tag-count');
    const itemsEl = document.getElementById('group-items');
    const groups = this._groupsInCurrent();
    nameEl.textContent = isUntagged ? '未分类' : this.selectedTagPath;
    countEl.textContent = `(${groups.length} 个群)`;
    itemsEl.innerHTML = '';

    const q = (document.getElementById('group-search').value || '').toLowerCase();
    const filtered = groups.filter(g => (this._displayName(g).toLowerCase()).includes(q));
    const sorted = this._sortGroups(filtered);
    if (sorted.length === 0) {
      itemsEl.innerHTML = '<div class="empty-state">无群聊</div>';
      this._updateMoveButton();
      return;
    }

    for (const g of sorted) {
      const row = document.createElement('div');
      row.className = 'group-item';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.wxid = g.wxid;
      cb.addEventListener('change', () => this._updateMoveButton());

      const span = document.createElement('span');
      span.textContent = this._displayName(g);
      span.title = g.wxid;

      row.appendChild(cb);
      row.appendChild(span);

      const meta = document.createElement('span');
      meta.className = 'group-meta';
      meta.textContent = this._groupMetaText(g);
      row.appendChild(meta);

      if (!isUntagged) {
        const rmBtn = document.createElement('button');
        rmBtn.type = 'button';
        rmBtn.textContent = '✕';
        rmBtn.className = 'btn-tiny btn-danger remove-group-btn';
        rmBtn.dataset.wxid = g.wxid;
        rmBtn.style.cssText = 'margin-left:4px;padding:1px 6px;font-size:11px;';
        rmBtn.title = '从标签中移除';
        row.appendChild(rmBtn);
      }

      row.addEventListener('click', (e) => {
        if (e.target.closest('input') || e.target.closest('button')) return;
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event('change'));
      });
      itemsEl.appendChild(row);
    }
    this._updateMoveButton();
  },

  _displayName(group) {
    return this._nameMap[group.wxid] || group.display_name || group.wxid;
  },

  _groupRecord(wxid, displayName = '') {
    const stats = this._groupStats[wxid] || {};
    return {
      wxid,
      display_name: displayName || stats.display_name || this._nameMap[wxid] || wxid,
      msg_count: Number(stats.msg_count || 0),
      last_msg_time: stats.last_msg_time || null,
      active_3d: Number(stats.active_3d || 0),
    };
  },

  _sortGroups(groups) {
    const dir = this.sortDir === 'desc' ? -1 : 1;
    const arr = [...(groups || [])];
    arr.sort((a, b) => {
      if (this.sortKey === 'last_time') {
        return (this._toTs(a.last_msg_time) - this._toTs(b.last_msg_time)) * dir;
      }
      if (this.sortKey === 'active_3d') {
        return ((a.active_3d || 0) - (b.active_3d || 0)) * dir;
      }
      return this._displayName(a).localeCompare(this._displayName(b), 'zh-Hans-CN') * dir;
    });
    return arr;
  },

  _toTs(value) {
    if (!value) return 0;
    if (typeof value === 'number') return value > 1000000000000 ? Math.floor(value / 1000) : value;
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
  },

  _formatTime(value) {
    const ts = this._toTs(value);
    if (!ts) return '无对话记录';
    const d = new Date(ts * 1000);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  },

  _groupMetaText(group) {
    const last = this._formatTime(group.last_msg_time);
    if (this.sortKey === 'active_3d') {
      return `近3天 ${group.active_3d || 0} 条 · ${last}`;
    }
    return `最近 ${last}`;
  },

  _updateMoveButton() {
    const checked = document.querySelectorAll('#group-items input:checked');
    const btn = document.getElementById('move-selected-btn');
    if (btn) btn.disabled = checked.length === 0;
    const blacklistBtn = document.getElementById('blacklist-selected-btn');
    if (blacklistBtn) {
      const isUntagged = this.selectedTagPath === null;
      blacklistBtn.style.display = isUntagged ? '' : 'none';
      blacklistBtn.disabled = !isUntagged || checked.length === 0;
    }
    const hint = document.getElementById('move-hint');
    if (hint) hint.textContent = checked.length > 0 ? `已选 ${checked.length} 个群` : '先在上方勾选群，再选择目标标签';
  },

  _populateMoveTargets() {
    const select = document.getElementById('move-target');
    if (!select) return;
    select.innerHTML = '<option value="">-- 选择目标标签 --</option>';

    const append = (tag, path, depth) => {
      const prefix = depth === 0 ? '' : `${'  '.repeat(depth)}└ `;
      select.appendChild(new Option(`${prefix}${tag.name}`, path.join('/')));
      for (const child of tag.children || []) append(child, [...path, child.name], depth + 1);
    };
    for (const tag of this.tags) append(tag, [tag.name], 0);
  },

  _groupsInCurrent() {
    if (this.selectedTagPath === null) {
      return (this.untagged || []).map(g => this._groupRecord(g.wxid, g.display_name));
    }
    const tag = this._findTagByPath(this.tags, this.selectedTagPath.split('/'));
    if (!tag) return [];
    return this._collectGroupsFromTag(tag);
  },

  _collectGroupsFromTag(tag) {
    const groups = [];
    for (const cid of tag.chat_ids || []) {
      groups.push(this._groupRecord(cid));
    }
    for (const c of tag.children || []) groups.push(...this._collectGroupsFromTag(c));
    return groups;
  },

  async _buildNameMap() {
    try {
      const r = await _gf(`/api/address-book/groups/stream?${this._loadWindowQuery()}`);
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.stage === 'progress') {
            const pct = ev.total ? (ev.done / ev.total) * 100 : (ev.progress || 0) * 100;
            this._updateLoading(ev.detail || `正在解析群名称 ${ev.done || 0}/${ev.total || 0}`, pct);
          } else if (ev.stage === 'done') {
            this._allGroups = ev.groups || [];
            this._nameMap = {};
            this._rememberGroups(this._allGroups);
            this._saveNameMapCache();
            this._updateLoading(`群名称加载完成 ${this._allGroups.length}/${this._allGroups.length}`, 100);
          } else if (ev.stage === 'error') {
            throw new Error(ev.message || '加载失败');
          }
        }
      }
    } catch (e) {
      console.error('_buildNameMap failed:', e);
      const fallback = await _gf(`/api/address-book/groups?${this._loadWindowQuery()}`);
      const data = await fallback.json();
      this._allGroups = data.groups || [];
      this._rememberGroups(this._allGroups);
      this._saveNameMapCache();
    }
  },

  async _refreshGroupData(options = {}) {
    try {
      const activity = !!options.activity;
      if (activity && this.activityStatsLoading) return;
      if (activity) this.activityStatsLoading = true;
      const extra = {};
      if (activity) extra.activity = '1';
      if (options.force) extra.force = '1';
      const r = await _gf(`/api/address-book/groups?${this._loadWindowQuery(extra)}`);
      if (!r.ok) return;
      const data = await r.json();
      if (!Array.isArray(data.groups)) return;
      this._allGroups = this._filterBlacklisted(data.groups);
      this._rememberGroups(this._allGroups);
      if (activity) this.activityStatsLoaded = true;
      this._saveNameMapCache();
      this._syncUntaggedWithTags();
      this.render();
    } catch (e) {
      console.error('_refreshGroupData failed:', e);
    } finally {
      if (options.activity) this.activityStatsLoading = false;
    }
  },

  _findTagByPath(tags, pathArr) {
    if (pathArr.length === 0) return null;
    const [name, ...rest] = pathArr;
    for (const t of tags) {
      if (t.name === name) {
        if (rest.length === 0) return t;
        return this._findTagByPath(t.children || [], rest);
      }
    }
    return null;
  },

  _moveChatIdsTo(wxids, targetPath) {
    const ids = [...new Set(wxids || [])].filter(Boolean);
    if (ids.length === 0) return;
    this._removeChatIdsFromTags(this.tags, ids);
    this._removeFromUntagged(ids);

    const target = this._findTagByPath(this.tags, targetPath.split('/'));
    if (target) {
      target.chat_ids = target.chat_ids || [];
      target.chat_ids = [...new Set([...target.chat_ids, ...ids])];
    }
    this._syncUntaggedWithTags();
    this.selectedTagPath = targetPath;
    this.dirty = true;
    this.render();
    this._scheduleSave();
  },

  _removeChatIdsFromTags(tags, ids) {
    const idSet = new Set(ids);
    for (const t of tags) {
      t.chat_ids = (t.chat_ids || []).filter(c => !idSet.has(c));
      this._removeChatIdsFromTags(t.children || [], ids);
    }
  },

  _removeFromUntagged(ids) {
    const idSet = new Set(ids);
    this.untagged = (this.untagged || []).filter(g => !idSet.has(g.wxid));
  },

  _addToUntagged(ids) {
    const existing = new Set((this.untagged || []).map(g => g.wxid));
    for (const cid of ids) {
      if (!existing.has(cid)) {
        this.untagged.push(this._groupRecord(cid));
        existing.add(cid);
      }
    }
    this.untagged.sort((a, b) => this._displayName(a).localeCompare(this._displayName(b), 'zh-Hans-CN'));
  },

  _removeFromTag(wxid) {
    if (this.selectedTagPath === null || !wxid) return;
    const tag = this._findTagByPath(this.tags, this.selectedTagPath.split('/'));
    if (!tag) return;
    this._removeChatIdsFromTags([tag], [wxid]);
    this._addToUntagged([wxid]);
    this._syncUntaggedWithTags();
    this.dirty = true;
    this.render();
    this._scheduleSave();
  },

  _collectTaggedChatIds(tags = this.tags, out = new Set()) {
    for (const tag of tags || []) {
      for (const cid of tag.chat_ids || []) out.add(cid);
      this._collectTaggedChatIds(tag.children || [], out);
    }
    return out;
  },

  _syncUntaggedWithTags() {
    const tagged = this._collectTaggedChatIds();
    if (this._allGroups && this._allGroups.length) {
      this.untagged = this._allGroups
        .filter(g => g && g.wxid && !tagged.has(g.wxid) && !this._blacklistIds().has(g.wxid))
        .map(g => this._groupRecord(g.wxid, this._nameMap[g.wxid] || g.display_name || g.wxid));
      return;
    }
    const blocked = this._blacklistIds();
    this.untagged = (this.untagged || []).filter(g => !tagged.has(g.wxid) && !blocked.has(g.wxid));
  },

  async _addSelectedToBlacklist() {
    if (this.selectedTagPath !== null) return;
    const checked = [...document.querySelectorAll('#group-items input:checked')].map(cb => cb.dataset.wxid);
    if (checked.length === 0) { showToast('请先勾选要加入黑名单的群', 'warning'); return; }
    if (!confirm(`将 ${checked.length} 个群加入黑名单？这些群之后不会参与群管理加载。`)) return;
    const groups = checked.map(wxid => this._groupRecord(wxid));
    try {
      const r = await _gf('/api/address-book/groups/blacklist', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({groups}),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      this.blacklist = data.blacklist || [];
      const ids = new Set(checked);
      this._allGroups = (this._allGroups || []).filter(g => !ids.has(g.wxid));
      this.untagged = (this.untagged || []).filter(g => !ids.has(g.wxid));
      this._saveNameMapCache();
      this.render();
    } catch (e) {
      showToast('加入黑名单失败：' + e.message, 'error');
    }
  },

  _renderBlacklistModal() {
    const list = document.getElementById('blacklist-items');
    if (!list) return;
    if (!this.blacklist.length) {
      list.innerHTML = '<div class="empty-state">黑名单为空</div>';
      return;
    }
    list.innerHTML = '';
    for (const item of this.blacklist) {
      const row = document.createElement('div');
      row.className = 'blacklist-row';
      row.innerHTML = `
        <div class="blacklist-name">
          <div title="${this._esc(item.display_name || item.wxid)}">${this._esc(item.display_name || item.wxid)}</div>
          <div class="blacklist-id">${this._esc(item.wxid || '')}</div>
        </div>
        <button class="btn-tiny btn-secondary" type="button" data-remove-blacklist="${this._esc(item.wxid || '')}">移出</button>`;
      list.appendChild(row);
    }
  },

  async _openBlacklistModal() {
    await this._loadBlacklist();
    this._renderBlacklistModal();
    const modal = document.getElementById('blacklist-modal');
    if (modal) modal.hidden = false;
  },

  async _removeFromBlacklist(wxid) {
    if (!wxid) return;
    try {
      const r = await _gf(`/api/address-book/groups/blacklist?wxid=${encodeURIComponent(wxid)}`, {method: 'DELETE'});
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      this.blacklist = data.blacklist || [];
      this._renderBlacklistModal();
      const restored = this._groupRecord(wxid);
      if (!this._allGroups.some(g => g.wxid === wxid)) this._allGroups.push(restored);
      this._syncUntaggedWithTags();
      this.render();
      this._refreshGroupData({force: true, activity: this.sortKey === 'active_3d'}).catch(() => {});
    } catch (e) {
      showToast('移出黑名单失败：' + e.message, 'error');
    }
  },

  // ----- AI auto-classify -----
  async autoClassify() {
    const modal = document.getElementById('progress-modal');
    modal.hidden = false;
    const fill = document.getElementById('progress-fill');
    const text = document.getElementById('progress-text');
    fill.style.width = '0%';
    text.textContent = '开始 AI 分类...';
    try {
      const r = await _gf('/api/analysis/tags/auto-classify', {method: 'POST'});
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
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
              const result = ev.result;
              if (result && result.tags && confirm(`AI 分类完成！生成 ${result.tags.length} 个分类，是否应用？`)) {
                this.tags = result.tags;
                this._syncUntaggedWithTags();
                this.dirty = true;
                this.render();
                this._scheduleSave();
              }
              modal.hidden = true;
              return;
            } else if (ev.stage === 'error') {
              text.textContent = '错误: ' + (ev.message || '');
              return;
            }
          } catch (e) { /* ignore parse errors */ }
        }
      }
    } catch (e) {
      text.textContent = '请求失败: ' + e.message;
    }
    modal.hidden = true;
  },

  // ----- Save -----
  async save(options = {}) {
    const silent = !!options.silent;
    const r = await _gf('/api/analysis/tags', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tags: this.tags}),
    });
    if (r.ok) {
      this.dirty = false;
      const btn = document.getElementById('save-btn');
      if (btn) btn.textContent = silent ? '已自动保存' : '💾 保存修改';
      if (!silent) {
        showToast('已保存', 'success');
        this.loadTags();
      }
    } else {
      if (!silent) showToast('保存失败', 'error');
      const btn = document.getElementById('save-btn');
      if (btn) btn.textContent = '保存失败';
    }
  },

  _scheduleSave() {
    const btn = document.getElementById('save-btn');
    if (btn) btn.textContent = '保存中...';
    if (this._saveTimer) clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => {
      this.save({silent: true}).catch(e => {
        console.error('auto save failed:', e);
        const saveBtn = document.getElementById('save-btn');
        if (saveBtn) saveBtn.textContent = '保存失败';
      });
    }, 500);
  },

  // ----- Context menu -----
  showContextMenu(e, tag, path) {
    const existing = document.querySelector('.context-menu');
    if (existing) existing.remove();
    const menu = document.createElement('div');
    menu.className = 'context-menu';
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';
    menu.innerHTML = `
      <div class="item" data-action="rename">重命名</div>
      <div class="item" data-action="add-child">+ 新建子标签</div>
      <div class="item" data-action="delete" style="color:#f85149">删除</div>
    `;
    document.body.appendChild(menu);
    menu.addEventListener('click', (ev) => {
      const item = ev.target.closest('.item');
      const action = item ? item.dataset.action : '';
      if (action === 'rename') this._renameTag(path);
      if (action === 'add-child') this._addChildTag(path);
      if (action === 'delete') this._deleteTag(path);
      menu.remove();
    });
    setTimeout(() => document.addEventListener('click', () => menu.remove(), {once: true}), 100);
  },

  _renameTag(path) {
    const newName = prompt('新标签名：');
    if (!newName) return;
    const tag = this._findTagByPath(this.tags, path);
    if (tag) {
      tag.name = newName;
      this.selectedTagPath = path.length === 1 ? newName : [...path.slice(0, -1), newName].join('/');
      this.dirty = true;
      this.render();
      this._scheduleSave();
    }
  },

  _addChildTag(path) {
    const newName = prompt('子标签名：');
    if (!newName) return;
    const tag = this._findTagByPath(this.tags, path);
    if (tag) {
      tag.children = tag.children || [];
      tag.children.push({name: newName, chat_ids: []});
      this.dirty = true;
      this.render();
      this._scheduleSave();
    }
  },

  _deleteTag(path) {
    if (!confirm(`删除标签"${path.join('/')}"？群聊将归入"未分类"`)) return;
    const tag = this._findTagByPath(this.tags, path);
    const wxids = [];
    if (tag) {
      const collect = (t) => {
        (t.chat_ids || []).forEach(c => wxids.push(c));
        (t.children || []).forEach(collect);
      };
      collect(tag);
    }

    if (path.length === 1) {
      this.tags = this.tags.filter(t => t.name !== path[0]);
    } else {
      const parent = this._findTagByPath(this.tags, path.slice(0, -1));
      if (parent) parent.children = (parent.children || []).filter(c => c.name !== path[path.length - 1]);
    }

    this._addToUntagged(wxids);
    if (this.selectedTagPath === path.join('/') || (this.selectedTagPath || '').startsWith(path.join('/') + '/')) {
      this.selectedTagPath = null;
    }
    this._syncUntaggedWithTags();
    this.dirty = true;
    this.render();
    this._scheduleSave();
  },

  _addRootTag() {
    const newName = prompt('新标签名：');
    if (!newName) return;
    this.tags.push({name: newName, chat_ids: []});
    this.dirty = true;
    this.render();
    this._scheduleSave();
  },

  // ----- Events -----
  bindEvents() {
    document.getElementById('auto-classify-btn').onclick = () => this.autoClassify();
    document.getElementById('save-btn').onclick = () => this.save();
    document.getElementById('add-root-tag-btn').onclick = () => this._addRootTag();
    document.getElementById('group-search').oninput = () => this.renderCurrent();
    const blacklistManageBtn = document.getElementById('blacklist-manage-btn');
    if (blacklistManageBtn) blacklistManageBtn.onclick = () => this._openBlacklistModal();
    const blacklistSelectedBtn = document.getElementById('blacklist-selected-btn');
    if (blacklistSelectedBtn) blacklistSelectedBtn.onclick = () => this._addSelectedToBlacklist();
    const closeBlacklistBtn = document.getElementById('close-blacklist');
    if (closeBlacklistBtn) closeBlacklistBtn.onclick = () => {
      const modal = document.getElementById('blacklist-modal');
      if (modal) modal.hidden = true;
    };
    const blacklistItems = document.getElementById('blacklist-items');
    if (blacklistItems) {
      blacklistItems.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-remove-blacklist]');
        if (btn) this._removeFromBlacklist(btn.dataset.removeBlacklist);
      });
    }
    const loadDays = document.getElementById('group-load-days');
    if (loadDays) {
      loadDays.value = this.loadWindowDays;
      loadDays.onchange = (e) => {
        this.loadWindowDays = e.target.value || '3';
        localStorage.setItem('groups_load_window_days', this.loadWindowDays);
        this.activityStatsLoaded = false;
        this._updateLoading('正在按时间范围加载群聊...', 35);
        const overlay = document.getElementById('loading-overlay');
        if (overlay) overlay.style.display = 'flex';
        this._refreshGroupData({activity: this.sortKey === 'active_3d'}).finally(() => this._hideLoading());
      };
    }
    const sortKey = document.getElementById('group-sort-key');
    if (sortKey) {
      sortKey.onchange = (e) => {
        this.sortKey = e.target.value || 'name';
        if (this.sortKey === 'active_3d' && !this.activityStatsLoaded) {
          this._refreshGroupData({activity: true});
        }
        this.renderCurrent();
      };
    }
    const sortDir = document.getElementById('group-sort-dir');
    if (sortDir) {
      sortDir.onchange = (e) => {
        this.sortDir = e.target.value || 'asc';
        this.renderCurrent();
      };
    }
    document.getElementById('move-selected-btn').onclick = () => {
      const target = document.getElementById('move-target').value;
      if (!target) { showToast('请先选择目标标签', 'warning'); return; }
      const checked = [...document.querySelectorAll('#group-items input:checked')].map(cb => cb.dataset.wxid);
      if (checked.length === 0) { showToast('请先勾选要移动的群', 'warning'); return; }
      this._moveChatIdsTo(checked, target);
    };
    document.getElementById('group-items').addEventListener('click', (e) => {
      const btn = e.target.closest('.remove-group-btn');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      this._removeFromTag(btn.dataset.wxid);
    });
  },

  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  },
};

document.addEventListener('DOMContentLoaded', () => GroupsApp.init());
