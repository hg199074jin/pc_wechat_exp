// Pure helper functions — no DOM dependencies, no state references
// Loaded before components.js

const MSG_TYPE_LABELS = { 1:'文本', 3:'图片', 6:'文件', 34:'语音', 42:'名片', 43:'视频', 47:'表情', 48:'位置', 49:'链接', 50:'通话', 10000:'系统', 10002:'系统' };
const MSG_TYPE_CSS = { 3:'image', 34:'voice', 43:'video', 48:'location', 49:'link', 42:'card', 6:'file', 47:'emoji', 50:'call', 10000:'system', 10002:'system' };

const formatTime = (ts) => {
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

const formatDateStr = (dateStr) => {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const days = ['日','一','二','三','四','五','六'];
  return `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日 星期${days[d.getDay()]}`;
};

const formatSize = (bytes) => {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + 'MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + 'KB';
  return bytes + 'B';
};

const _AVATAR_COLORS = ['#e94560','#569cd6','#56d364','#f0883e','#c084fc','#ffd54f','#58a6ff'];

const _avatarColor = (id) => {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = ((h << 5) - h) + id.charCodeAt(i);
  return _AVATAR_COLORS[Math.abs(h) % _AVATAR_COLORS.length];
};

const _avatarChar = (name) => {
  if (!name) return '?';
  for (let c of name) {
    if (/[一-鿿぀-ヿ가-힯\w]/.test(c)) return c;
  }
  return '?';
};

const _sysMsgIcon = (text) => {
  if (!text) return '';
  const t = String(text);
  if (/红包/.test(t)) return '🧧 ';
  if (/邀请.*加入/.test(t)) return '👋 ';
  if (/修改了群公告/.test(t) || /发布了群公告/.test(t)) return '📝 ';
  if (/改群名为/.test(t)) return '✏️ ';
  if (/移出群聊/.test(t) || /被移除/.test(t)) return '🚫 ';
  if (/退出了群聊/.test(t)) return '🚶 ';
  return '';
};

// Convert GCJ-02 (Mars coordinate system, used by WeChat location / Amap) to BD-09 (Baidu Maps)
// WeChat stores location coordinates in GCJ-02. Amap uses GCJ-02 natively; Baidu Maps requires BD-09.
const gcj02ToBd09 = (lng, lat) => {
  const X_PI = Math.PI * 3000.0 / 180.0;
  const z = Math.sqrt(lng * lng + lat * lat) + 0.00002 * Math.sin(lat * X_PI);
  const theta = Math.atan2(lat, lng) + 0.000003 * Math.cos(lng * X_PI);
  return {
    lng: z * Math.cos(theta) + 0.0065,
    lat: z * Math.sin(theta) + 0.006
  };
};

const escapeHtml = (s) => {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
};

const escapeAttr = (s) => {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
};

const jsQuote = (s) => JSON.stringify(s);

const showLoading = () => { document.getElementById('message-list').innerHTML = '<div class="loading"><div class="loading-icon">&#x23f3;</div>加载消息中...</div>'; };

const showError = (msg) => { document.getElementById('message-list').innerHTML = `<div class="error-msg">&#x26a0; ${escapeHtml(msg)}</div>`; };

const openLightbox = (url) => { document.getElementById('lightbox-img').src = url; document.getElementById('lightbox').style.display = 'flex'; };

const showChatUI = (show) => {
  const d = show ? '' : 'none';
  document.getElementById('welcome').style.display = show ? 'none' : '';
  ['chat-header','filter-bar','message-list','pagination-bar'].forEach(id => document.getElementById(id).style.display = d);
};

// Toast notification system
function showToast(message, type = 'info', duration = 3000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:10000;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  const colors = { info: '#58a6ff', success: '#3fb950', warning: '#d29922', error: '#f85149' };
  const icons = { info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌' };
  toast.style.cssText = `background:#161b22;color:#c9d1d9;border:1px solid ${colors[type]};border-left:4px solid ${colors[type]};padding:12px 16px;border-radius:6px;font-size:13px;max-width:360px;pointer-events:auto;opacity:0;transform:translateX(20px);transition:opacity 0.3s,transform 0.3s;box-shadow:0 4px 12px rgba(0,0,0,0.4);display:flex;align-items:center;gap:8px;`;
  toast.innerHTML = `<span>${icons[type]}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  requestAnimationFrame(() => { toast.style.opacity = '1'; toast.style.transform = 'translateX(0)'; });
  setTimeout(() => {
    toast.style.opacity = '0'; toast.style.transform = 'translateX(20px)';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// Skeleton loading placeholder
function showSkeleton(el, rows = 5) {
  let html = '<style>@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}</style>';
  for (let i = 0; i < rows; i++) {
    const w = 40 + Math.random() * 50;
    html += `<div style="height:16px;background:linear-gradient(90deg,#21262d 25%,#30363d 50%,#21262d 75%);background-size:200% 100%;border-radius:4px;margin-bottom:12px;width:${w}%;animation:shimmer 1.5s infinite;"></div>`;
  }
  el.innerHTML = html;
}

// Error recovery suggestions
const _errorHints = {
  'LLM': '请检查 AI 分析页面的 LLM 配置（API Key、Base URL、Model）',
  'timeout': '请求超时，请检查网络连接或增加超时时间',
  'connection': '连接失败，请检查服务是否正在运行',
  '401': '认证失败，请检查 API Key 是否正确',
  '403': '访问被拒绝，请检查权限设置',
  '429': '请求过于频繁，请稍后再试',
  '500': '服务器内部错误，请查看日志获取详情',
  '密钥': '请先运行"提取密钥"功能获取数据库密钥',
  '解密': '请确认微信已关闭后再试，或重新提取密钥',
  '备份': '请确认微信数据目录路径正确，且微信已至少登录过一次',
  '扫描': '请确认已配置 LLM 且选择的群聊有消息记录',
};
function errorWithHint(msg) {
  const lower = (msg || '').toLowerCase();
  for (const [key, hint] of Object.entries(_errorHints)) {
    if (lower.includes(key.toLowerCase())) {
      return msg + '\n💡 ' + hint;
    }
  }
  return msg;
}

// Unified modal management
function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.style.display = 'flex';
  modal.setAttribute('aria-hidden', 'false');
  const focusTarget = modal.querySelector('input,button,select,textarea,[tabindex]');
  if (focusTarget) setTimeout(() => focusTarget.focus(), 50);
  modal._prevFocus = document.activeElement;
}
function closeModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.style.display = 'none';
  modal.setAttribute('aria-hidden', 'true');
  if (modal._prevFocus) { modal._prevFocus.focus(); modal._prevFocus = null; }
}
// ESC to close topmost modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modals = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
    for (let i = modals.length - 1; i >= 0; i--) {
      if (modals[i].style.display !== 'none') {
        closeModal(modals[i].id);
        e.preventDefault();
        break;
      }
    }
  }
});
