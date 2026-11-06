/**
 * 股探 StockExplorer v3 — 前端主逻辑
 * 深浅色主题 · 大盘行情 · 自选股 · 热门人气股 · 分时/K线/指标 · 成分股 · 热点掘金
 */

const $ = (s,p) => (p||document).querySelector(s);
const $$ = (s,p) => (p||document).querySelectorAll(s);

function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function safeURL(value) {
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? escapeHTML(url.href) : '#'; }
    catch { return '#'; }
}
async function requestAPI(url, options = {}) {
    const response = await window.fetch(url, { ...options, signal: options.signal || AbortSignal.timeout(url.includes('/match') ? 60000 : url.includes('/commentary') ? 45000 : url.includes('/constituents') ? 30000 : 20000) });
    if (!response.ok) throw new Error(`请求失败 (${response.status})`);
    return response;
}
function dataState(message, action) {
    return `<div class="data-state">${escapeHTML(message)}${action ? `<button type="button" data-retry="${action}">重新连接</button>` : ''}</div>`;
}
function makeKeyboardClickable(container, selector) {
    container.querySelectorAll(selector).forEach(item => {
        item.tabIndex = 0;
        item.setAttribute('role', 'button');
        item.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') {e.preventDefault(); item.click();} });
    });
}
function beijingTime() {
    return new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
}
function displayTime(value, full = false) {
    if (!value) return '时间暂不可用';
    const date = new Date(value.includes('T') ? value : value.replace(' ', 'T') + '+08:00');
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', ...(full ? {month:'2-digit',day:'2-digit'} : {}),hour:'2-digit',minute:'2-digit',hour12:false});
}


/* ═══════════════════════════════════════════════════════
   Global State
   ═══════════════════════════════════════════════════════ */

let state = {
    currentStock: null,      // {code, market, name}
    currentPeriod: 'daily',
    activeIndicators: new Set(['vol']),  // 'macd','rsi','kdj','boll','vol'
    watchlist: [],
    selectedIndex: null,
    chartInstances: {},
    discoverLoaded: false,
    hashReady: false,        // true after initial hash handled
};

/* ═══════════════════════════════════════════════════════
   Hash Routing — 保持刷新后页面状态
   #market              → 大盘
   #discover            → 掘金
   #discover/TOPIC      → 掘金+搜索
   #stock/CODE          → 个股(默认分时)
   #stock/CODE/PERIOD   → 个股+指定周期(daily/weekly/monthly)
   ═══════════════════════════════════════════════════════ */

function setHash(view, param1, param2) {
    let hash = '#' + view;
    if (param1) hash += '/' + encodeURIComponent(param1);
    if (param2) hash += '/' + encodeURIComponent(param2);
    if (window.location.hash !== hash) {
        history.replaceState(null, '', hash);
    }
}

function getHashInfo() {
    const raw = window.location.hash.replace(/^#/, '');
    if (!raw) return { view: 'market' };
    let parts;
    try { parts = raw.split('/').map(decodeURIComponent); } catch { return {view:'market'}; }
    return { view: parts[0], param1: parts[1] || null, param2: parts[2] || null };
}

async function handleHash() {
    const { view, param1, param2 } = getHashInfo();
    state.hashReady = true;

    switch (view) {
    case 'discover':
        switchToDiscover(param1 || null);
        break;
    case 'stock':
        if (param1 && /^\d{6}$/.test(param1)) {
            const mkt = stockMarket(param1);
            let name = param1;
            try {
                const r = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(param1)}`);
                const data = await r.json();
                if (data.length) name = data[0].name;
            } catch(e) {}
            // Restore period: param2 = daily/weekly/monthly, default = intraday
            const period = ['daily','weekly','monthly'].includes(param2) ? param2 : null;
            await selectStock(param1, mkt, name, period);
        } else {
            switchToMarket();
        }
        break;
    default:
        switchToMarket();
        break;
    }
}

function switchToMarket() {
    setHash('market');
    $$('.mtab').forEach(b => b.classList.remove('active'));
    const mktTab = $('.mtab[data-view="market"]');
    if (mktTab) mktTab.classList.add('active');
    $$('.view').forEach(v => v.classList.remove('active'));
    $('#view-market').classList.add('active');
}

window.addEventListener('hashchange', () => {
    if (!state.hashReady) return;
    const info = getHashInfo();
    // Skip if hash didn't actually change (prevents re-entrant loops)
    if (state._lastHash === JSON.stringify(info)) return;
    state._lastHash = JSON.stringify(info);
    handleHash();
});

/* ═══════════════════════════════════════════════════════
   Theme Management
   ═══════════════════════════════════════════════════════ */

function loadTheme() {
    const theme = localStorage.getItem('theme') || 'dark';
    document.documentElement.dataset.theme = theme;
    updateThemeButton(theme);
}

function saveTheme(theme) {
    localStorage.setItem('theme', theme);
}

function updateThemeButton(theme) {
    const btn = $('#btn-theme-toggle');
    if (btn) { btn.textContent = theme === 'light' ? '☼' : '◐'; btn.setAttribute('aria-label', theme === 'light' ? '切换深色主题' : '切换浅色主题'); }
}

function getChartTheme() {
    const light = document.documentElement.dataset.theme === 'light';
    return {
        bg: light ? '#ffffff' : '#10161f',
        text: light ? '#566c7a' : '#a2afbf',
        grid: light ? '#e1e7eb' : '#24303e',
        border: light ? '#bccbd2' : '#38485b',
    };
}

function toggleTheme() {
    const current = document.documentElement.dataset.theme || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    saveTheme(next);
    updateThemeButton(next);
    updateAllChartThemes();
}

function updateAllChartThemes() {
    const theme = getChartTheme();
    Object.values(state.chartInstances).forEach(c => {
        try {
            const option = c.getOption();
            const light = document.documentElement.dataset.theme === 'light';
            const replacements = light ? {'#10161f':theme.bg,'#a2afbf':theme.text,'#24303e':theme.grid,'#38485b':theme.border} : {'#ffffff':theme.bg,'#566c7a':theme.text,'#e1e7eb':theme.grid,'#bccbd2':theme.border};
            function recolor(value) {
                if (typeof value === 'string') return replacements[value] || value;
                if (Array.isArray(value)) return value.map(recolor);
                if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,recolor(v)]));
                return value;
            }
            c.setOption({...recolor(option), backgroundColor:theme.bg}, true);
            c.resize();
        } catch(e) {}
    });
}

/* ═══════════════════════════════════════════════════════
   Density (Spacing) Management
   ═══════════════════════════════════════════════════════ */

const DENSITY_LEVELS = ['compact', 'normal', 'relaxed'];
const DENSITY_LABELS = { compact: '≡', normal: '☷', relaxed: '☰' };

function loadDensity() {
    const density = localStorage.getItem('density') || 'normal';
    document.documentElement.dataset.density = density;
    updateDensityButton(density);
}

function saveDensity(density) {
    localStorage.setItem('density', density);
}

function updateDensityButton(density) {
    const btn = $('#btn-density-toggle');
    if (btn) btn.textContent = DENSITY_LABELS[density] || '📏';
    if (btn) btn.title = `间距: ${density} — 点击切换 (紧凑 → 正常 → 宽松)`;
}

function toggleDensity() {
    const current = document.documentElement.dataset.density || 'normal';
    const idx = DENSITY_LEVELS.indexOf(current);
    const next = DENSITY_LEVELS[(idx + 1) % DENSITY_LEVELS.length];
    document.documentElement.dataset.density = next;
    saveDensity(next);
    updateDensityButton(next);
    // Resize charts to fit new padding
    Object.values(state.chartInstances).forEach(c => {
        try { c.resize(); } catch(e) {}
    });
}

// Dynamic chart theme getters (replaces constants)
function chartBg() { return getChartTheme().bg; }
function chartText() { return getChartTheme().text; }
function chartGrid() { return getChartTheme().grid; }
function chartBorder() { return getChartTheme().border; }

/* ═══════════════════════════════════════════════════════
   Utilities
   ═══════════════════════════════════════════════════════ */

function formatVol(v) {
    if (v == null || !Number.isFinite(Number(v))) return '--';
    if (v === 0) return '0手';
    if (v >= 1e8) return (v/1e8).toFixed(2)+'亿手';
    if (v >= 1e4) return (v/1e4).toFixed(0)+'万手';
    return v+'手';
}
function formatAmt(a) {
    if (!a) return '--';
    if (a >= 1e8) return (a/1e8).toFixed(2)+'亿';
    if (a >= 1e4) return (a/1e4).toFixed(0)+'万';
    return a.toFixed(0);
}
function formatCap(c) {
    if (!c) return '--';
    if (c >= 1e12) return (c/1e12).toFixed(2)+'万亿';
    if (c >= 1e8) return (c/1e8).toFixed(2)+'亿';
    return (c/1e4).toFixed(0)+'万';
}
function colorCls(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; }
function colorSign(v) { return v > 0 ? '+' : ''; }
function stockMarket(code) { return /^[69]/.test(code) ? 1 : 0; }
function formatPercent(value) {
    return Number.isFinite(value) ? `${colorSign(value)}${value.toFixed(2)}%` : '—';
}

/* ═══════════════════════════════════════════════════════
   Index Cards & Bottom Strip
   ═══════════════════════════════════════════════════════ */

async function loadIndexQuotes() {
    try {
        const r = await requestAPI('/api/index/quotes');
        const indices = await r.json();
        renderBottomStrip(indices);
        renderIndexCards(indices);
        const quoteTime = indices.map(i => i.quote_time || '').sort().at(-1);
        $('#index-update').textContent = indices.length ? `行情 ${quoteTime || '时间暂不可用'} · 北京时间` : '行情源暂不可用';
        updateMarketTime();
    } catch(e) { $('#index-cards').innerHTML = dataState('行情连接失败，请稍后重试', 'indices'); $('#index-update').textContent = '连接中断'; }
}

function renderBottomStrip(indices) {
    const container = $('.bottom-index-strip-inner');
    if (!container) return;

    // Build items — full names with fixed-width price/change blocks
    const itemsHtml = indices.map(i => {
        const cls = i.change_pct >= 0 ? 'im-up' : 'im-down';
        const sign = colorSign(i.change_pct);
        return `<div class="bottom-index-mini ${cls}" data-code="${i.code}" title="${i.name}">
            <span class="im-name">${i.name}</span>
            <span class="im-sep">·</span>
            <span class="im-price">${i.price.toFixed(2)}</span>
            <span class="im-change">${sign}${i.change_pct.toFixed(2)}%</span>
        </div>`;
    }).join('');

    // Duplicate for seamless infinite scroll animation
    container.innerHTML = itemsHtml + itemsHtml;

    // Click handlers — attach to all items (original + duplicate)
    container.querySelectorAll('.bottom-index-mini').forEach(mini => {
        mini.addEventListener('click', () => {
            const code = mini.dataset.code;
            const card = $(`.idx-card[data-code="${code}"]`);
            if (card) {
                card.click();
                card.scrollIntoView({behavior:'smooth',block:'center'});
            }
        });
    });
}

function renderIndexCards(indices) {
    const container = $('#index-cards');
    if (!Array.isArray(indices) || !indices.length) {
        container.innerHTML = dataState('暂未取得有效指数行情', 'indices'); return;
    }
    container.innerHTML = indices.map(i => {
        const cls = i.change_pct > 0 ? 'idx-up' : i.change_pct < 0 ? 'idx-down' : '';
        return `<button class="idx-card ${cls} ${state.selectedIndex?.code === i.code ? 'selected' : ''}" data-code="${escapeHTML(i.code)}" data-name="${escapeHTML(i.name)}" aria-label="查看${escapeHTML(i.name)}成分股">
            <div class="ic-header"><span class="ic-name">${escapeHTML(i.name)}</span><span class="ic-code">${escapeHTML(i.code)}</span></div>
            <div class="ic-price">${i.price.toFixed(2)}</div>
            <div class="ic-change"><span class="change-badge">${colorSign(i.change_pct)}${i.change_pct.toFixed(2)}%</span><span>${colorSign(i.change_amt)}${i.change_amt.toFixed(2)}</span></div>
            <div class="ic-detail"><span>最高<b>${i.high > 0 ? i.high.toFixed(2) : '--'}</b></span><span>最低<b>${i.low > 0 ? i.low.toFixed(2) : '--'}</b></span><span>成交额<b>${formatAmt(i.amount)}</b></span></div>
        </button>`;
    }).join('');
    container.querySelectorAll('.idx-card').forEach(card => {
        card.addEventListener('click', () => {
            const {code, name} = card.dataset;
            container.querySelectorAll('.idx-card').forEach(c => c.classList.toggle('selected', c === card));
            state.selectedIndex = {code, name};
            loadConstituents(code, name);
        });
    });
}

let constituentsRequest = 0;
let constituentRows = [];
let constituentSort;

function renderConstituentRows() {
    const body = $('#constituents-body');
    const rows = [...constituentRows];
    if (constituentSort) {
        const {field, direction} = constituentSort;
        rows.sort((a, b) => a[field] == null ? (b[field] == null ? 0 : 1) :
            b[field] == null ? -1 : direction * (a[field] - b[field]));
    }
    body.innerHTML = rows.map(s => `<tr data-code="${escapeHTML(s.code)}" data-name="${escapeHTML(s.name)}">
        <td>${escapeHTML(s.code)}</td><td>${escapeHTML(s.name)}</td>
        <td>${s.price > 0 ? s.price.toFixed(2) : '—'}</td>
        <td class="${s.change_pct > 0 ? 'st-change-up' : s.change_pct < 0 ? 'st-change-down' : ''}">${formatPercent(s.change_pct)}</td>
        <td>${formatVol(s.volume)}</td><td>${formatCap(s.market_cap)}</td>
    </tr>`).join('');
    body.querySelectorAll('tr').forEach(row => row.addEventListener('click', () =>
        selectStock(row.dataset.code, stockMarket(row.dataset.code), row.dataset.name)));
    makeKeyboardClickable(body, 'tr');
}

async function loadConstituents(indexCode, name) {
    const id = ++constituentsRequest;
    const panel = $('#constituents-panel'), body = $('#constituents-body'), meta = $('#constituents-meta');
    $('#constituents-title').textContent = `${name} 成分股`;
    constituentRows = [];
    constituentSort = null;
    $$('#constituents-panel th[aria-sort]').forEach(th => th.removeAttribute('aria-sort'));
    meta.textContent = '';
    body.innerHTML = '<tr><td colspan="6" class="data-state">正在加载样本名单…</td></tr>';
    panel.classList.remove('hidden');
    try {
        const response = await requestAPI(`/api/index/${encodeURIComponent(indexCode)}/constituents?top=50`);
        const data = await response.json();
        if (id !== constituentsRequest) return;
        constituentRows = data.stocks;
        renderConstituentRows();
        meta.innerHTML = `显示 ${data.stocks.length} 只 · ${data.incomplete ? '来源只提供部分样本' : `来源共 ${data.total} 只`} ·
            <a href="${safeURL(data.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(data.source)}</a>
            ${data.sample_date ? ' · 样本日期 ' + escapeHTML(data.sample_date) : ''}
            · 行情 ${escapeHTML(data.quote_source)}
            ${data.quotes_available < data.stocks.length ? ' · 部分行情暂不可用' : ''}`;
    } catch {
        if (id !== constituentsRequest) return;
        body.innerHTML = `<tr><td colspan="6">${dataState('指数样本暂不可用', 'constituents')}</td></tr>`;
    }
}

function updateMarketTime() {
    const now = beijingTime();
    const day = now.getDay();
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;

    const isWeekday = day >= 1 && day <= 5;
    const morningOpen = 570, morningClose = 690;  // 9:30-11:30
    const afternoonOpen = 780, afternoonClose = 900; // 13:00-15:00

    let status;
    if (!isWeekday) status = '周末休市';
    else if (t < morningOpen) status = '未开盘';
    else if (t >= morningOpen && t < morningClose) status = '交易时段';
    else if (t >= morningClose && t < afternoonOpen) status = '午间休市';
    else if (t >= afternoonOpen && t < afternoonClose) status = '交易时段';
    else status = '已收盘';

    const el = $('#market-time');
    if (el) el.textContent = `${status} · 北京时间`;
    $('#market-date').textContent = new Date().toLocaleDateString('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',weekday:'short'});
}

/* ═══════════════════════════════════════════════════════
   Popular Stocks (Sidebar)
   ═══════════════════════════════════════════════════════ */

async function loadPopularStocks() {
    const container = $('#popular-stocks');
    const countEl = $('#popular-stocks-count');
    if (!container) return;

    try {
        const r = await requestAPI('/api/stocks/popular?top=30');
        const stocks = await r.json();

        if (!stocks.length) {
            container.innerHTML = dataState('活跃股行情暂不可用', 'popular');
            if (countEl) countEl.textContent = '0';
            return;
        }

        container.innerHTML = stocks.map((s, index) => {
            const chgCls = s.change_pct > 0 ? 'wl-up' : s.change_pct < 0 ? 'wl-down' : '';
            const activeCls = state.currentStock && state.currentStock.code === s.code ? 'active' : '';
            return `<div class="wl-item ${activeCls}" data-code="${s.code}" data-market="${stockMarket(s.code)}" data-name="${escapeHTML(s.name)}" title="${escapeHTML(s.name)}  换手:${(s.turnover||0).toFixed(1)}% | 成交额:${formatAmt(s.amount)}">
                <span class="wl-rank">${String(index + 1).padStart(2, '0')}</span>
                <span class="wl-code">${s.code}</span>
                <span class="wl-name">${escapeHTML(s.name)}</span>
                <span class="wl-price ${chgCls}">${s.price>0?s.price.toFixed(2):'--'}</span>
                <span class="wl-change ${chgCls}">${formatPercent(s.change_pct)}</span>
            </div>`;
        }).join('');

        if (countEl) countEl.textContent = stocks.length;

        makeKeyboardClickable(container, '.wl-item');
        // Click handlers
        container.querySelectorAll('.wl-item').forEach(item => {
            item.addEventListener('click', () => {
                const code = item.dataset.code;
                const name = item.dataset.name;
                const mkt = parseInt(item.dataset.market);
                selectStock(code, mkt, name);
            });
        });
    } catch(e) {
        container.innerHTML = dataState('活跃股加载失败', 'popular');
        console.error('Popular stocks error:', e);
    }
}

/* ═══════════════════════════════════════════════════════
   Watchlist (Horizontal Strip + localStorage)
   ═══════════════════════════════════════════════════════ */

function loadWatchlist() {
    try {
        const saved = JSON.parse(localStorage.getItem('watchlist') || '[]');
        state.watchlist = Array.isArray(saved) ? saved.filter(w => w && /^\d{6}$/.test(w.code)).slice(0,30) : [];
    } catch(e) { state.watchlist = []; }
}

function saveWatchlist() {
    localStorage.setItem('watchlist', JSON.stringify(state.watchlist));
}

function isInWatchlist(code) {
    return state.watchlist.some(w => w.code === code);
}

function addToWatchlist(code, name) {
    if (!/^\d{6}$/.test(code) || isInWatchlist(code) || state.watchlist.length >= 30) return;
    state.watchlist.push({code, name, addedAt: Date.now()});
    saveWatchlist();
    refreshWatchlistQuotes();
    updateStarButton();
}

function removeFromWatchlist(code) {
    state.watchlist = state.watchlist.filter(w => w.code !== code);
    saveWatchlist();
    refreshWatchlistQuotes();
    if (state.currentStock && state.currentStock.code === code) {
        updateStarButton();
    }
}

async function refreshWatchlistQuotes() {
    const container = $('#wl-strip-items');
    const countEl = $('#wl-strip-count');

    if (!state.watchlist.length) {
        if (container) container.innerHTML = '<span class="wl-strip-empty">添加第一只自选股，关注它的每一次变化</span>';
        if (countEl) countEl.textContent = '0';
        return;
    }

    const codes = state.watchlist.map(w => w.code).join(',');
    let stocks = [];
    try {
        const r = await requestAPI(`/api/stocks/batch?codes=${encodeURIComponent(codes)}`);
        stocks = await r.json();
    } catch(e) { console.error('Watchlist quotes error:', e); }

    const lookup = {};
    stocks.forEach(s => { lookup[s.code] = s; });

    if (!container) return;
    container.innerHTML = state.watchlist.map(w => {
        const s = lookup[w.code];
        if (!s) return `<div class="wl-strip-mini" data-code="${w.code}" data-market="${stockMarket(w.code)}" data-name="${escapeHTML(w.name)}">
            <span class="wls-code">${w.code}</span>
            <span class="wls-name">${escapeHTML(w.name)}</span>
            <span class="wls-remove" data-code="${w.code}">✕</span></div>`;

        const cls = s.change_pct >= 0 ? 'wl-up' : s.change_pct < 0 ? 'wl-down' : '';
        const activeCls = state.currentStock && state.currentStock.code === w.code ? 'active' : '';
        return `<div class="wl-strip-mini ${activeCls}" data-code="${w.code}" data-market="${stockMarket(w.code)}" data-name="${escapeHTML(s.name)}">
            <span class="wls-code">${w.code}</span>
            <span class="wls-name">${escapeHTML(s.name)}</span>
            <span class="wls-price ${cls}">${s.price>0?s.price.toFixed(2):'--'}</span>
            <span class="wls-change ${cls}">${formatPercent(s.change_pct)}</span>
            <span class="wls-remove" data-code="${w.code}">✕</span></div>`;
    }).join('');

    if (countEl) countEl.textContent = state.watchlist.length;

    // Click handlers
    container.querySelectorAll('.wl-strip-mini').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('wls-remove')) return;
            const code = item.dataset.code;
            const name = item.dataset.name;
            const mkt = parseInt(item.dataset.market);
            selectStock(code, mkt, name);
        });
    });
    container.querySelectorAll('.wls-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeFromWatchlist(btn.dataset.code);
        });
    });
}

function updateStarButton() {
    const btn = $('#btn-watchlist-star');
    if (!btn) return;
    if (!state.currentStock) { btn.style.display = 'none'; return; }
    btn.style.display = '';
    if (isInWatchlist(state.currentStock.code)) {
        btn.textContent = '★'; btn.classList.add('starred');
    } else {
        btn.textContent = '☆'; btn.classList.remove('starred');
    }
}

function updateWatchlistHighlight() {
    $$('.wl-strip-mini').forEach(item => {
        item.classList.toggle('active', state.currentStock && item.dataset.code === state.currentStock.code);
    });
    // Also update sidebar popular stocks highlight
    $$('#popular-stocks .wl-item').forEach(item => {
        item.classList.toggle('active', state.currentStock && item.dataset.code === state.currentStock.code);
    });
}

/* ═══════════════════════════════════════════════════════
   Stock Detail
   ═══════════════════════════════════════════════════════ */

async function selectStock(code, market, name, restorePeriod) {
    // restorePeriod: null/'intraday' = 分时, 'daily'/'weekly'/'monthly' = K线
    if (!/^\d{6}$/.test(code)) return;
    const period = restorePeriod || 'intraday';
    setHash('stock', code, period === 'intraday' ? null : period);
    state.currentStock = {code, market, name};
    state.currentPeriod = period;
    state.activeIndicators = new Set(period === 'intraday' ? [] : ['vol']);

    // Switch to stock detail view
    $$('.mtab').forEach(b => b.classList.remove('active'));
    const detailTab = $('#tab-stock-detail');
    if (detailTab) {
        detailTab.classList.add('active');
        detailTab.disabled = false;
    }
    $$('.view').forEach(v => v.classList.remove('active'));
    $('#view-stock-detail').classList.add('active');

    // Reset UI
    const header = $('#stock-detail-header');
    const tabs = $('#chart-period-tabs');
    const wrap = $('#chart-wrap');
    if (header) header.classList.add('hidden');
    if (tabs) tabs.classList.add('hidden');
    if (wrap) wrap.classList.add('hidden');
    $('#stock-empty').classList.add('hidden');
    $('#stock-loading').classList.remove('hidden');

    // Set active period tab
    $$('.cptab').forEach(b => b.classList.remove('active'));
    const activeTab = $(`.cptab[data-period="${period}"]`);
    if (activeTab) activeTab.classList.add('active');
    // Reset indicator toggles
    $$('.ind-toggle').forEach(b => b.classList.toggle('on', period !== 'intraday' && b.dataset.ind === 'vol'));
    // Show/hide indicator panels
    const indDom = $('#chart-indicator');
    const ind2Dom = $('#chart-indicator2');
    if (indDom) indDom.classList.remove('show');
    if (ind2Dom) ind2Dom.classList.remove('show');

    updateStarButton();
    updateWatchlistHighlight();

    // The chart must be visible before ECharts measures its size.
    if (header) header.classList.remove('hidden');
    if (tabs) tabs.classList.remove('hidden');
    if (wrap) wrap.classList.remove('hidden');
    $('#sdh-name').textContent = name || code;
    $('#sdh-code').textContent = `${market === 1 ? 'SH' : 'SZ'}${code}`;
    ['price','change','open','high','low','preclose','volume','amount','turnover','amplitude','market-cap','pe'].forEach(k => { $(`#sdh-${k}`).textContent = '--'; });
    $('#stock-quote-status').textContent = '正在获取行情…';
    loadCommentary(code);
    // Load chart based on period
    if (period === 'intraday') {
        await loadIntradayChart(code, market, name);
    } else {
        await loadKlineChart(code, market, period);
    }
    if (state.currentStock?.code !== code) return;
    await loadStockInfo(code, market);
    if (state.currentStock?.code !== code) return;
    requestAnimationFrame(() => Object.values(state.chartInstances).forEach(c => { if (!c.isDisposed()) c.resize(); }));

    if (header) header.classList.remove('hidden');
    if (tabs) tabs.classList.remove('hidden');
    if (wrap) wrap.classList.remove('hidden');
    $('#stock-loading').classList.add('hidden');
}

async function loadStockInfo(code, market) {
    try {
        const r = await requestAPI(`/api/stock/${code}/info`);
        const info = await r.json();
        if (info.error || state.currentStock?.code !== code) return;

        $('#sdh-name').textContent = info.name || '--';
        $('#sdh-code').textContent = `${market===1?'SH':'SZ'}${code}`;
        const price = info.price || 0;
        const chgPct = info.change_pct;

        $('#sdh-price').textContent = price > 0 ? price.toFixed(2) : '--';
        $('#sdh-price').className = 'sdh-price ' + (chgPct > 0 ? 'sdh-up' : chgPct < 0 ? 'sdh-down' : '');
        $('#sdh-change').textContent = `${info.change_amt == null ? '—' : colorSign(info.change_amt) + info.change_amt.toFixed(2)}  ${formatPercent(chgPct)}`;
        $('#sdh-change').className = 'sdh-change ' + (chgPct > 0 ? 'sdh-up' : chgPct < 0 ? 'sdh-down' : '');

        $('#sdh-open').textContent = info.open ? info.open.toFixed(2) : '--';
        $('#sdh-high').textContent = info.high ? info.high.toFixed(2) : '--';
        $('#sdh-low').textContent = info.low ? info.low.toFixed(2) : '--';
        $('#sdh-preclose').textContent = info.preclose ? info.preclose.toFixed(2) : '--';
        $('#sdh-volume').textContent = formatVol(info.volume);
        $('#sdh-amount').textContent = formatAmt(info.amount);
        $('#sdh-turnover').textContent = Number.isFinite(info.turnover_rate) ? info.turnover_rate.toFixed(2)+'%' : '—';
        $('#sdh-amplitude').textContent = info.amplitude ? info.amplitude.toFixed(2)+'%' : '--';
        $('#sdh-market-cap').textContent = formatCap(info.market_cap);
        $('#sdh-pe').textContent = Number.isFinite(info.pe) ? info.pe.toFixed(2) : '—';
        $('#stock-quote-status').textContent = [
            info.quote_time ? `行情 ${info.quote_time} 北京时间` : '行情时间暂不可用',
            info.quote_source, info.metrics_source ? `估值与换手率：${info.metrics_source}` : '估值与换手率暂不可用',
        ].filter(Boolean).join(' · ');
    } catch {
        if (state.currentStock?.code === code) $('#stock-quote-status').innerHTML = dataState('个股行情暂不可用', 'stock-info');
    }
}

/* ═══════════════════════════════════════════════════════
   Charts (ECharts)
   ═══════════════════════════════════════════════════════ */

const UP_COLOR = '#f85149', DOWN_COLOR = '#3fb950';

function initChart(domId) {
    const dom = $(`#${domId}`);
    if (!dom) return null;
    if (typeof echarts === 'undefined') { dom.innerHTML = dataState('图表组件加载失败，请检查网络后刷新页面'); return null; }
    let instance = state.chartInstances[domId];
    if (!instance || instance.isDisposed()) {
        instance = echarts.init(dom, null, {backgroundColor: chartBg()});
        state.chartInstances[domId] = instance;
    }
    return instance;
}

const indicatorPanels = {macd:'chart-indicator', kdj:'chart-indicator2', rsi:'chart-rsi'};

function disposeAllCharts() {
    const dom = $('#chart-main');
    if (dom._wheelHandler) {
        dom.removeEventListener('wheel', dom._wheelHandler);
        delete dom._wheelHandler;
    }
    Object.values(state.chartInstances).forEach(c => { if (!c.isDisposed()) c.dispose(); });
    state.chartInstances = {};
    Object.values(indicatorPanels).forEach(id => document.getElementById(id).classList.remove('show'));
}

async function loadIntradayChart(code, market, name) {
    disposeAllCharts();
    $('#chart-status').textContent = '正在加载分时…';
    $$('.ind-toggle').forEach(button => { button.disabled = true; });
    const chart = initChart('chart-main');
    if (!chart) return;
    chart.showLoading();

    try {
        const r = await requestAPI(`/api/stock/${code}/intraday`);
        const data = await r.json();
        if (chart.isDisposed() || state.currentStock?.code !== code || state.currentPeriod !== 'intraday') return;
        chart.hideLoading();

        if (!data.minutes || !data.minutes.length) {
            chart.setOption({
                title: {text: '暂无分时数据', subtext: '当日非交易时段或无数据源', left: 'center', top: 'center',
                    textStyle: {color: chartText(), fontSize: 14},
                    subtextStyle: {color: '#8b949e', fontSize: 11}},
            }, true);
            return;
        }

        $('#chart-status').textContent = `${data.source || '分时'} · ${data.trade_date || '日期未知'} · 成交量单位：手`;
        const times = data.minutes.map(d => d.time?.split(' ').at(-1));
        const prices = data.minutes.map(d => d.price);
        const avgs = data.minutes.map(d => d.avg_price);
        const volumes = data.minutes.map(d => d.volume);
        const preclose = data.preclose > 0 ? data.preclose : null;
        const tradeDate = data.trade_date || '';
        const isToday = data.is_today !== false;

        const allPrices = [...prices, ...(preclose ? [preclose] : []), ...avgs.filter(v=>v>0)];
        const pMin = Math.min(...allPrices), pMax = Math.max(...allPrices);
        const padding = (pMax - pMin) * 0.1 || pMax * 0.02;

        // Title with date indicator for non-today data
        const chartTitle = isToday ? {} : {
            title: {
                text: `分时图 (${tradeDate})`,
                subtext: '⚠ 非当日数据，为最近交易日分时',
                left: 'center', top: 2,
                textStyle: { color: chartText(), fontSize: 12 },
                subtextStyle: { color: '#d2991d', fontSize: 10 },
            },
        };

        chart.setOption({
            backgroundColor: chartBg(),
            ...chartTitle,
            tooltip: {trigger:'axis', axisPointer:{type:'cross'}},
            grid: [{left:'8%',right:'4%',top:'5%',height:'55%'},{left:'8%',right:'4%',top:'68%',height:'20%'}],
            xAxis: [{type:'category',data:times,gridIndex:0,axisLabel:{color:chartText(),fontSize:10},axisLine:{lineStyle:{color:chartBorder()}}},
                    {type:'category',data:times,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:chartBorder()}}}],
            yAxis: [{type:'value',gridIndex:0,min:pMin-padding,max:pMax+padding,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:10}},
                    {type:'value',gridIndex:1,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:10}}],
            series: [
                {name:'价格',type:'line',xAxisIndex:0,yAxisIndex:0,data:prices,smooth:false,symbol:'none',
                    lineStyle:{width:1.5,color:chartText()},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,
                    colorStops:[{offset:0,color:'rgba(255,255,255,0.08)'},{offset:1,color:'rgba(255,255,255,0.01)'}]}}},
                {name:'均价',type:'line',xAxisIndex:0,yAxisIndex:0,data:avgs,smooth:false,symbol:'none',
                    lineStyle:{width:1,color:DOWN_COLOR,type:'dashed'}},
                ...(preclose ? [{name:'昨收',type:'line',xAxisIndex:0,yAxisIndex:0,data:Array(times.length).fill(preclose),
                    smooth:false,symbol:'none',lineStyle:{width:0.5,color:chartText(),type:'dotted'}}] : []),
                {name:'成交量（手）',type:'bar',xAxisIndex:1,yAxisIndex:1,data:volumes.map((v,i)=>({
                    value:v, itemStyle:{color:volumeColor(prices[i], i ? prices[i-1] : (preclose ?? prices[0]))}}))},
            ],
        }, true);
    } catch(e) {
        if (chart.isDisposed()) return;
        $('#chart-status').innerHTML = dataState('分时行情暂不可用', 'chart');
        chart.hideLoading();
        chart.setOption({
            title: {text: '分时数据加载失败', subtext: '请稍后重试', left: 'center', top: 'center',
                textStyle: {color: '#f85149', fontSize: 14},
                subtextStyle: {color: '#8b949e', fontSize: 11}},
        }, true);
        console.error('Intraday error:', e);
    }
}

function volumeColor(price, reference) {
    return price > reference ? 'rgba(248,81,73,0.5)' : price < reference ? 'rgba(63,185,80,0.5)' : 'rgba(140,150,160,0.45)';
}

async function loadKlineChart(code, market, period) {
    disposeAllCharts();
    $('#chart-status').textContent = '正在加载K线…';
    $$('.ind-toggle').forEach(button => { button.disabled = false; });

    const mainChart = initChart('chart-main');
    if (!mainChart) return;
    mainChart.showLoading();

    try {
        const r = await requestAPI(`/api/stock/${code}/chart?period=${period}`);
        const result = await r.json();
        if (mainChart.isDisposed() || state.currentStock?.code !== code || state.currentPeriod !== period) return;
        mainChart.hideLoading();
        if (!result.data || !result.data.length) {
            mainChart.setOption({title:{text:'暂无数据',left:'center',top:'center',textStyle:{color:chartText()}}}, true);
            return;
        }

        $('#chart-status').textContent = '新浪财经 · 不复权 · 成交量单位：手';
        Object.entries(indicatorPanels).forEach(([key, id]) => {
            document.getElementById(id).classList.toggle('show', state.activeIndicators.has(key) && Boolean(result.indicators?.[key]));
        });
        const data = result.data;
        const mas = result.mas || {};
        const inds = result.indicators || {};
        const dates = data.map(d => d.date);
        const ohlc = data.map(d => [d.open, d.close, d.low, d.high]);
        const volumes = data.map(d => d.volume);

        const mainSeries = [
            {name:'K线',type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:ohlc,
                itemStyle:{color:UP_COLOR,color0:DOWN_COLOR,borderColor:UP_COLOR,borderColor0:DOWN_COLOR}},
        ];

        const maColors = {ma5:'#f0c060',ma10:'#58a6ff',ma20:'#a371f7',ma60:'#8b949e',ma120:'#d2991d',ma250:'#f85149'};
        Object.entries(mas).forEach(([key, vals]) => {
            if (key.startsWith('ma')) {
                mainSeries.push({name:key.toUpperCase(),type:'line',xAxisIndex:0,yAxisIndex:0,
                    data:vals,smooth:true,symbol:'none',lineStyle:{width:1,color:maColors[key]||chartText()}});
            }
        });

        if (state.activeIndicators.has('boll') && inds.boll) {
            const b = inds.boll;
            ['up','dn'].forEach(band => {
                mainSeries.push({name:'BOLL '+band.toUpperCase(),type:'line',xAxisIndex:0,yAxisIndex:0,
                    data:b[band],smooth:true,symbol:'none',lineStyle:{width:1,color:'#f85149',type:'dashed'}});
            });
            if (b.mb) mainSeries.push({name:'BOLL MB',type:'line',xAxisIndex:0,yAxisIndex:0,
                data:b.mb,smooth:true,symbol:'none',lineStyle:{width:1,color:'#d2991d'}});
        }

        const volSeries = {
            name:'成交量（手）',type:'bar',xAxisIndex:1,yAxisIndex:1,
            data: volumes.map((v, i) => ({value: v,
                itemStyle: {color: volumeColor(ohlc[i][1], ohlc[i][0])}})),
        };

        mainChart.setOption({
            backgroundColor: chartBg(),
            tooltip: {trigger:'axis',axisPointer:{type:'cross'},backgroundColor:chartBg(),
                borderColor:chartBorder(),textStyle:{color:chartText(),fontSize:11}},
            legend: {top:0,left:'center',textStyle:{color:chartText(),fontSize:10},data:['K线','MA5','MA10','MA20','MA60']},
            dataZoom: [
                {type:'inside', id:'dzInside', xAxisIndex:[0,1],
                    start:Math.max(0,100-Math.min(80,100*80/Math.max(data.length,1))), end:100,
                    minSpan:3, maxSpan:100,
                    zoomOnMouseWheel:false,
                    moveOnMouseWheel:false,
                },
                {type:'slider', id:'dzSlider', xAxisIndex:[0,1],
                    start:Math.max(0,100-Math.min(80,100*80/Math.max(data.length,1))), end:100,
                    minSpan:3, maxSpan:100,
                    bottom:2, height:16,
                    borderColor:chartBorder(), backgroundColor:chartBg(), textStyle:{color:chartText()}},
            ],
            grid: [{left:'8%',right:'3%',top:'6%',height:'50%'},{left:'8%',right:'3%',top:'62%',height:'14%'}],
            xAxis: [{type:'category',data:dates,gridIndex:0,axisLabel:{color:chartText(),fontSize:10},axisLine:{lineStyle:{color:chartBorder()}}},
                    {type:'category',data:dates,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:chartBorder()}}}],
            yAxis: [{type:'value',gridIndex:0,scale:true,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:10}},
                    {type:'value',gridIndex:1,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:10}}],
            series: state.activeIndicators.has('vol') ? [...mainSeries, volSeries] : mainSeries,
        }, true);

        // ── Manual wheel handler (more precise speed control) ──
        const dom = mainChart.getDom();
        if (dom._wheelHandler) dom.removeEventListener('wheel', dom._wheelHandler);
        dom._wheelHandler = (e) => {
            if (mainChart.isDisposed()) return;
            const dzOpt = mainChart.getOption()?.dataZoom?.[0];
            if (!dzOpt) return;
            const span = dzOpt.end - dzOpt.start;
            const center = (dzOpt.start + dzOpt.end) / 2;

            if (e.ctrlKey || e.metaKey) {
                // Zoom — slow 8% per tick
                e.preventDefault();
                const factor = e.deltaY > 0 ? 1.08 : 0.926;
                const newSpan = Math.max(3, Math.min(100, span * factor));
                const ns = Math.max(0, Math.min(100 - newSpan, center - newSpan / 2));
                mainChart.dispatchAction({ type: 'dataZoom', start: ns, end: ns + newSpan });
            } else if (Math.abs(e.deltaX) > 0) {
                // Horizontal pan — move 2% of visible span per scroll tick
                e.preventDefault();
                const shift = (e.deltaX > 0 ? 1 : -1) * Math.max(0.5, span * 0.02);
                const ns = Math.max(0, Math.min(100 - span, dzOpt.start + shift));
                mainChart.dispatchAction({ type: 'dataZoom', start: ns, end: ns + span });
            }
        };
        dom.addEventListener('wheel', dom._wheelHandler, { passive: false });

        // ── MACD ──
        if (state.activeIndicators.has('macd') && inds.macd) {
            const c = initChart('chart-indicator');
            if (c) {
                const m = inds.macd;
                c.setOption({
                    backgroundColor: chartBg(),
                    tooltip:{trigger:'axis'}, grid:{left:'8%',right:'3%',top:'8%',height:'70%'},
                    xAxis:{type:'category',data:dates,axisLabel:{color:chartText(),fontSize:9},axisLine:{lineStyle:{color:chartBorder()}}},
                    yAxis:{type:'value',splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:9}},
                    series:[
                        {name:'DIF',type:'line',data:m.dif,smooth:false,symbol:'none',lineStyle:{width:1,color:chartText()}},
                        {name:'DEA',type:'line',data:m.dea,smooth:false,symbol:'none',lineStyle:{width:1,color:DOWN_COLOR}},
                        {name:'MACD',type:'bar',data:m.macd.map((v,i)=>({value:v,
                            itemStyle:{color:v>=0?'rgba(248,81,73,0.6)':'rgba(63,185,80,0.6)'}}))},
                    ],
                }, true);
            }
        }

        // ── RSI ──
        if (state.activeIndicators.has('rsi') && inds.rsi) {
            const c = initChart('chart-rsi');
            if (c) {
                c.setOption({
                    backgroundColor: chartBg(),
                    tooltip:{trigger:'axis'}, grid:{left:'8%',right:'3%',top:'8%',height:'70%'},
                    xAxis:{type:'category',data:dates,axisLabel:{color:chartText(),fontSize:9},axisLine:{lineStyle:{color:chartBorder()}}},
                    yAxis:{type:'value',min:0,max:100,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:9}},
                    series:[
                        {name:'RSI',type:'line',data:inds.rsi,smooth:false,symbol:'none',lineStyle:{width:1.5,color:'#a371f7'},
                            markLine:{silent:true,data:[{yAxis:70,lineStyle:{color:DOWN_COLOR,type:'dashed'}},
                                {yAxis:30,lineStyle:{color:UP_COLOR,type:'dashed'}}]}},
                    ],
                }, true);
            }
        }

        // ── KDJ ──
        if (state.activeIndicators.has('kdj') && inds.kdj) {
            const c = initChart('chart-indicator2');
            if (c) {
                const kdj = inds.kdj;
                c.setOption({
                    backgroundColor: chartBg(),
                    tooltip:{trigger:'axis'}, grid:{left:'8%',right:'3%',top:'8%',height:'72%'},
                    xAxis:{type:'category',data:dates,axisLabel:{color:chartText(),fontSize:9},axisLine:{lineStyle:{color:chartBorder()}}},
                    yAxis:{type:'value',splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:9}},
                    series:[
                        {name:'K',type:'line',data:kdj.k,smooth:false,symbol:'none',lineStyle:{width:1,color:UP_COLOR}},
                        {name:'D',type:'line',data:kdj.d,smooth:false,symbol:'none',lineStyle:{width:1,color:DOWN_COLOR}},
                        {name:'J',type:'line',data:kdj.j,smooth:false,symbol:'none',lineStyle:{width:1,color:'#a371f7'}},
                    ],
                }, true);
            }
        }

        // Clear unused indicator areas
        if (!state.activeIndicators.has('macd') && !state.activeIndicators.has('rsi')) {
            const c = state.chartInstances['chart-indicator'];
            if (c && !c.isDisposed()) c.clear();
        }
        if (!state.activeIndicators.has('kdj')) {
            const c = state.chartInstances['chart-indicator2'];
            if (c && !c.isDisposed()) c.clear();
        }

        mainChart.resize();
    } catch(e) {
        if (mainChart.isDisposed()) return;
        $('#chart-status').innerHTML = dataState('K线行情暂不可用', 'chart');
        mainChart.hideLoading();
        mainChart.setOption({title:{text:'K线数据加载失败',subtext:'请稍后重试',left:'center',top:'center',textStyle:{color:chartText(),fontSize:14}}},true);
        console.error('Kline error:', e);
    }
}

/* ═══════════════════════════════════════════════════════
   Hot News & Topics
   ═══════════════════════════════════════════════════════ */

let hotNewsPending = false;
let hotNewsTimer;
function renderTopics(container, topics) {
    if (!container) return;
    if (!topics.length) { container.innerHTML = dataState('暂未取得热门话题，稍后会自动重试'); return; }
    container.innerHTML = topics.map((t, index) => {
        const change = t.change_pct == null ? NaN : Number(t.change_pct);
        return `<button class="ht-tag" data-topic="${escapeHTML(t.name)}"><span class="topic-rank">${String(index+1).padStart(2,'0')}</span><span class="topic-name">${escapeHTML(t.name)}<span class="topic-sub">${t.kind === 'board' ? (t.amount == null ? '板块成交额暂不可用' : `板块成交 ${formatAmt(t.amount)}`) : `${t.mentions} 条资讯提及`}</span></span><span class="topic-change ${Number.isFinite(change) ? colorCls(change) === 'up' ? 'st-change-up' : change < 0 ? 'st-change-down' : '' : ''}">${Number.isFinite(change) ? `${colorSign(change)}${change.toFixed(2)}%` : '↗'}</span></button>`;
    }).join('');
    container.querySelectorAll('[data-topic]').forEach(button => button.addEventListener('click', () => switchToDiscover(button.dataset.topic)));
}
async function loadHotNews(force = false) {
    if (hotNewsPending) return;
    hotNewsPending = true;
    clearTimeout(hotNewsTimer);
    const button = $('#btn-refresh-topics');
    button.disabled = true;
    try {
        const r = await requestAPI(`/api/news/hot${force === true ? '?refresh=1' : ''}`);
        const data = await r.json();
        state.hotData = data;
        const topics = data.topics || [];
        renderTopics($('#hot-topics'), topics);
        renderTopics($('#discover-hot-topics'), topics);
        const status = data.refreshing ? '正在抓取…' : data.stale || data.topics_stale ? '上次缓存 · 正在重试' : '每 5 分钟更新';
        $('#topics-update').textContent = `${data.sources?.topics || '市场热点'} · ${status}${data.topics_updated_at || data.updated_at ? ` · 抓取 ${displayTime(data.topics_updated_at || data.updated_at, true)}` : ''}`;
        const news = data.news || [];
        if (news.length) {
            $('#hot-news').innerHTML = news.slice(0,8).map(n => `<article class="news-card">
                <div class="nc-meta"><span class="nc-source">${escapeHTML(n.source || '财经资讯')}</span><time>${escapeHTML(displayTime(n.date, true))}</time></div>
                <a href="${safeURL(n.url)}" target="_blank" rel="noopener noreferrer" class="nc-title">${escapeHTML(n.title)}<span class="nc-arrow">↗</span></a>
                ${n.summary ? `<p class="nc-summary">${escapeHTML(n.summary)}</p>` : ''}
            </article>`).join('');
        } else {
            $('#hot-news').innerHTML = dataState(data.refreshing ? '正在抓取市场快讯…' : '资讯源暂不可用，稍后会自动重试', data.refreshing ? null : 'news');
        }
        const note = $('.news-panel .panel-footnote');
        if (note) note.textContent = `${data.news_stale || data.stale ? '显示上次缓存 · ' : ''}聚合公开财经资讯 · 点击标题阅读原文${data.news_updated_at ? ` · 抓取 ${displayTime(data.news_updated_at, true)}` : ''}`;
        if (data.refreshing || data.status === 'unavailable') hotNewsTimer = setTimeout(loadHotNews, data.refreshing ? 2500 : 15000);
    } catch(e) {
        if (!state.hotData) {
            $('#hot-news').innerHTML = dataState('资讯连接失败，请稍后重试', 'news');
            $('#hot-topics').innerHTML = dataState('热点连接失败，请稍后重试', 'news');
        }
        $('#topics-update').textContent = '连接中断 · 将自动重试';
        hotNewsTimer = setTimeout(loadHotNews, 15000);
    } finally { hotNewsPending = false; button.disabled = false; }
}

async function loadCommentary(code) {
    const panel = $('#stock-commentary');
    panel.classList.remove('hidden');
    $('#commentary-title').textContent = '个股速读';
    $('#commentary-mode').textContent = '分析中';
    $('#commentary-body').textContent = '正在分析行情与近期走势…';
    $('#commentary-meta').textContent = '根据公开行情生成';
    try {
        const r = await requestAPI(`/api/stock/${code}/commentary`);
        const data = await r.json();
        if (state.currentStock?.code !== code) return;
        $('#commentary-title').textContent = data.mode === 'ai' ? 'AI 短评' : '行情速读';
        $('#commentary-mode').textContent = data.mode === 'ai' ? data.model || 'AI' : data.mode === 'unavailable' ? '数据不足' : '量价观察';
        $('#commentary-body').innerHTML = `<p>${escapeHTML(data.summary)}</p>${data.points?.length ? `<div class="commentary-points">${data.points.map(p => `<div class="commentary-point"><label>${escapeHTML(p.label)}</label>${escapeHTML(p.text)}</div>`).join('')}</div>` : ''}`;
        $('#commentary-meta').textContent = `${data.notice || ''}${data.quote_time ? ` · 行情 ${data.quote_time} 北京时间` : ''}${data.cached ? ' · 5分钟内缓存' : ''}`;
    } catch {
        if (state.currentStock?.code !== code) return;
        $('#commentary-mode').textContent = '暂不可用';
        $('#commentary-body').innerHTML = dataState('速读获取失败，可重新尝试', 'commentary');
        $('#commentary-meta').textContent = '请检查行情源连接';
    }
}

function switchToDiscover(topic) {
    setHash('discover', topic || null);
    $$('.mtab').forEach(b => b.classList.remove('active'));
    const discoverTab = $('.mtab[data-view="discover"]');
    if (discoverTab) discoverTab.classList.add('active');
    $$('.view').forEach(v => v.classList.remove('active'));
    $('#view-discover').classList.add('active');
    $('#topic-search-input').value = topic || '';
    if (topic) {
        searchTopic();
    } else {
        showDiscoverDefault();
        if (!state.discoverLoaded) loadDiscoverDefault();
    }
}

/* ═══════════════════════════════════════════════════════
   Discover / Topic Matching
   ═══════════════════════════════════════════════════════ */

async function loadDiscoverDefault() {
    const defaultContainer = $('#discover-default');
    if (!defaultContainer) return;

    try {
        if (state.hotData) renderTopics($('#discover-hot-topics'), state.hotData.topics || []);
        else loadHotNews();

        // Load hot stocks
        const stocksR = await requestAPI('/api/stocks/popular?top=10');
        const stocks = await stocksR.json();
        const stocksBody = $('#discover-hot-stocks-body');
        if (!stocks.length && stocksBody) stocksBody.innerHTML = '<tr><td colspan="5" class="data-state">暂无活跃股行情</td></tr>';
        if (stocks.length && stocksBody) {
            stocksBody.innerHTML = stocks.map(s => {
                const chgCls = s.change_pct > 0 ? 'st-change-up' : s.change_pct < 0 ? 'st-change-down' : '';
                return `<tr data-code="${s.code}" data-name="${escapeHTML(s.name)}" data-market="${stockMarket(s.code)}">
                    <td style="color:var(--tx3)">${s.code}</td>
                    <td style="font-weight:500">${escapeHTML(s.name)}</td>
                    <td>${s.price>0?s.price.toFixed(2):'--'}</td>
                    <td class="${chgCls}">${formatPercent(s.change_pct)}</td>
                    <td>${formatVol(s.volume)}</td>
                </tr>`;
            }).join('');

            stocksBody.querySelectorAll('tr').forEach(row => {
                row.addEventListener('click', () => {
                    selectStock(row.dataset.code, parseInt(row.dataset.market), row.dataset.name);
                });
            });
        }

        if (!$('#topic-search-input').value.trim()) defaultContainer.classList.remove('hidden');
        state.discoverLoaded = true;
    } catch(e) { $('#discover-hot-stocks-body').innerHTML = '<tr><td colspan="5" class="data-state">活跃股加载失败，请刷新重试</td></tr>'; }
}

function showDiscoverDefault() {
    topicRequest++;
    $('#topic-source-status').classList.add('hidden');
    const dd = $('#discover-default');
    if (dd) dd.classList.remove('hidden');
    $('#topic-empty').classList.add('hidden');
    $('#topic-loading').classList.add('hidden');
    $('#topic-concepts').classList.add('hidden');
    $('#topic-stocks-section').classList.add('hidden');
    $('#topic-news-section').classList.add('hidden');
}

let topicRequest = 0;
async function searchTopic() {
    const requestId = ++topicRequest;
    const query = $('#topic-search-input').value.trim();
    if (!query) {
        setHash('discover');
        showDiscoverDefault();
        if (!state.discoverLoaded) loadDiscoverDefault();
        return;
    }

    setHash('discover', query);
    // Hide default, show loading
    $('#topic-source-status').classList.add('hidden');
    const dd = $('#discover-default');
    if (dd) dd.classList.add('hidden');
    $('#topic-empty').classList.add('hidden');
    $('#topic-loading').classList.remove('hidden');
    $('#topic-concepts').classList.add('hidden');
    $('#topic-stocks-section').classList.add('hidden');
    $('#topic-news-section').classList.add('hidden');

    try {
        const r = await requestAPI(`/api/match?query=${encodeURIComponent(query)}`);
        const data = await r.json();
        if (requestId !== topicRequest) return;
        $('#topic-loading').classList.add('hidden');

        let hasResults = false;
        const status = $('#topic-source-status');
        if (data.match_status !== 'ok') {
            status.innerHTML = dataState('部分板块数据暂不可用，已显示可取得的结果', 'topic');
            status.classList.remove('hidden');
        }

        if (data.concepts && data.concepts.length) {
            $('#topic-concepts').innerHTML = data.concepts.map(c =>
                `<span class="concept-tag">${escapeHTML(c.name)} <small>${Math.min(100, c.score*100).toFixed(0)}%</small></span>`).join('');
            $('#topic-concepts').classList.remove('hidden');
            hasResults = true;
        }

        if (data.stocks && data.stocks.length) {
            $('#topic-stocks-count').textContent = `· 显示 ${data.stocks.length} / 本次匹配 ${data.total_related} 只`;
            $('#topic-stocks-body').innerHTML = data.stocks.map(s => {
                const chgCls = s.change_pct > 0 ? 'st-change-up' : s.change_pct < 0 ? 'st-change-down' : '';
                const relPct = Math.min(100, Math.round((s.relevance||0)*100));
                return `<tr data-code="${s.code}" data-name="${escapeHTML(s.name)}" data-market="${stockMarket(s.code)}">
                    <td style="color:var(--tx3)">${s.code}</td>
                    <td style="font-weight:500">${escapeHTML(s.name)}</td>
                    <td>${s.price>0?s.price.toFixed(2):'--'}</td>
                    <td class="${chgCls}">${formatPercent(s.change_pct)}</td>
                    <td style="color:var(--purple);font-size:.8rem">${escapeHTML(s.matched_via||'--')}</td>
                    <td><span class="relevance-bar" style="width:${Math.max(3,relPct/2)}px"></span>${relPct}%</td>
                </tr>`;
            }).join('');
            $('#topic-stocks-body').querySelectorAll('tr').forEach(row => {
                row.addEventListener('click', () => {
                    selectStock(row.dataset.code, parseInt(row.dataset.market), row.dataset.name);
                });
            });
            $('#topic-stocks-section').classList.remove('hidden');
            hasResults = true;
        }

        const allNews = [...(data.em_news||[]), ...(data.web_news||[])];
        $('#topic-news-status').textContent = data.news_status === 'fallback'
            ? (data.news_primary_status === 'unavailable' ? '东方财富资讯暂不可用 · 以下为备用来源' : '以下为补充来源')
            : '';
        if (allNews.length) {
            $('#topic-news-list').innerHTML = allNews.map(n =>
                `<div class="news-item">
                    <a href="${safeURL(n.url)}" target="_blank" rel="noopener noreferrer" class="news-title">${escapeHTML(n.title||'')}</a>
                    <div class="news-summary">${escapeHTML(n.summary||'')}</div>
                    <div class="news-meta"><span>${escapeHTML(n.source||'')}</span><span>${escapeHTML(n.date||'')}</span></div>
                </div>`).join('');
            $('#topic-news-section').classList.remove('hidden');
            hasResults = true;
        }

        if (!allNews.length) {
            $('#topic-news-list').innerHTML = dataState(data.news_status === 'unavailable' ? '资讯源暂不可用' : '暂无匹配资讯', 'topic');
            $('#topic-news-section').classList.remove('hidden');
        }
        if (!hasResults) {
            $('#topic-empty').innerHTML = dataState('未找到相关结果，试试其他关键词');
            $('#topic-empty').classList.remove('hidden');
        }
    } catch(e) {
        if (requestId !== topicRequest) return;
        $('#topic-loading').classList.add('hidden');
        $('#topic-empty').innerHTML = dataState('话题搜索暂不可用', 'topic');
        $('#topic-empty').classList.remove('hidden');
        console.error('Topic error:', e);
    }
}

/* ═══════════════════════════════════════════════════════
   Navigation & UI Events
   ═══════════════════════════════════════════════════════ */

function initNavigation() {
    bindGlobalSearch();
    document.addEventListener('click', e => {
        const retry = e.target.closest('[data-retry]');
        if (!retry) return;
        const actions = {indices:loadIndexQuotes, popular:loadPopularStocks, news:() => loadHotNews(true), commentary:() => state.currentStock && loadCommentary(state.currentStock.code),
            constituents:() => state.selectedIndex && loadConstituents(state.selectedIndex.code, state.selectedIndex.name),
            topic:searchTopic, 'stock-info':() => state.currentStock && loadStockInfo(state.currentStock.code, state.currentStock.market),
            chart:() => {
                const stock = state.currentStock;
                if (!stock) return;
                if (state.currentPeriod === 'intraday') loadIntradayChart(stock.code, stock.market, stock.name);
                else loadKlineChart(stock.code, stock.market, state.currentPeriod);
            }};
        actions[retry.dataset.retry]?.();
    });
    $('#btn-refresh-topics').addEventListener('click', () => loadHotNews(true));
    // Density toggle
    const densityBtn = $('#btn-density-toggle');
    if (densityBtn) densityBtn.addEventListener('click', toggleDensity);

    // Theme toggle
    const themeBtn = $('#btn-theme-toggle');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

    // Main tabs
    $$('.mtab').forEach(tab => {
        tab.addEventListener('click', () => {
            if (tab.disabled) return;
            const viewId = tab.dataset.view;
            $$('.mtab').forEach(b => b.classList.remove('active'));
            tab.classList.add('active');
            $$('.view').forEach(v => v.classList.remove('active'));
            $(`#view-${viewId}`).classList.add('active');

            // Update hash
            if (viewId === 'market') setHash('market');
            else if (viewId === 'discover') setHash('discover');
            else if (viewId === 'stock-detail' && state.currentStock) setHash('stock', state.currentStock.code);

            // Load discover default on first visit
            if (viewId === 'discover') {
                $('#topic-search-input').value = '';
                showDiscoverDefault();
                if (!state.discoverLoaded) loadDiscoverDefault();
            }

            if (viewId === 'stock-detail' && state.currentStock) {
                Object.values(state.chartInstances).forEach(c => { try { c.resize(); } catch(e) {} });
            }
        });
    });

    // Period tabs
    const chartTabs = $('#chart-period-tabs');
    if (chartTabs) {
        chartTabs.addEventListener('click', (e) => {
            const tab = e.target.closest('.cptab');
            if (!tab || !state.currentStock) return;
            const period = tab.dataset.period;

            if (period === 'intraday') {
                state.currentPeriod = 'intraday';
                $$('.cptab').forEach(b => b.classList.remove('active'));
                tab.classList.add('active');
                $('#chart-indicator').classList.remove('show');
                $('#chart-indicator2').classList.remove('show');
                $$('.ind-toggle').forEach(b => b.classList.remove('on'));
                state.activeIndicators = new Set([]);
                setHash('stock', state.currentStock.code);  // no period → intraday
                loadIntradayChart(state.currentStock.code, state.currentStock.market, state.currentStock.name);
            } else {
                state.currentPeriod = period;
                $$('.cptab').forEach(b => b.classList.remove('active'));
                tab.classList.add('active');
                const volToggle = $('.ind-toggle[data-ind="vol"]');
                if (volToggle && !state.activeIndicators.has('vol')) {
                    volToggle.classList.add('on');
                    state.activeIndicators.add('vol');
                }
                setHash('stock', state.currentStock.code, period);
                loadKlineChart(state.currentStock.code, state.currentStock.market, period);
            }
        });

        // Indicator toggles
        chartTabs.addEventListener('click', (e) => {
            const btn = e.target.closest('.ind-toggle');
            if (!btn || !state.currentStock) return;
            const ind = btn.dataset.ind;

            if (state.activeIndicators.has(ind)) {
                state.activeIndicators.delete(ind);
                btn.classList.remove('on');
            } else {
                state.activeIndicators.add(ind);
                btn.classList.add('on');
            }

            if (state.currentPeriod !== 'intraday') {
                loadKlineChart(state.currentStock.code, state.currentStock.market, state.currentPeriod);
            }


        });
    }

    $$('#constituents-panel [data-index-sort]').forEach(button => button.addEventListener('click', () => {
        if (!constituentRows.length) return;
        const field = button.dataset.indexSort;
        const direction = constituentSort?.field === field ? -constituentSort.direction : -1;
        constituentSort = {field, direction};
        $$('#constituents-panel th[aria-sort]').forEach(th => th.removeAttribute('aria-sort'));
        button.closest('th').setAttribute('aria-sort', direction === -1 ? 'descending' : 'ascending');
        renderConstituentRows();
    }));
    // Close constituents
    const closeBtn = $('#btn-close-constituents');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            $('#constituents-panel').classList.add('hidden');
            $$('.idx-card').forEach(c => c.classList.remove('selected'));
            state.selectedIndex = null;
            constituentsRequest++;
        });
    }

    // Watchlist star toggle
    const starBtn = $('#btn-watchlist-star');
    if (starBtn) {
        starBtn.addEventListener('click', () => {
            if (!state.currentStock) return;
            if (isInWatchlist(state.currentStock.code)) {
                removeFromWatchlist(state.currentStock.code);
            } else {
                addToWatchlist(state.currentStock.code, state.currentStock.name);
            }
        });
    }

    // Refresh button
    const refreshBtn = $('#btn-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            if (refreshBtn.disabled) return;
            refreshBtn.disabled = true;
            try { await Promise.allSettled([loadIndexQuotes(), refreshWatchlistQuotes(), loadPopularStocks(), loadHotNews(true)]); } finally { refreshBtn.disabled = false; }
            /* individual stock panels refresh independently */
            
            if (state.currentStock) {
                if (state.currentPeriod === 'intraday') {
                    loadIntradayChart(state.currentStock.code, state.currentStock.market, state.currentStock.name);
                } else {
                    loadKlineChart(state.currentStock.code, state.currentStock.market, state.currentPeriod);
                }
                loadStockInfo(state.currentStock.code, state.currentStock.market);
                loadCommentary(state.currentStock.code);
            }
        });
    }

    // Refresh popular stocks button
    const refreshPopBtn = $('#btn-refresh-popular');
    if (refreshPopBtn) refreshPopBtn.addEventListener('click', loadPopularStocks);

    // Clear watchlist
    const clearWlBtn = $('#btn-clear-watchlist-strip');
    if (clearWlBtn) {
        clearWlBtn.addEventListener('click', () => {
            if (state.watchlist.length && confirm('确定清空所有自选股？')) {
                state.watchlist = [];
                saveWatchlist();
                refreshWatchlistQuotes();
                updateStarButton();
            }
        });
    }

    // Topic search
    const searchBtn = $('#topic-search-btn');
    const searchInput = $('#topic-search-input');
    if (searchBtn) searchBtn.addEventListener('click', searchTopic);
    if (searchInput) searchInput.addEventListener('keydown', e => { if (e.key==='Enter') searchTopic(); });

    // Stock detail search
    const sdSearch = $('#stock-detail-search');
    const sdSug = $('#stock-detail-suggestions');
    if (sdSearch && sdSug) {
        let sdTimer;
        sdSearch.addEventListener('input', () => {
            clearTimeout(sdTimer);
            const kw = sdSearch.value.trim();
            if (kw.length < 1) { sdSug.classList.remove('show'); return; }
            sdTimer = setTimeout(async () => {
                try {
                    const r = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(kw)}`);
                    const data = await r.json();
                    if (sdSearch.value.trim() !== kw) return;
                    if (!data.length) { sdSug.classList.remove('show'); return; }
                    sdSug.innerHTML = data.map(s => `<div class="suggestion-item" data-code="${s.code}" data-market="${s.market}" data-name="${escapeHTML(s.name)}">
                        <span>${escapeHTML(s.name)}</span><span style="color:var(--tx3)">${s.full_code}</span></div>`).join('');
                    sdSug.classList.add('show');
                } catch(e) {}
            }, 250);
        });
        sdSearch.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                sdSug.classList.remove('show');
                const kw = sdSearch.value.trim();
                if (kw) autoSelectStock(kw, sdSearch);
            }
        });
        sdSug.addEventListener('click', (e) => {
            const item = e.target.closest('.suggestion-item');
            if (!item) return;
            sdSug.classList.remove('show');
            sdSearch.value = item.dataset.name;
            selectStock(item.dataset.code, parseInt(item.dataset.market), item.dataset.name);
        });
        document.addEventListener('click', e => {
            if (!sdSearch.contains(e.target) && !sdSug.contains(e.target)) sdSug.classList.remove('show');
        });
    }

    // Watchlist add search (in horizontal strip)
    const wlInput = $('#wl-strip-add-input');
    const wlSug = $('#wl-strip-suggestions');
    if (wlInput && wlSug) {
        let wlTimer;
        wlInput.addEventListener('input', () => {
            clearTimeout(wlTimer);
            const kw = wlInput.value.trim();
            if (kw.length < 1) { wlSug.classList.remove('show'); return; }
            wlTimer = setTimeout(async () => {
                try {
                    const r = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(kw)}`);
                    const data = await r.json();
                    if (wlInput.value.trim() !== kw) return;
                    if (!data.length) { wlSug.classList.remove('show'); return; }
                    wlSug.innerHTML = data.map(s => `<div class="ws-item" data-code="${s.code}" data-name="${escapeHTML(s.name)}">
                        <span>${escapeHTML(s.name)}</span><span class="ws-code">${s.code}</span></div>`).join('');
                    wlSug.classList.add('show');
                } catch(e) {}
            }, 250);
        });
        wlSug.addEventListener('click', (e) => {
            const item = e.target.closest('.ws-item');
            if (!item) return;
            wlSug.classList.remove('show');
            wlInput.value = '';
            addToWatchlist(item.dataset.code, item.dataset.name);
        });
        document.addEventListener('click', e => {
            if (!wlInput.contains(e.target) && !wlSug.contains(e.target)) wlSug.classList.remove('show');
        });
    }

    // Logo click → market view
    $('.logo').addEventListener('click', switchToMarket);

    // Resize handler
    window.addEventListener('resize', () => {
        Object.values(state.chartInstances).forEach(c => { try { c.resize(); } catch(e) {} });
    });
}

function bindGlobalSearch() {
    const input = $('#global-stock-search'), suggestions = $('#global-stock-suggestions');
    let timer, version = 0;
    input.addEventListener('input', () => {
        clearTimeout(timer);
        const id = ++version, keyword = input.value.trim();
        if (!keyword) { suggestions.classList.remove('show'); return; }
        timer = setTimeout(async () => {
            try {
                const r = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(keyword)}`), results = await r.json();
                if (id !== version) return;
                suggestions.innerHTML = results.length ? results.slice(0,8).map(stock => `<button type="button" class="suggestion-item" data-code="${escapeHTML(stock.code)}" data-name="${escapeHTML(stock.name)}" data-market="${stock.market}"><span>${escapeHTML(stock.name)}</span><span>${escapeHTML(stock.code)}</span></button>`).join('') : '<div class="data-state">未找到相关股票</div>';
                suggestions.classList.add('show');
            } catch { if (id === version) {suggestions.innerHTML = '<div class="data-state">搜索源暂不可用</div>';suggestions.classList.add('show');} }
        }, 250);
    });
    suggestions.addEventListener('click', e => {
        const item = e.target.closest('[data-code]');
        if (!item) return;
        version++;
        suggestions.classList.remove('show'); input.value = '';
        selectStock(item.dataset.code, Number(item.dataset.market), item.dataset.name);
    });
    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') { version++; suggestions.classList.remove('show'); input.blur(); }
        if (e.key === 'Enter') { version++; suggestions.classList.remove('show'); if (input.value.trim()) autoSelectStock(input.value.trim(), input); }
        if (e.key === 'ArrowDown') { e.preventDefault(); suggestions.querySelector('button')?.focus(); }
    });
    document.addEventListener('keydown', e => {
        if (e.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) { e.preventDefault(); input.focus(); }
    });
    document.addEventListener('click', e => { if (!e.target.closest('.global-search')) { version++; clearTimeout(timer); suggestions.classList.remove('show'); } });
}

async function autoSelectStock(keyword, inputEl) {
    try {
        const r = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(keyword)}`);
        const data = await r.json();
        if (data.length) {
            const s = data[0];
            inputEl.value = s.name;
            selectStock(s.code, s.market, s.name);
        }
    } catch(e) {}
}

/* ═══════════════════════════════════════════════════════
   Periodic Refresh
   ═══════════════════════════════════════════════════════ */

function startAutoRefresh() {
    // Refresh quotes every 30 seconds during Beijing trading sessions.
    setInterval(() => {
        const now = beijingTime();
        updateMarketTime();
        if (document.hidden) return;
        const day = now.getDay();
        const h = now.getHours(), m = now.getMinutes();
        const t = h * 60 + m;
        if (day >= 1 && day <= 5 && ((t >= 570 && t < 690) || (t >= 780 && t < 900))) {
            loadIndexQuotes();
            refreshWatchlistQuotes();
            loadPopularStocks();
            if (state.currentStock) loadStockInfo(state.currentStock.code, state.currentStock.market);
        }
    }, 30000);

    // Refresh hot news every 5 minutes
    setInterval(loadHotNews, 300000);
}

/* ═══════════════════════════════════════════════════════
   Init
   ═══════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', async () => {
    loadTheme();
    updateMarketTime();
    loadDensity();
    loadWatchlist();
    initNavigation();
    loadIndexQuotes();
    refreshWatchlistQuotes();
    loadPopularStocks();
    loadHotNews();
    startAutoRefresh();

    // Route to the correct view based on URL hash
    await handleHash();

    console.log('股探市场工作台已就绪');
});
