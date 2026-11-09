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
let feedbackTimer;
function showActionFeedback(message) {
    const status = $('#action-feedback');
    clearTimeout(feedbackTimer);
    status.textContent = message;
    feedbackTimer = setTimeout(() => { status.textContent = ''; }, 4000);
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
    case 'picks':
        switchToPicks(param1 || null);
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
        return Boolean(indices.length);
    } catch {
        $('#index-cards').innerHTML = dataState('行情连接失败，请稍后重试', 'indices');
        $('#index-update').textContent = '连接中断';
        return false;
    }
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
            switchToMarket();
            if (card) card.click();
            else showActionFeedback('该指数的成分股入口暂不可用');
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
            $('#constituents-panel').scrollIntoView({block:'start'});
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
            return false;
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
        return true;
    } catch(e) {
        container.innerHTML = dataState('活跃股加载失败', 'popular');
        if (countEl) countEl.textContent = '0';
        console.error('Popular stocks error:', e);
        return false;
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

function isInWatchlist(code) {
    return state.watchlist.some(w => w.code === code);
}

function updateWatchlist(next, message) {
    try {
        localStorage.setItem('watchlist', JSON.stringify(next));
    } catch {
        showActionFeedback('自选保存失败，请检查浏览器的存储设置');
        return;
    }
    state.watchlist = next;
    renderWatchlist();
    updateStarButton();
    showActionFeedback(message);
    refreshWatchlistQuotes();
}

function addToWatchlist(code, name) {
    if (!/^\d{6}$/.test(code)) return;
    if (isInWatchlist(code)) {
        showActionFeedback(`${name}已在自选中`);
        return;
    }
    if (state.watchlist.length >= 30) {
        showActionFeedback('自选已满 30 只，移除一只后再添加');
        return;
    }
    updateWatchlist([...state.watchlist, {code, name, addedAt: Date.now()}], `已添加 ${name} 到自选`);
}

function removeFromWatchlist(code) {
    const stock = state.watchlist.find(item => item.code === code);
    if (stock) updateWatchlist(state.watchlist.filter(w => w.code !== code), `已移除自选 ${stock.name}`);
}

let watchlistQuotes = new Map();
let watchlistRequest = 0;

function renderWatchlist() {
    const container = $('#wl-strip-items');
    $('#wl-strip-count').textContent = state.watchlist.length;
    $('#btn-clear-watchlist-strip').disabled = !state.watchlist.length;

    if (!state.watchlist.length) {
        container.innerHTML = '<span class="wl-strip-empty">添加第一只自选股，关注它的每一次变化</span>';
        return;
    }

    container.innerHTML = state.watchlist.map(w => {
        const s = watchlistQuotes.get(w.code);
        const name = escapeHTML(s?.name || w.name || w.code);
        const cls = s?.change_pct > 0 ? 'wl-up' : s?.change_pct < 0 ? 'wl-down' : '';
        const activeCls = state.currentStock?.code === w.code ? 'active' : '';
        return `<div class="wl-strip-mini ${activeCls}" data-code="${w.code}" data-market="${stockMarket(w.code)}" data-name="${name}">
            <span class="wls-code">${w.code}</span>
            <span class="wls-name">${name}</span>
            <span class="wls-price ${cls}">${s?.price > 0 ? s.price.toFixed(2) : '—'}</span>
            ${s ? `<span class="wls-change ${cls}">${formatPercent(s.change_pct)}</span>` : ''}
            <button type="button" class="wls-remove" data-code="${w.code}" aria-label="从自选移除 ${name}">✕</button></div>`;
    }).join('');

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

async function refreshWatchlistQuotes() {
    const id = ++watchlistRequest;
    const codes = state.watchlist.map(w => w.code);
    if (!codes.length) return true;
    try {
        const response = await requestAPI(`/api/stocks/batch?codes=${encodeURIComponent(codes.join(','))}`);
        const stocks = await response.json();
        if (id === watchlistRequest) {
            watchlistQuotes = new Map(stocks.map(stock => [stock.code, stock]));
            renderWatchlist();
        }
        return codes.every(code => stocks.some(stock => stock.code === code && stock.price > 0));
    } catch {
        return false;
    }
}

function updateStarButton() {
    const btn = $('#btn-watchlist-star');
    if (!btn) return;
    if (!state.currentStock) { btn.style.display = 'none'; return; }
    btn.style.display = '';
    const starred = isInWatchlist(state.currentStock.code);
    btn.setAttribute('aria-pressed', String(starred));
    btn.setAttribute('aria-label', starred ? '移出自选' : '加入自选');
    if (starred) {
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
    const openingStock = state.currentStock?.code !== code || !$('#view-stock-detail').classList.contains('active');
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
    if (openingStock) header?.scrollIntoView({block:'start'});
    loadCommentary(code);
    loadStockSteady(code);
    loadStockResearch(code);
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
const KLINE_LEFT = 0.08, KLINE_RIGHT = 0.03;
const KLINE_GRID = {left:`${KLINE_LEFT * 100}%`, right:`${KLINE_RIGHT * 100}%`};
const KLINE_MIN_SPAN = 3;
let klineViewport;
let removeKlineGestures;

function disposeAllCharts() {
    removeKlineGestures?.();
    removeKlineGestures = null;
    $('#kline-gesture-hint').classList.add('hidden');
    Object.values(state.chartInstances).forEach(c => { if (!c.isDisposed()) c.dispose(); });
    state.chartInstances = {};
    Object.values(indicatorPanels).forEach(id => document.getElementById(id).classList.remove('show'));
}

async function loadIntradayChart(code, market, name) {
    disposeAllCharts();
    klineViewport = null;
    $('#chart-status').textContent = '正在加载分时…';
    $$('.ind-toggle').forEach(button => { button.disabled = true; });
    const chart = initChart('chart-main');
    if (!chart) {
        $('#chart-status').textContent = '图表组件暂不可用';
        return;
    }
    chart.showLoading();

    try {
        const r = await requestAPI(`/api/stock/${code}/intraday`);
        const data = await r.json();
        if (chart.isDisposed() || state.currentStock?.code !== code || state.currentPeriod !== 'intraday') return;
        chart.hideLoading();

        if (!data.minutes || !data.minutes.length) {
            $('#chart-status').innerHTML = dataState('暂未取得分时行情', 'chart');
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

function bindKlineGestures(chart, viewport) {
    const dom = chart.getDom();
    const scroller = $('#chart-scroll');
    const listeners = new AbortController();
    const options = {capture:true, passive:false, signal:listeners.signal};
    let frame = null;
    let pending;
    let lastPointer;
    let gestureScale = null;
    let gestureAnchor = 0.5;
    let lastScrollLeft = 0;
    let viewWidth = 0;

    function plotWidth() {
        return scroller.clientWidth * (1 - KLINE_LEFT - KLINE_RIGHT);
    }

    function anchorAt(event) {
        const pointer = Number.isFinite(event.clientX) && Number.isFinite(event.clientY) ? event : lastPointer;
        if (!pointer) return 0.5;
        const rect = dom.getBoundingClientRect();
        const fraction = (pointer.clientX - rect.left - scroller.clientWidth * KLINE_LEFT) / Math.max(1, plotWidth());
        return Math.max(0, Math.min(1, fraction));
    }

    function syncScroll() {
        const width = scroller.clientWidth;
        if (!width) return;
        if (viewWidth !== width) {
            viewWidth = width;
            scroller.style.setProperty('--kline-view-width', `${width}px`);
            if (chart.getWidth() !== width) chart.resize({width});
        }
        const span = viewport.end - viewport.start;
        const scrollDistance = Math.ceil(plotWidth() * (100 - span) / span);
        scroller.style.setProperty('--kline-scroll-width', `${width + scrollDistance}px`);
        const maxScroll = scroller.scrollWidth - scroller.clientWidth;
        scroller.scrollLeft = span < 100 ? maxScroll * viewport.start / (100 - span) : 0;
        lastScrollLeft = scroller.scrollLeft;
    }

    function updateRange(start, span, fromScroll = false) {
        const nextStart = Math.max(0, Math.min(100 - span, start));
        const end = nextStart + span;
        const range = pending || viewport;
        if (nextStart === range.start && end === range.end) return;
        pending = {start:nextStart, end, fromScroll};
        if (frame !== null) return;
        frame = requestAnimationFrame(() => {
            frame = null;
            const next = pending;
            pending = null;
            if (!next || chart.isDisposed()) return;
            const {start, end, fromScroll} = next;
            Object.assign(viewport, {start, end});
            chart.dispatchAction({type:'dataZoom', dataZoomId:'dzInside', start, end, animation:{duration:0}});
            if (!fromScroll) syncScroll();
        });
    }

    function zoomAt(anchor, factor) {
        const range = pending || viewport;
        const span = range.end - range.start;
        const nextSpan = Math.max(KLINE_MIN_SPAN, Math.min(100, span * factor));
        updateRange(range.start + (span - nextSpan) * anchor, nextSpan);
    }

    function syncRange(event) {
        const range = event.batch?.[0] || event;
        if (!Number.isFinite(range.start) || !Number.isFinite(range.end) ||
            (range.start === viewport.start && range.end === viewport.end)) return;
        Object.assign(viewport, {start:range.start, end:range.end});
        pending = null;
        syncScroll();
    }

    scroller.classList.add('kline-scroll');
    scroller.tabIndex = 0;
    scroller.setAttribute('role', 'region');
    scroller.setAttribute('aria-label', 'K 线时间轴，可用左右方向键平移');
    syncScroll();
    const resize = new ResizeObserver(syncScroll);
    resize.observe(scroller);
    chart.on('datazoom', syncRange);

    function panFromScroll() {
        const maxScroll = scroller.scrollWidth - scroller.clientWidth;
        const left = Math.max(0, Math.min(maxScroll, scroller.scrollLeft));
        if (!maxScroll || Math.abs(left - lastScrollLeft) < 0.5) return;
        lastScrollLeft = left;
        const span = viewport.end - viewport.start;
        updateRange(left / maxScroll * (100 - span), span, true);
    }

    function panBy(pixels) {
        scroller.scrollLeft += pixels;
        panFromScroll();
    }

    const passive = {passive:true, signal:listeners.signal};
    scroller.addEventListener('scroll', panFromScroll, passive);
    scroller.addEventListener('pointermove', event => { lastPointer = event; }, passive);
    scroller.addEventListener('keydown', event => {
        if (event.target !== scroller || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
        event.preventDefault();
        panBy((event.key === 'ArrowLeft' ? -1 : 1) * plotWidth() / 8);
    }, options);
    scroller.addEventListener('wheel', event => {
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? plotWidth() : 1;
        if (!event.ctrlKey && !event.metaKey) {
            const horizontal = event.deltaX || -event.wheelDeltaX || 0;
            if (event.cancelable && Math.abs(horizontal) > Math.abs(event.deltaY)) {
                event.preventDefault();
                panBy(horizontal * unit);
            }
            return;
        }
        if (!event.deltaY) return;
        event.preventDefault();
        if (gestureScale !== null) return;
        const delta = Math.max(-160, Math.min(160, event.deltaY * unit));
        zoomAt(anchorAt(event), Math.exp(delta / 250));
    }, options);
    // Safari reports trackpad pinch as a cumulative scale through WebKit gesture events.
    scroller.addEventListener('gesturestart', event => {
        gestureAnchor = anchorAt(event);
        gestureScale = 1;
        event.preventDefault();
    }, options);
    scroller.addEventListener('gesturechange', event => {
        if (gestureScale === null || !Number.isFinite(event.scale) || event.scale <= 0) return;
        event.preventDefault();
        zoomAt(gestureAnchor, gestureScale / event.scale);
        gestureScale = event.scale;
    }, options);
    scroller.addEventListener('gestureend', event => {
        if (gestureScale === null) return;
        gestureScale = null;
        event.preventDefault();
    }, options);

    return () => {
        listeners.abort();
        resize.disconnect();
        chart.off('datazoom', syncRange);
        if (frame !== null) cancelAnimationFrame(frame);
        scroller.classList.remove('kline-scroll');
        scroller.removeAttribute('tabindex');
        scroller.removeAttribute('role');
        scroller.removeAttribute('aria-label');
        scroller.style.removeProperty('--kline-view-width');
        scroller.style.removeProperty('--kline-scroll-width');
        scroller.scrollLeft = 0;
    };
}

async function loadKlineChart(code, market, period) {
    disposeAllCharts();
    if (klineViewport?.code !== code || klineViewport?.period !== period) klineViewport = null;
    $('#chart-status').textContent = '正在加载K线…';
    $$('.ind-toggle').forEach(button => { button.disabled = false; });

    const mainChart = initChart('chart-main');
    if (!mainChart) {
        $('#chart-status').textContent = '图表组件暂不可用';
        return;
    }
    mainChart.showLoading();

    try {
        const r = await requestAPI(`/api/stock/${code}/chart?period=${period}`);
        const result = await r.json();
        if (mainChart.isDisposed() || state.currentStock?.code !== code || state.currentPeriod !== period) return;
        mainChart.hideLoading();
        if (!result.data || !result.data.length) {
            $('#chart-status').innerHTML = dataState('暂未取得 K 线行情', 'chart');
            mainChart.setOption({title:{text:'暂无数据',left:'center',top:'center',textStyle:{color:chartText()}}}, true);
            return;
        }

        $('#chart-status').textContent = '新浪财经 · 不复权 · 成交量单位：手';
        Object.entries(indicatorPanels).forEach(([key, id]) => {
            document.getElementById(id).classList.toggle('show', state.activeIndicators.has(key) && Boolean(result.indicators?.[key]));
        });
        const data = result.data;
        const initialStart = Math.max(0, 100 - Math.min(80, 100 * 80 / data.length));
        klineViewport ??= {code, period, start:initialStart, end:100};
        const {start, end} = klineViewport;
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
            animation:false,
            tooltip: {trigger:'axis',axisPointer:{type:'cross'},backgroundColor:chartBg(),
                borderColor:chartBorder(),textStyle:{color:chartText(),fontSize:11}},
            legend: {top:0,left:'center',textStyle:{color:chartText(),fontSize:10},data:['K线','MA5','MA10','MA20','MA60']},
            dataZoom: [
                {type:'inside', id:'dzInside', xAxisIndex:[0,1],
                    start, end, minSpan:KLINE_MIN_SPAN, maxSpan:100,
                    throttle:0,
                    zoomOnMouseWheel:false,
                    moveOnMouseWheel:false,
                },
                {type:'slider', id:'dzSlider', xAxisIndex:[0,1],
                    start, end, minSpan:KLINE_MIN_SPAN, maxSpan:100,
                    throttle:0,
                    bottom:2, height:16,
                    borderColor:chartBorder(), backgroundColor:chartBg(), textStyle:{color:chartText()}},
            ],
            grid: [{...KLINE_GRID,top:'6%',height:'50%'},{...KLINE_GRID,top:'62%',height:'14%'}],
            xAxis: [{type:'category',data:dates,gridIndex:0,axisLabel:{color:chartText(),fontSize:10},axisLine:{lineStyle:{color:chartBorder()}}},
                    {type:'category',data:dates,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:chartBorder()}}}],
            yAxis: [{type:'value',gridIndex:0,scale:true,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:10}},
                    {type:'value',gridIndex:1,splitLine:{lineStyle:{color:chartGrid()}},axisLabel:{color:chartText(),fontSize:10}}],
            series: state.activeIndicators.has('vol') ? [...mainSeries, volSeries] : mainSeries,
        }, true);

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
        removeKlineGestures = bindKlineGestures(mainChart, klineViewport);
        $('#kline-gesture-hint').classList.remove('hidden');
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
    if (hotNewsPending) return 'pending';
    hotNewsPending = true;
    clearTimeout(hotNewsTimer);
    const button = $('#btn-refresh-topics');
    const note = $('#topics-update');
    let refreshing = false;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    if (force) note.textContent = '正在更新热门话题…';
    try {
        const r = await requestAPI(`/api/news/hot${force === true ? '?refresh=1' : ''}`);
        const data = await r.json();
        state.hotData = data;
        const topics = data.topics || [];
        renderTopics($('#hot-topics'), topics);
        renderTopics($('#discover-hot-topics'), topics);
        refreshing = Boolean(data.refreshing);
        const cached = data.stale || data.last_attempt_at > data.fetched_at;
        const status = refreshing ? '正在抓取…' : !topics.length ? '暂未取得热门话题' :
            cached || data.topics_stale ? '显示上次缓存' : '每 5 分钟更新';
        note.textContent = `${data.sources?.topics || '市场热点'} · ${status}${data.topics_updated_at || data.updated_at ? ` · 抓取 ${displayTime(data.topics_updated_at || data.updated_at, true)}` : ''}`;
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
        const newsNote = $('.news-panel .panel-footnote');
        if (newsNote) newsNote.textContent = `${data.news_stale || cached ? '显示上次缓存 · ' : ''}聚合公开财经资讯 · 点击标题阅读原文${data.news_updated_at ? ` · 抓取 ${displayTime(data.news_updated_at, true)}` : ''}`;
        if (data.refreshing || data.status === 'unavailable') hotNewsTimer = setTimeout(loadHotNews, data.refreshing ? 2500 : 15000);
        return refreshing ? 'pending' : Boolean(topics.length && news.length && !cached && !data.topics_stale && !data.news_stale);
    } catch(e) {
        if (!state.hotData) {
            $('#hot-news').innerHTML = dataState('资讯连接失败，请稍后重试', 'news');
            $('#hot-topics').innerHTML = dataState('热点连接失败，请稍后重试', 'news');
        }
        note.textContent = '连接中断 · 将自动重试';
        hotNewsTimer = setTimeout(loadHotNews, 15000);
        return false;
    } finally {
        hotNewsPending = false;
        button.disabled = refreshing;
        button.setAttribute('aria-busy', String(refreshing));
    }
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

const researchState = {request: 0, controller: null, data: null, kind: 'all', limit: 8};

function researchDate(date) {
    return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
}

function setResearchRange(days) {
    const end = beijingTime();
    const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - (days - 1));
    const today = researchDate(end);
    $('#research-end').max = today;
    $('#research-start').max = today;
    $('#research-end').value = today;
    $('#research-start').value = researchDate(start);
    $$('[data-research-days]').forEach(btn => btn.setAttribute('aria-pressed', String(Number(btn.dataset.researchDays) === days)));
}

function researchLink(url, label) {
    return url ? `<a href="${safeURL(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)} ↗</a>` : '';
}

function researchMoney(value, currency) {
    if (!Number.isFinite(value)) return '—';
    if (currency === 'CNY') return `${(value / 1e8).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2})} 亿`;
    return `${value.toLocaleString('zh-CN', {maximumFractionDigits:2})} ${escapeHTML(currency || '原币')}`;
}

function renderResearchFinancial(data) {
    const f = data.financial;
    const section = $('#research-financial');
    if (!f) {
        const msg = data.sources.financial === 'out_of_range' ? '可取得的财报中，没有在此截止日前发布的记录。' : '财务数据源暂不可用。';
        section.innerHTML = `<h4>经营状况</h4>${dataState(msg, data.sources.financial === 'unavailable' ? 'research' : null)}`;
        return;
    }
    const cashPerShare = f.operating_cash == null && f.cash_per_share != null;
    const metrics = [
        ['营业总收入', researchMoney(f.revenue, f.currency), f.revenue_yoy, '同比'],
        ['归母净利润', researchMoney(f.net_profit, f.currency), f.profit_yoy, '同比'],
        cashPerShare ? ['每股经营现金流', `${f.cash_per_share.toFixed(2)} ${escapeHTML(f.currency || '原币')}`, f.cash_per_share_yoy, '同比']
            : ['经营活动现金流净额', researchMoney(f.operating_cash, f.currency), null, null],
        ['资产负债率', Number.isFinite(f.debt_ratio) ? f.debt_ratio.toFixed(2) + '%' : '—', null, null],
    ];
    const rows = data.reports.map(report => `<tr><td>${escapeHTML(report.period)}</td><td>${escapeHTML(report.disclosed_on)}</td>
        <td>${escapeHTML(report.available_on)}${report.updated_after_cutoff ? '（晚于截止日）' : ''}</td>
        <td>${researchMoney(report.revenue, report.currency)}</td><td>${researchMoney(report.net_profit, report.currency)}</td>
        <td>${researchMoney(report.operating_cash, report.currency)}</td></tr>`).join('');
    section.innerHTML = `<div class="section-heading"><h4>经营状况</h4><span class="small-badge">${escapeHTML(f.period_name || f.period)}</span></div>
        <p class="research-help">报告期 ${escapeHTML(f.period)} · 披露 ${escapeHTML(f.disclosed_on)} · 收入、利润与现金流为累计口径${f.currency === 'CNY' ? '，金额为人民币' : ''}</p>
        ${f.updated_after_cutoff ? `<p class="research-help research-version">平台于 ${escapeHTML(f.available_on)} 更新该期数据。以下为可取得的版本，可能与截止日当天看到的数字不同。</p>` : ''}
        <div class="research-metrics">${metrics.map(([label, value, change, sub]) => `<div class="research-metric"><span>${label}</span><strong>${value}</strong>
            ${Number.isFinite(change) ? `<small class="${change > 0 ? 'st-change-up' : change < 0 ? 'st-change-down' : ''}">${sub} ${formatPercent(change)}</small>` : ''}</div>`).join('')}</div>
        <div class="research-links">${researchLink(f.source_url, f.source)} ${researchLink(f.filing_url, '财报 PDF')}</div>
        <details class="research-history"><summary>历期数据（${data.reports.length} 期）</summary><div class="stock-table-wrap"><table class="stock-table">
            <thead><tr><th>报告期</th><th>披露日期</th><th>数据版本日期</th><th>营业总收入</th><th>归母净利润</th><th>经营现金流净额</th></tr></thead><tbody>${rows}</tbody>
        </table></div></details>`;
}

function renderResearchTimeline() {
    const data = researchState.data;
    if (!data) return;
    const {kind, limit} = researchState;
    const records = data.timeline.filter(item => kind === 'all' || item.kind === kind);
    $$('[data-research-kind]').forEach(btn => btn.setAttribute('aria-pressed', String(btn.dataset.researchKind === kind)));
    const notes = [`${data.start} 至 ${data.end} · ${records.length} 条`];
    const hasMedia = data.timeline.some(row => row.kind === 'media');
    if (kind !== 'disclosure') {
        if (data.sources.media === 'unavailable') notes.push(hasMedia ? '历史媒体索引暂不可用，已显示新浪近期报道' : '历史媒体索引暂不可用');
        else notes.push('媒体报道按索引收录');
        if (data.truncated.media) notes.push('媒体结果达到上限，可缩小日期范围');
    }
    if (kind !== 'media') {
        if (data.sources.disclosures === 'unavailable') notes.push('巨潮资讯暂不可用');
        else if (data.sources.disclosures === 'partial') notes.push('公告仅取得部分结果，可重新查询');
        else if (data.truncated.disclosures) notes.push('最多收录 60 条公告，可缩小日期范围');
    }
    $('#research-coverage').textContent = notes.join(' · ');
    const timeline = $('#research-timeline');
    const missingSource = (kind === 'disclosure' && data.sources.disclosures === 'unavailable') ||
        (kind === 'media' && data.sources.media === 'unavailable' && !hasMedia) ||
        (kind === 'all' && (data.sources.media === 'unavailable' || data.sources.disclosures === 'unavailable'));
    timeline.innerHTML = records.length ? records.slice(0, limit).map(item => `<article class="research-event">
        <time datetime="${escapeHTML(item.date)}">${escapeHTML(item.date)}</time>
        <div><div class="research-event-meta"><span class="small-badge">${item.kind === 'disclosure' ? '公告' : '媒体'}</span><span>${escapeHTML(item.category)}</span><span>${escapeHTML(item.source)}${item.via ? ' · ' + escapeHTML(item.via) : ''}</span></div>
        ${researchLink(item.url, item.title)}</div></article>`).join('') : dataState(
            missingSource ? '有来源暂未返回可用资料。' : '此日期范围没有收录到此类记录。',
            missingSource ? 'research' : null);
    $('#research-more').classList.toggle('hidden', records.length <= limit);
}

function renderResearch(data) {
    const company = data.company;
    $('#research-company').innerHTML = company
        ? `<div class="section-heading"><h4>${escapeHTML(company.name)}</h4>${company.industry ? `<span class="small-badge">${escapeHTML(company.industry)}</span>` : ''}</div>
           ${company.business ? `<p>${escapeHTML(company.business)}</p>` : ''}<div class="research-links">${researchLink(company.website, '公司官网')} ${researchLink(company.source_url, company.source)}</div>`
        : `<h4>${escapeHTML(data.name)}</h4><p class="research-help">公司资料暂不可用。</p>`;
    renderResearchFinancial(data);
    $('#research-insights').innerHTML = `<div class="section-heading"><h4>经营与事件分析</h4><span class="small-badge">规则联读</span></div>
        ${data.reports.some(report => report.updated_after_cutoff) ? '<p class="research-help">部分财务数据来自截止日后更新的平台版本，分析仅供回顾。</p>' : ''}
        ${data.insights.length ? `<div class="research-insight-grid">${data.insights.map(insight => `<article class="research-insight">
        <h5>${escapeHTML(insight.title)}</h5><p>${escapeHTML(insight.text)}</p>
        <div class="research-links">${insight.refs.map(ref => researchLink(ref.url, ref.label)).join(' ')}</div></article>`).join('')}</div>`
        : dataState('暂无足够的财报或公司事件材料可供分析。')}`;
    renderResearchTimeline();
}

async function loadStockResearch(code) {
    const panel = $('#stock-research');
    panel.classList.remove('hidden');
    const status = $('#research-status');
    const start = $('#research-start').value, end = $('#research-end').value;
    const id = ++researchState.request;
    researchState.controller?.abort();
    researchState.data = null;
    researchState.kind = 'all';
    researchState.limit = 8;
    $('#research-results').classList.add('hidden');
    panel.setAttribute('aria-busy', 'false');
    if (!start || !end || start > end || end > $('#research-end').max) {
        status.textContent = '请选择有效的起止日期，截止日期不能晚于今天。';
        return;
    }
    if ((Date.parse(end) - Date.parse(start)) / 86400000 + 1 > 730) {
        status.textContent = '单次最多查询 730 天，请缩小日期范围。';
        return;
    }
    if (!/^(60|68|00|30)\d{4}$/.test(code)) {
        status.textContent = '公司研究暂支持沪深 A 股。';
        return;
    }
    const controller = new AbortController();
    researchState.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 45000);
    panel.setAttribute('aria-busy', 'true');
    status.textContent = '正在查询公司资料、财报、新闻与公告…';
    try {
        const params = new URLSearchParams({start, end});
        const response = await requestAPI(`/api/stock/${encodeURIComponent(code)}/research?${params}`, {signal: controller.signal});
        const data = await response.json();
        if (id !== researchState.request || state.currentStock?.code !== code) return;
        researchState.data = data;
        renderResearch(data);
        status.textContent = '';
        $('#research-results').classList.remove('hidden');
    } catch {
        if (id !== researchState.request || state.currentStock?.code !== code) return;
        status.innerHTML = dataState('公司研究资料加载失败，请稍后重试。', 'research');
    } finally {
        clearTimeout(timeout);
        if (id === researchState.request) panel.setAttribute('aria-busy', 'false');
    }
}

function bindStockResearch() {
    setResearchRange(90);
    $('#btn-open-research').addEventListener('click', () => {
        const panel = $('#stock-research');
        panel.focus({preventScroll: true});
        panel.scrollIntoView({block: 'start'});
    });
    $('#research-form').addEventListener('submit', event => {
        event.preventDefault();
        if (state.currentStock) loadStockResearch(state.currentStock.code);
    });
    $$('[data-research-days]').forEach(btn => btn.addEventListener('click', () => {
        setResearchRange(Number(btn.dataset.researchDays));
        if (state.currentStock) loadStockResearch(state.currentStock.code);
    }));
    ['#research-start','#research-end'].forEach(selector => $(selector).addEventListener('input', () => {
        $$('[data-research-days]').forEach(btn => btn.setAttribute('aria-pressed', 'false'));
    }));
    $$('[data-research-kind]').forEach(btn => btn.addEventListener('click', () => {
        researchState.kind = btn.dataset.researchKind;
        researchState.limit = 8;
        renderResearchTimeline();
    }));
    $('#research-more').addEventListener('click', () => {
        researchState.limit += 8;
        renderResearchTimeline();
    });
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
    pendingTopic = null;
    setTopicLoading(false);
    $('#topic-action-status').textContent = '';
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
let pendingTopic = null;

function setTopicLoading(loading) {
    const button = $('#topic-search-btn');
    button.disabled = loading;
    button.setAttribute('aria-busy', String(loading));
    button.textContent = loading ? '匹配中' : '探索话题 ↗';
}

async function searchTopic() {
    const input = $('#topic-search-input');
    const query = input.value.trim();
    const actionStatus = $('#topic-action-status');
    if (query === pendingTopic) return;
    if (!query) {
        setHash('discover');
        showDiscoverDefault();
        actionStatus.textContent = '请输入话题，或点击下方的热门话题';
        input.focus();
        if (!state.discoverLoaded) loadDiscoverDefault();
        return;
    }

    const requestId = ++topicRequest;
    pendingTopic = query;
    setTopicLoading(true);
    actionStatus.textContent = `正在匹配「${query}」…`;
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
        actionStatus.textContent = hasResults ? '' : '未找到相关结果，试试其他关键词';
    } catch(e) {
        if (requestId !== topicRequest) return;
        $('#topic-loading').classList.add('hidden');
        $('#topic-empty').innerHTML = dataState('话题搜索暂不可用', 'topic');
        $('#topic-empty').classList.remove('hidden');
        actionStatus.textContent = '话题搜索暂不可用，请重试';
        console.error('Topic error:', e);
    } finally {
        if (requestId === topicRequest) {
            pendingTopic = null;
            setTopicLoading(false);
        }
    }
}

/* ═══════════════════════════════════════════════════════
   Backtest Picks — 回测选股
   #picks        → 当前热点话题
   #picks/TOPIC  → 指定话题
   ═══════════════════════════════════════════════════════ */

const picksState = {request: 0, data: null, strategy: 'ma_cross', window: 'year', pickedOnly: false, loaded: false};
const ADVICE = {
    keep: ['继续持有', 'pick-signal-hold'], sell: ['建议卖出', 'pick-signal-sell'],
    buy: ['建议买入', 'pick-signal-buy'], watch: ['观望', 'pick-signal-flat'],
};

// 同一个规则信号，按你是否真的持有给出不同建议：
// 持有 → 继续持有 / 建议卖出；没持有 → 建议买入 / 观望（没持有的股票不会显示「持有」）
function adviceFor(state, held) {
    if (!state) return null;
    const long = state.signal === 'buy' || state.signal === 'hold';
    const key = held ? (long ? 'keep' : 'sell') : (long ? 'buy' : 'watch');
    let detail = '';
    if (state.signal === 'buy') detail = `买入信号 ${state.as_of} 收盘`;
    else if (state.signal === 'sell') detail = `卖出信号 ${state.as_of} 收盘`;
    else if (state.signal === 'hold') detail = held ? `持有信号自 ${state.since}` : `上涨信号自 ${state.since} 起，不是新信号`;
    else if (held) detail = `自 ${state.since} 起已转为空仓信号`;
    const [label, cls] = ADVICE[key];
    return {key, label, cls, detail};
}

function adviceHTML(advice) {
    if (!advice) return '<span class="pick-signal pick-signal-flat">数据不足</span>';
    return `<span class="pick-signal ${advice.cls}">${advice.label}</span>${advice.detail ? `<small>${escapeHTML(advice.detail)}</small>` : ''}`;
}

function pickPercent(value) {
    return Number.isFinite(value) ? `${colorSign(value)}${(value * 100).toFixed(2)}%` : '—';
}
function pickChangeClass(value) {
    return value > 0 ? 'st-change-up' : value < 0 ? 'st-change-down' : '';
}

const steadyPct = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
const steadyPrice = value => Number.isFinite(value) ? value.toFixed(2) : '—';

async function loadSteadyPool() {
    const status = $('#steady-pool-status');
    const details = $('#steady-pool-details');
    const badge = $('#steady-pool-badge');
    status.textContent = '正在读取月度名单…';
    try {
        const data = await (await requestAPI('/api/steady/pool')).json();
        badge.textContent = data.stale ? '历史名单' : `${data.stocks.length} 只候选`;
        status.textContent = `${data.active_month} 名单 · ${data.as_of} 收盘筛选 · `
            + `股息率门槛 ${steadyPct(data.yield_floor)}${data.stale ? ' · 最新月度数据待更新' : ''}`;
        $('#steady-pool-body').innerHTML = data.stocks.map(stock => {
            const {selection} = stock;
            return `<tr><td>${selection.rank}</td><td><button type="button" class="steady-stock-link" data-steady-code="${escapeHTML(stock.code)}" data-steady-name="${escapeHTML(stock.name)}">${escapeHTML(stock.name)} <small>${escapeHTML(stock.code)}</small></button></td><td>${steadyPct(selection.yield)}</td><td>${steadyPct(selection.volatility)}</td></tr>`;
        }).join('');
        details.classList.remove('hidden');
    } catch {
        badge.textContent = '暂不可用';
        details.classList.add('hidden');
        status.innerHTML = dataState('月度股票池暂不可用', 'steady-pool');
    }
}

let steadyRequest = 0;
async function loadStockSteady(code) {
    const id = ++steadyRequest;
    const badge = $('#stock-steady-badge');
    const status = $('#stock-steady-status');
    const body = $('#stock-steady-body');
    $('#stock-steady').classList.remove('hidden');
    badge.textContent = '分析中';
    status.textContent = '正在核对月度候选与已收盘日线…';
    body.innerHTML = '';
    try {
        const data = await (await requestAPI(`/api/stock/${encodeURIComponent(code)}/steady`)).json();
        if (id !== steadyRequest || state.currentStock?.code !== code) return;
        renderStockSteady(data, badge, status, body);
    } catch {
        if (id !== steadyRequest || state.currentStock?.code !== code) return;
        badge.textContent = '暂不可用';
        status.innerHTML = dataState('稳健派分析暂不可用', 'stock-steady');
    }
}

function renderStockSteady({pool, selection, signal, archived, source, strategy}, badge, status, body) {
    const candidate = selection?.selected;
    badge.textContent = !selection ? '无筛选记录' : pool.stale ? '历史名单' : candidate ? '已入池' : '未入池';
    status.textContent = [pool ? `候选名单 ${pool.active_month} · ${pool.as_of} 收盘筛选` : '月度名单暂不可用',
        signal ? `行情截至 ${signal.as_of} 收盘 · ${source}` : '前复权日线暂不可用'].join(' ｜ ');
    const metrics = (rows) => `<div class="research-metrics">${rows.map(([label, value]) =>
        `<div class="research-metric"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`
    ).join('')}</div>`;
    let selectionHTML = '<p class="research-help">该股票在此期名单没有可用的筛选记录。</p>';
    if (selection) {
        const reasons = [];
        if (!selection.priced) reasons.push('筛选日缺少有效收盘价');
        if (selection.years < 3) reasons.push('过去三个滚动年度未全部实施现金分红');
        if (selection.turnover == null || selection.turnover < pool.turnover_floor) reasons.push('近 20 日成交额中位数未达到门槛');
        if (!selection.volatility) reasons.push('日线样本不足以进行有效低波排名');
        if (selection.eligible && selection.yield < pool.yield_floor) reasons.push('股息率低于合格股票的中位数');
        if (selection.rank && !candidate) reasons.push(`高股息组低波排名第 ${selection.rank}，候选取前 50`);
        if (candidate) reasons.push(`通过分红和流动性门槛，高股息组低波排名第 ${selection.rank}`);
        selectionHTML = metrics([
            ['有现金分红的年度', `${selection.years} / 3`], ['近一年股息率', steadyPct(selection.yield)],
            ['年化波动率', steadyPct(selection.volatility)], ['近 20 日成交额中位数', formatAmt(selection.turnover)],
        ]) + `<p class="research-help">${escapeHTML(reasons.join(' · '))}</p>`;
    }

    let tradeHTML = signal ? '<p class="research-help">已收盘日线不足 120 根，暂不能计算突破条件。</p>'
        : dataState('前复权日线暂不可用', 'stock-steady');
    if (signal?.enough) {
        const allowed = candidate && !pool.stale && signal.as_of >= pool.as_of;
        const checks = [['收盘在 100 日线上方', signal.above_ma], ['100 日线较 20 日前上升', signal.rising_ma],
            ['收盘突破此前 55 日高点', signal.breakout]];
        let plan = `<p>${signal.setup ? '技术突破条件成立，当前名单未确认可入场。' : '尚未同时满足三项突破条件。'}</p>`;
        if (signal.setup && allowed && signal.entry_passed) {
            // 下一交易日已经开过盘：不能再把这个计划当成还能照做的买入条件
            plan = `<p>${escapeHTML(signal.as_of)} 收盘时突破条件成立，按规则应在下一交易日开盘买入；那次开盘已经过去，这个入场计划已过期。</p>
               <p>${archived ? '实时行情暂不可用，以上是本地归档数据，行情恢复后会显示最新信号。' : '收盘后会按最新日线更新。'}</p>`;
        } else if (signal.setup && allowed) {
            plan = `<p>${archived ? '归档交易计划' : '突破条件成立'}。次一交易日开盘须高于 ${steadyPrice(signal.stop)} 元，且不超过 ${steadyPrice(signal.max_open)} 元。</p>
               <p>参考止损 ${steadyPrice(signal.stop)} 元，按信号日收盘估算 1R 止盈 ${steadyPrice(signal.indicative_target)} 元。实际目标按成交价重新计算。</p>`;
        }
        const previous = allowed && signal.previous_plan;
        let previousHTML = '';
        if (previous) {
            const details = previous.filled
                ? `开盘 ${steadyPrice(previous.opening)} 元符合模拟入场条件，止损 ${steadyPrice(previous.stop)} 元，1R 目标 ${steadyPrice(previous.target)} 元。`
                : '开盘价或当日可交易条件未满足，计划跳过。';
            previousHTML = `<p>${escapeHTML(previous.entry_day)} 开盘回看 · ${details}${previous.stop_due ? ' 入场日收盘跌破止损，模型等下一可交易日开盘退出。' : ''}</p>`;
        }
        tradeHTML = metrics([['最新收盘', steadyPrice(signal.close)], ['100 日均线', steadyPrice(signal.ma_100)],
            ['此前 55 日最高', steadyPrice(signal.high_55)], ['近 10 日最低', steadyPrice(signal.stop)]])
            + `<ul class="steady-checks">${checks.map(([label, met]) => `<li><strong>${met ? '满足' : '未满足'}</strong> · ${label}</li>`).join('')}</ul>`
            + `<div class="steady-plan">${plan}${previousHTML}</div>`;
    }
    body.innerHTML = `<div class="steady-section"><h4>入池依据</h4>${selectionHTML}</div>
        <div class="steady-section"><h4>${escapeHTML(strategy)} 交易条件</h4>${tradeHTML}
            <p class="panel-footnote">模型买入当天不卖出。成交后的固定止盈位是成交价加上成交价到初始止损的距离，触及止盈或止损时退出。</p></div>`;
}

function switchToPicks(topic, options) {
    $$('.mtab').forEach(b => b.classList.toggle('active', b.dataset.view === 'picks'));
    $$('.view').forEach(v => v.classList.remove('active'));
    $('#view-picks').classList.add('active');
    $('#picks-topic-input').value = topic || '';
    loadSteadyPool();
    loadHoldings();
    loadPicks(options);
}

async function loadPicks({revealResults = false} = {}) {
    const query = $('#picks-topic-input').value.trim();
    const id = ++picksState.request;
    const submit = $('#picks-submit');
    const label = $('#picks-submit-label');
    const results = $('#picks-results');
    const status = $('#picks-status');
    submit.disabled = revealResults;
    label.textContent = revealResults ? '回测中' : '开始回测';
    setHash('picks', query || null);
    const params = new URLSearchParams({scope: query ? 'topic' : 'hot', window: picksState.window});
    if (query) params.set('query', query);
    picksState.data = null;
    renderPicks();
    results.classList.add('hidden');
    $('#picks-loading').classList.remove('hidden');
    status.textContent = query ? `正在回测与「${query}」相关的股票…` : '正在回测当前热点话题里的股票…';
    try {
        const response = await window.fetch(`/api/picks?${params}`, {signal: AbortSignal.timeout(90000)});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
        if (id !== picksState.request) return;
        picksState.data = data;
        picksState.loaded = true;
        status.textContent = '';
        renderPicks();
        results.classList.remove('hidden');
    } catch (error) {
        if (id !== picksState.request) return;
        status.innerHTML = dataState(error.name === 'TimeoutError'
            ? '回测超时，行情源响应较慢，请稍后重试' : error.message || '回测失败，请稍后重试', 'picks');
    } finally {
        if (id === picksState.request) {
            $('#picks-loading').classList.add('hidden');
            submit.disabled = false;
            label.textContent = '开始回测';
        }
    }
    if (revealResults && !results.classList.contains('hidden') && $('#view-picks').classList.contains('active')) {
        results.focus({preventScroll: true});
        const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        results.scrollIntoView({block: 'start', behavior: reducedMotion ? 'auto' : 'smooth'});
    }
}

function renderPicks() {
    const data = picksState.data;
    const key = picksState.strategy;
    $$('[data-picks-strategy]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.picksStrategy === key)));
    $$('[data-picks-window]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.picksWindow === picksState.window)));
    $('#picks-picked-only').setAttribute('aria-pressed', String(picksState.pickedOnly));
    updatePicksHotButton();
    renderHoldings();  // 持仓建议也跟着所选规则变化
    if (!data) return;

    const strategy = data.strategies[key];
    const cost = value => `${(value * 100).toFixed(3)}%`;
    $('#picks-rule').textContent = `${strategy.name}：${strategy.rule}。收盘后出信号、下一个交易日开盘成交，`
        + `每笔扣除买入 ${cost(data.costs.buy)}、卖出 ${cost(data.costs.sell)} 的费用。`;

    // 两种规则的结果后端一次算好，切换规则只需要重新排序
    const rows = data.stocks.map(stock => ({...stock, result: stock.results[key]}))
        .sort((a, b) => b.result.total_return - a.result.total_return);
    const picked = rows.filter(row => row.result.picked);
    const shown = picksState.pickedOnly ? picked : rows;

    $('#picks-topics').innerHTML = data.topics.map(topic => `<button type="button" class="concept-tag" data-picks-topic="${escapeHTML(topic.name)}" title="只回测「${escapeHTML(topic.name)}」">${escapeHTML(topic.name)}${Number.isFinite(topic.change_pct) ? ` <small class="${pickChangeClass(topic.change_pct)}">${formatPercent(topic.change_pct)}</small>` : ''}</button>`).join('');
    const start = rows.map(row => row.start).sort()[0], end = rows.map(row => row.end).sort().at(-1);
    $('#picks-summary').innerHTML = `${escapeHTML(data.scope === 'topic' ? `话题「${data.query}」` : '当前热点话题')} · ${escapeHTML(data.window_label)}${rows.length ? `（${escapeHTML(start)} 至 ${escapeHTML(end)}）` : ''} · 回测 ${rows.length} 只 · <strong>入选 ${picked.length} 只</strong>`;

    const body = $('#picks-body');
    const held = new Set(holdingsState.records.map(record => record.code));
    body.innerHTML = shown.length ? shown.map((row, index) => {
        const r = row.result;
        const isHeld = held.has(row.code);
        const notes = [row.code, row.days < data.window_days ? `仅 ${row.days} 个交易日` : '', row.adjusted ? '' : '不复权'].filter(Boolean).join(' · ');
        const topics = row.topics.join('、');
        return `<tr data-code="${escapeHTML(row.code)}" data-name="${escapeHTML(row.name)}"${r.picked ? ' class="pick-row-picked"' : ''}>
            <td class="pick-rank">${index + 1}</td>
            <td><span class="pick-name">${escapeHTML(row.name)}</span>${r.picked ? '<span class="pick-badge">入选</span>' : ''}${isHeld ? '<span class="pick-badge pick-badge-held">已持有</span>' : ''}<small>${escapeHTML(notes)}</small></td>
            <td class="pick-topic" title="${escapeHTML(topics)}">${escapeHTML(topics)}</td>
            <td class="${pickChangeClass(r.total_return)}"><b>${pickPercent(r.total_return)}</b></td>
            <td class="${pickChangeClass(row.benchmark_return)}">${pickPercent(row.benchmark_return)}</td>
            <td class="${pickChangeClass(r.excess_return)}">${pickPercent(r.excess_return)}</td>
            <td>${pickPercent(r.max_drawdown)}</td>
            <td>${r.trades} 次${r.win_rate == null ? '' : ` · ${Math.round(r.win_rate * 100)}%`}</td>
            <td>${adviceHTML(adviceFor(r, isHeld))}</td>
        </tr>`;
    }).join('') : `<tr><td colspan="9">${dataState(picksState.pickedOnly ? '这个规则下暂时没有入选的股票，可以换一种规则或区间看看' : '没有可显示的回测结果')}</td></tr>`;
    body.querySelectorAll('tr[data-code]').forEach(tr => tr.addEventListener('click', () =>
        selectStock(tr.dataset.code, stockMarket(tr.dataset.code), tr.dataset.name)));
    makeKeyboardClickable(body, 'tr[data-code]');

    const names = list => list.map(stock => stock.name || stock.code).join('、');
    $('#picks-footnote').textContent = [
        '入选 = 回测区间内策略赚钱、跑赢「持有不动」，并且最新信号看多（含刚出现的买入信号）',
        '操作建议按「我的持仓」区分：已持有的给继续持有 / 建议卖出，没持有的给建议买入 / 观望',
        '收益按比例计算，不受 100 股一手的限制',
        `数据：${data.data_source}`,
        data.unadjusted ? '不复权数据会把分红除权日的跌价算进收益' : '',
        data.failed.length ? `${data.failed.length} 只取不到行情：${names(data.failed)}` : '',
        data.skipped.length ? `${data.skipped.length} 只上市时间太短未回测：${names(data.skipped)}` : '',
        `计算于 ${displayTime(data.generated_at, true)}${data.cached ? '（10 分钟内的缓存结果）' : ''}`,
        '历史回测不代表未来表现，仅供研究参考，不构成投资建议',
    ].filter(Boolean).join(' · ');
}

// 输入了话题（包括加载失败后）就显示「改用当前热点」，方便一键回到热点话题
function updatePicksHotButton() {
    $('#picks-use-hot').classList.toggle('hidden', !$('#picks-topic-input').value.trim() && picksState.data?.scope !== 'topic');
}

function bindPicks() {
    $('#picks-form').addEventListener('submit', event => {
        event.preventDefault();
        if (!$('#picks-submit').disabled) loadPicks({revealResults: true});
    });
    $('#picks-topic-input').addEventListener('input', updatePicksHotButton);
    $$('[data-picks-strategy]').forEach(btn => btn.addEventListener('click', () => {
        picksState.strategy = btn.dataset.picksStrategy;
        renderPicks();
    }));
    $$('[data-picks-window]').forEach(btn => btn.addEventListener('click', () => {
        if (picksState.window === btn.dataset.picksWindow && picksState.data) return;
        picksState.window = btn.dataset.picksWindow;
        loadPicks({revealResults: true});
    }));
    $('#picks-picked-only').addEventListener('click', () => {
        picksState.pickedOnly = !picksState.pickedOnly;
        renderPicks();
    });
    $('#picks-use-hot').addEventListener('click', () => {
        $('#picks-topic-input').value = '';
        loadPicks({revealResults: true});
    });
    $('#picks-topics').addEventListener('click', event => {
        const tag = event.target.closest('[data-picks-topic]');
        if (!tag) return;
        $('#picks-topic-input').value = tag.dataset.picksTopic;
        loadPicks({revealResults: true});
    });
    $('#btn-topic-backtest').addEventListener('click', () =>
        switchToPicks($('#topic-search-input').value.trim(), {revealResults: true}));
    renderPicks();
}

/* ═══════════════════════════════════════════════════════
   My Holdings — 我的持仓（记录保存在服务器的 data/holdings.json）
   ═══════════════════════════════════════════════════════ */

const holdingsState = {records: [], signals: {}, strategies: {}, loaded: false, request: 0, selected: null};

async function loadHoldings() {
    const id = ++holdingsState.request;
    if (!holdingsState.loaded) $('#holdings-list').innerHTML = dataState('正在读取持仓记录并分析…');
    try {
        const response = await window.fetch('/api/holdings', {signal: AbortSignal.timeout(60000)});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
        if (id !== holdingsState.request) return;
        Object.assign(holdingsState, {records: data.records || [], signals: data.signals || {},
            strategies: data.strategies || {}, loaded: true});
        renderHoldings();
        if (picksState.data) renderPicks();  // 排名里的操作建议取决于持有了哪些股票
    } catch (error) {
        if (id !== holdingsState.request) return;
        $('#holdings-count').textContent = '读取失败';
        $('#holdings-list').innerHTML = dataState(error.name === 'TimeoutError' ? '持仓分析超时，请稍后重试' : error.message, 'holdings');
    }
}

function renderHoldings() {
    if (!holdingsState.loaded) return;
    const records = holdingsState.records;
    const key = picksState.strategy;
    $('#holdings-count').textContent = `${records.length} 条记录`;
    if (!records.length) {
        $('#holdings-list').innerHTML = dataState('还没有持仓记录。记下买入的股票后，这里会按所选规则给出「继续持有」或「建议卖出」。');
        return;
    }
    const strategyName = holdingsState.strategies[key]?.name || '';
    $('#holdings-list').innerHTML = `<div class="stock-table-wrap"><table class="stock-table holdings-table"><thead><tr>
        <th>股票</th><th>买入日期</th><th>买入价</th><th>现价</th><th>盈亏</th><th>${escapeHTML(strategyName)}建议</th><th></th>
        </tr></thead><tbody>${records.map(record => {
            const info = holdingsState.signals[record.code];
            const money = value => value.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            const cost = Number.isFinite(record.buy_price) ? money(record.buy_price) : '—';
            const costNote = record.price_estimate === 'live' ? `按 ${record.price_estimated_on} 记录时的现价估算`
                : record.price_estimated_on ? `按 ${record.price_estimated_on} 收盘价估算`
                : record.buy_price == null ? '没查到当天价格' : '';
            const price = Number.isFinite(record.price) ? money(record.price) : '—';
            const profit = Number.isFinite(record.profit) ? `${colorSign(record.profit)}${money(record.profit)} 元` : '';
            return `<tr data-code="${escapeHTML(record.code)}" data-name="${escapeHTML(record.name || record.code)}">
                <td><span class="pick-name">${escapeHTML(record.name || record.code)}</span><small>${escapeHTML(record.code)}${info && !info.adjusted ? ' · 不复权' : ''}</small></td>
                <td>${escapeHTML(record.buy_date)}${record.shares ? `<small>${record.shares} 股</small>` : ''}</td>
                <td>${cost}${costNote ? `<small>${escapeHTML(costNote)}</small>` : ''}</td>
                <td>${price}${record.price_is_preclose ? '<small>停牌或未开盘，按昨收</small>' : ''}</td>
                <td class="${pickChangeClass(record.return_rate)}">${pickPercent(record.return_rate)}${profit ? `<small>${profit}</small>` : ''}</td>
                <td>${adviceHTML(adviceFor(info?.signals?.[key], true))}</td>
                <td><button type="button" class="text-btn holding-remove" data-holding-id="${escapeHTML(record.id)}">删除</button></td>
            </tr>`;
        }).join('')}</tbody></table></div>`;
}

function isEligibleHoldingStock(stock) {
    return /^(60|68|00|30)\d{4}$/.test(stock.code);
}

async function resolveHoldingStock() {
    if (holdingsState.selected) return holdingsState.selected;
    const text = $('#holding-stock').value.trim();
    const code = text.match(/(?:^|\D)((?:60|68|00|30)\d{4})(?:\D|$)/)?.[1];
    if (code) return {code, name: ''};
    if (!text) return null;
    const response = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(text)}`);
    const match = (await response.json()).find(isEligibleHoldingStock);
    return match ? {code: match.code, name: match.name} : null;
}

async function addHolding() {
    const status = $('#holding-status'), button = $('#holding-form button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = '记录中';
    status.textContent = '正在确认股票信息…';
    const details = {buy_date: $('#holding-date').value, buy_price: $('#holding-price').value, shares: $('#holding-shares').value};
    try {
        const stock = await resolveHoldingStock();
        if (!stock) {
            status.textContent = '没找到这只股票，请从下拉列表里选择沪深 A 股';
            return;
        }
        status.textContent = '正在保存买入记录…';
        const response = await window.fetch('/api/holdings', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, signal: AbortSignal.timeout(30000),
            body: JSON.stringify({code: stock.code, name: stock.name, ...details}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `记录失败 (${response.status})`);
        const priceNote = data.price_estimate === 'live' ? `，买入价按记录时的现价 ${data.buy_price.toFixed(2)} 元估算`
            : data.price_estimated_on ? `，买入价按 ${data.price_estimated_on} 收盘价 ${data.buy_price.toFixed(2)} 元估算`
            : data.buy_price == null ? '，没查到当天价格，盈亏暂不计算' : '';
        status.textContent = `已记录：${data.name} ${data.buy_date} 买入${priceNote}`;
        holdingsState.selected = null;
        ['#holding-stock', '#holding-price', '#holding-shares'].forEach(selector => { $(selector).value = ''; });
        await loadHoldings();
    } catch (error) {
        status.textContent = error.name === 'TimeoutError' ? '记录超时，请稍后重试' : error.message;
    } finally {
        button.disabled = false;
        button.setAttribute('aria-busy', 'false');
        button.textContent = '记录买入';
    }
}

async function removeHolding(button, name) {
    if (button.disabled) return;
    if (!confirm(`删除「${name}」的这条买入记录？`)) return;
    const status = $('#holding-status');
    const id = button.dataset.holdingId;
    button.disabled = true;
    button.textContent = '删除中…';
    try {
        const response = await window.fetch(`/api/holdings/${encodeURIComponent(id)}`, {method: 'DELETE', signal: AbortSignal.timeout(15000)});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `删除失败 (${response.status})`);
        status.textContent = `已删除「${name}」的买入记录`;
        showActionFeedback(status.textContent);
        await loadHoldings();
    } catch (error) {
        status.textContent = error.name === 'TimeoutError' ? '删除超时，请稍后重试' : error.message;
        showActionFeedback(status.textContent);
    } finally {
        button.disabled = false;
        button.textContent = '删除';
    }
}

function bindHoldings() {
    const input = $('#holding-stock'), suggestions = $('#holding-suggestions');
    const today = researchDate(beijingTime());
    $('#holding-date').max = today;
    $('#holding-date').value = today;
    input.addEventListener('input', () => { holdingsState.selected = null; });
    const closeSearch = bindStockLookup(input, suggestions, stock => {
        holdingsState.selected = {code: stock.code, name: stock.name};
        input.value = `${stock.name} ${stock.code}`;
    }, {filter: isEligibleHoldingStock, selectOnEnter: false});
    $('#holding-form').addEventListener('submit', event => {
        event.preventDefault();
        closeSearch();
        addHolding();
    });
    $('#holdings-list').addEventListener('click', event => {
        const remove = event.target.closest('[data-holding-id]');
        if (remove) { removeHolding(remove, remove.closest('tr').dataset.name); return; }
        const row = event.target.closest('tr[data-code]');
        if (row) selectStock(row.dataset.code, stockMarket(row.dataset.code), row.dataset.name);
    });
}

/* ═══════════════════════════════════════════════════════
   Navigation & UI Events
   ═══════════════════════════════════════════════════════ */

function initNavigation() {
    bindGlobalSearch();
    bindStockResearch();
    bindPicks();
    bindHoldings();
    $('#steady-pool-body').addEventListener('click', event => {
        const button = event.target.closest('[data-steady-code]');
        if (button) selectStock(button.dataset.steadyCode, stockMarket(button.dataset.steadyCode), button.dataset.steadyName, 'daily');
    });
    document.addEventListener('click', e => {
        const retry = e.target.closest('[data-retry]');
        if (!retry) return;
        const actions = {indices:loadIndexQuotes, popular:loadPopularStocks, news:() => loadHotNews(true), commentary:() => state.currentStock && loadCommentary(state.currentStock.code),
            research:() => state.currentStock && loadStockResearch(state.currentStock.code),
            constituents:() => state.selectedIndex && loadConstituents(state.selectedIndex.code, state.selectedIndex.name),
            topic:searchTopic, picks:() => loadPicks({revealResults: true}), holdings:loadHoldings, 'stock-info':() => state.currentStock && loadStockInfo(state.currentStock.code, state.currentStock.market),
            'steady-pool':loadSteadyPool, 'stock-steady':() => state.currentStock && loadStockSteady(state.currentStock.code),
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
            else if (viewId === 'picks') setHash('picks', $('#picks-topic-input').value.trim() || null);
            else if (viewId === 'stock-detail' && state.currentStock) setHash('stock', state.currentStock.code);

            // First visit (or after a failed load): backtest the current hot topics
            if (viewId === 'picks') { loadHoldings(); loadSteadyPool(); }
            if (viewId === 'picks' && !picksState.loaded && $('#picks-loading').classList.contains('hidden')) loadPicks();

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
            refreshBtn.setAttribute('aria-busy', 'true');
            showActionFeedback('正在刷新行情与热点…');
            try {
                const tasks = [loadIndexQuotes(), refreshWatchlistQuotes(), loadPopularStocks(), loadHotNews(true)];
                const stock = state.currentStock;
                if (stock && $('#view-stock-detail').classList.contains('active')) {
                    if (state.currentPeriod === 'intraday') loadIntradayChart(stock.code, stock.market, stock.name);
                    else loadKlineChart(stock.code, stock.market, state.currentPeriod);
                    $('#stock-quote-status').textContent = '正在刷新个股行情…';
                    loadStockInfo(stock.code, stock.market);
                    loadCommentary(stock.code);
                }
                const refreshed = await Promise.allSettled(tasks);
                const failed = refreshed.some(item => item.status === 'rejected' || item.value === false);
                const pending = refreshed[3].value === 'pending';
                showActionFeedback(failed ? '部分行情或资讯暂未更新，请查看各栏提示' :
                    pending ? '行情已刷新，热点仍在抓取' : '行情与热点已刷新');
            } finally {
                refreshBtn.disabled = false;
                refreshBtn.setAttribute('aria-busy', 'false');
            }
        });
    }

    // Refresh popular stocks button
    const refreshPopBtn = $('#btn-refresh-popular');
    if (refreshPopBtn) refreshPopBtn.addEventListener('click', async () => {
        if (refreshPopBtn.disabled) return;
        refreshPopBtn.disabled = true;
        refreshPopBtn.textContent = '刷新中…';
        try {
            showActionFeedback(await loadPopularStocks() ? '活跃股已刷新' : '活跃股行情暂不可用');
        } finally {
            refreshPopBtn.disabled = false;
            refreshPopBtn.textContent = '刷新';
        }
    });

    // Clear watchlist
    const clearWlBtn = $('#btn-clear-watchlist-strip');
    if (clearWlBtn) {
        clearWlBtn.addEventListener('click', () => {
            if (state.watchlist.length && confirm('确定清空所有自选股？')) {
                updateWatchlist([], '已清空自选');
            }
        });
    }

    // Topic search
    const searchBtn = $('#topic-search-btn');
    const searchInput = $('#topic-search-input');
    if (searchBtn) searchBtn.addEventListener('click', searchTopic);
    if (searchInput) searchInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) searchTopic();
    });

    const detailInput = $('#stock-detail-search');
    bindStockLookup(detailInput, $('#stock-detail-suggestions'), stock => {
        detailInput.value = stock.name;
        selectStock(stock.code, stock.market, stock.name);
    });

    const watchlistInput = $('#wl-strip-add-input');
    bindStockLookup(watchlistInput, $('#wl-strip-suggestions'), stock => {
        watchlistInput.value = '';
        addToWatchlist(stock.code, stock.name);
    });

    // Logo click → market view
    $('.logo').addEventListener('click', switchToMarket);

    // Resize handler
    window.addEventListener('resize', () => {
        Object.values(state.chartInstances).forEach(c => { try { c.resize(); } catch(e) {} });
    });
}

function bindStockLookup(input, suggestions, onChoose, {filter = () => true, selectOnEnter = true} = {}) {
    let timer, version = 0;
    input.setAttribute('aria-controls', suggestions.id);

    function closeSearch() {
        version++;
        clearTimeout(timer);
        input.removeAttribute('aria-busy');
        suggestions.classList.remove('show');
    }

    function showStatus(message) {
        suggestions.innerHTML = `<div class="search-feedback" role="status">${escapeHTML(message)}</div>`;
        suggestions.classList.add('show');
    }

    async function search(keyword, id, chooseFirst) {
        try {
            const response = await requestAPI(`/api/stock/search?keyword=${encodeURIComponent(keyword)}`);
            const stocks = (await response.json()).filter(filter);
            if (id !== version) return;
            if (!stocks.length) {
                showStatus('未找到匹配的股票，请试试代码或简称');
            } else if (chooseFirst) {
                closeSearch();
                onChoose(stocks[0]);
            } else {
                suggestions.innerHTML = stocks.slice(0, 8).map(stock => `<button type="button" class="suggestion-item" data-code="${escapeHTML(stock.code)}" data-name="${escapeHTML(stock.name)}" data-market="${escapeHTML(stock.market)}">
                    <span>${escapeHTML(stock.name)}</span><span>${escapeHTML(stock.code)}</span></button>`).join('');
            }
        } catch {
            if (id === version) showStatus('股票搜索暂不可用，请稍后重试');
        } finally {
            if (id === version) input.removeAttribute('aria-busy');
        }
    }

    function startSearch(immediate) {
        closeSearch();
        const keyword = input.value.trim();
        if (!keyword) return;
        const id = version;
        showStatus('正在搜索股票…');
        input.setAttribute('aria-busy', 'true');
        if (immediate) search(keyword, id, true);
        else timer = setTimeout(() => search(keyword, id, false), 250);
    }

    input.addEventListener('input', () => startSearch(false));
    input.addEventListener('keydown', event => {
        if (event.isComposing || event.keyCode === 229) return;
        if (event.key === 'Escape') closeSearch();
        if (event.key === 'Enter' && selectOnEnter) {
            event.preventDefault();
            startSearch(true);
        }
        if (event.key === 'ArrowDown' && suggestions.classList.contains('show')) {
            event.preventDefault();
            suggestions.querySelector('button')?.focus();
        }
    });
    suggestions.addEventListener('click', event => {
        const item = event.target.closest('button[data-code]');
        if (!item) return;
        closeSearch();
        onChoose({code: item.dataset.code, name: item.dataset.name, market: Number(item.dataset.market)});
    });
    suggestions.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        closeSearch();
        input.focus();
    });
    document.addEventListener('click', event => {
        if (event.target !== input && !suggestions.contains(event.target)) closeSearch();
    });
    return closeSearch;
}

function bindGlobalSearch() {
    const input = $('#global-stock-search');
    bindStockLookup(input, $('#global-stock-suggestions'), stock => {
        input.value = '';
        selectStock(stock.code, stock.market, stock.name);
    });
    document.addEventListener('keydown', event => {
        if (event.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {
            event.preventDefault();
            input.focus();
        }
    });
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
    renderWatchlist();
    refreshWatchlistQuotes();
    loadPopularStocks();
    loadHotNews();
    startAutoRefresh();

    // Route to the correct view based on URL hash
    await handleHash();

    console.log('股探市场工作台已就绪');
});
