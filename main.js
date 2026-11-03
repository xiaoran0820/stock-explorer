/**
 * 股探 StockExplorer v3 — 前端主逻辑
 * 深浅色主题 · 大盘行情 · 自选股 · 热门人气股 · 分时/K线/指标 · 成分股 · 热点掘金
 */

const $ = (s,p) => (p||document).querySelector(s);
const $$ = (s,p) => (p||document).querySelectorAll(s);

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
    const parts = raw.split('/').map(decodeURIComponent);
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
            const mkt = param1.startsWith('6') || param1.startsWith('9') ? 1 : 0;
            let name = param1;
            try {
                const r = await fetch(`/api/stock/search?keyword=${encodeURIComponent(param1)}`);
                const data = await r.json();
                if (data.length) name = data[0].name;
            } catch(e) {}
            // Restore period: param2 = daily/weekly/monthly, default = intraday
            const period = param2 || null;
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
    if (btn) btn.textContent = theme === 'light' ? '☀️' : '🌙';
}

function getChartTheme() {
    const light = document.documentElement.dataset.theme === 'light';
    return {
        bg: light ? '#ffffff' : '#1c2333',
        text: light ? '#333333' : '#8b949e',
        grid: light ? '#e0e0e0' : '#21262d',
        border: light ? '#d0d7de' : '#30363d',
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
            c.setOption({ backgroundColor: theme.bg }, true);
            c.resize();
        } catch(e) {}
    });
}

/* ═══════════════════════════════════════════════════════
   Density (Spacing) Management
   ═══════════════════════════════════════════════════════ */

const DENSITY_LEVELS = ['compact', 'normal', 'relaxed'];
const DENSITY_LABELS = { compact: '📏c', normal: '📏', relaxed: '📏r' };

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
    if (!v) return '--';
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

/* ═══════════════════════════════════════════════════════
   Index Cards & Bottom Strip
   ═══════════════════════════════════════════════════════ */

async function loadIndexQuotes() {
    try {
        const r = await fetch('/api/index/quotes');
        const indices = await r.json();
        renderBottomStrip(indices);
        renderIndexCards(indices);
        updateMarketTime();
    } catch(e) { console.error('Index load error:', e); }
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
    container.innerHTML = indices.map(i => {
        const cls = i.change_pct >= 0 ? 'idx-up' : 'idx-down';
        return `<div class="idx-card ${cls}" data-code="${i.code}" data-name="${i.name}">
            <div class="ic-header"><span class="ic-name">${i.name}</span><span class="ic-code">${i.code}</span></div>
            <div class="ic-price">${i.price.toFixed(2)}</div>
            <div class="ic-change">${colorSign(i.change_pct)}${i.change_pct.toFixed(2)}%  ${colorSign(i.change_amt)}${i.change_amt.toFixed(2)}</div>
            <div class="ic-detail">
                <span>开盘<b>${i.open.toFixed(2)}</b></span>
                <span>最高<b>${i.high.toFixed(2)}</b></span>
                <span>最低<b>${i.low.toFixed(2)}</b></span>
                <span>成交额<b>${formatAmt(i.amount)}</b></span>
            </div>
        </div>`;
    }).join('');

    // Click handler
    container.querySelectorAll('.idx-card').forEach(card => {
        card.addEventListener('click', () => {
            const code = card.dataset.code;
            const name = card.dataset.name;
            container.querySelectorAll('.idx-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            state.selectedIndex = {code, name};
            loadConstituents(code, name);
        });
    });
}

async function loadConstituents(indexCode, name) {
    const panel = $('#constituents-panel');
    const title = $('#constituents-title');
    const body = $('#constituents-body');

    title.textContent = `${name} 成分股`;
    body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;">加载中...</td></tr>';
    panel.classList.remove('hidden');

    try {
        const r = await fetch(`/api/index/${encodeURIComponent(indexCode)}/constituents?top=50`);
        const stocks = await r.json();

        if (!stocks.length) {
            body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--tx3);">暂无成分股数据</td></tr>';
            return;
        }

        body.innerHTML = stocks.map(s => {
            const chgCls = s.change_pct > 0 ? 'st-change-up' : s.change_pct < 0 ? 'st-change-down' : '';
            return `<tr data-code="${s.code}" data-name="${s.name}" data-market="${s.code.startsWith('6')?1:0}">
                <td style="color:var(--tx3)">${s.code}</td>
                <td style="font-weight:500">${s.name}</td>
                <td>${s.price>0?s.price.toFixed(2):'--'}</td>
                <td class="${chgCls}">${colorSign(s.change_pct)}${s.change_pct.toFixed(2)}%</td>
                <td>${formatVol(s.volume)}</td>
                <td>${formatCap(s.market_cap)}</td>
            </tr>`;
        }).join('');

        body.querySelectorAll('tr').forEach(row => {
            row.addEventListener('click', () => {
                const code = row.dataset.code;
                const sName = row.dataset.name;
                const mkt = parseInt(row.dataset.market);
                selectStock(code, mkt, sName);
            });
        });
    } catch(e) {
        body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--red);">加载失败</td></tr>';
        console.error('Constituents error:', e);
    }
}

function updateMarketTime() {
    const now = new Date();
    const day = now.getDay();
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;

    const isWeekday = day >= 1 && day <= 5;
    const morningOpen = 570, morningClose = 690;  // 9:30-11:30
    const afternoonOpen = 780, afternoonClose = 900; // 13:00-15:00

    let status;
    if (!isWeekday) status = '周末休市';
    else if (t < morningOpen) status = '未开盘';
    else if (t >= morningOpen && t < morningClose) status = '🟢 交易中';
    else if (t >= morningClose && t < afternoonOpen) status = '午间休市';
    else if (t >= afternoonOpen && t < afternoonClose) status = '🟢 交易中';
    else status = '已收盘';

    const el = $('#market-time');
    if (el) el.textContent = status;
}

/* ═══════════════════════════════════════════════════════
   Popular Stocks (Sidebar)
   ═══════════════════════════════════════════════════════ */

async function loadPopularStocks() {
    const container = $('#popular-stocks');
    const countEl = $('#popular-stocks-count');
    if (!container) return;

    try {
        const r = await fetch('/api/stocks/popular?top=30');
        const stocks = await r.json();

        if (!stocks.length) {
            container.innerHTML = '<div class="watchlist-empty">暂无数据</div>';
            if (countEl) countEl.textContent = '0';
            return;
        }

        container.innerHTML = stocks.map(s => {
            const chgCls = s.change_pct > 0 ? 'wl-up' : s.change_pct < 0 ? 'wl-down' : '';
            const activeCls = state.currentStock && state.currentStock.code === s.code ? 'active' : '';
            return `<div class="wl-item ${activeCls}" data-code="${s.code}" data-market="${s.code.startsWith('6')?1:0}" data-name="${s.name}" title="${s.name}  换手:${(s.turnover||0).toFixed(1)}% | 成交额:${formatAmt(s.amount)}">
                <span class="wl-code">${s.code}</span>
                <span class="wl-name">${s.name}</span>
                <span class="wl-price ${chgCls}">${s.price>0?s.price.toFixed(2):'--'}</span>
                <span class="wl-change ${chgCls}">${colorSign(s.change_pct)}${s.change_pct.toFixed(2)}%</span>
            </div>`;
        }).join('');

        if (countEl) countEl.textContent = stocks.length;

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
        container.innerHTML = '<div class="watchlist-empty" style="color:var(--red)">加载失败</div>';
        console.error('Popular stocks error:', e);
    }
}

/* ═══════════════════════════════════════════════════════
   Watchlist (Horizontal Strip + localStorage)
   ═══════════════════════════════════════════════════════ */

function loadWatchlist() {
    try {
        state.watchlist = JSON.parse(localStorage.getItem('watchlist') || '[]');
    } catch(e) { state.watchlist = []; }
}

function saveWatchlist() {
    localStorage.setItem('watchlist', JSON.stringify(state.watchlist));
}

function isInWatchlist(code) {
    return state.watchlist.some(w => w.code === code);
}

function addToWatchlist(code, name) {
    if (isInWatchlist(code)) return;
    state.watchlist.push({code, name, addedAt: Date.now()});
    saveWatchlist();
    refreshWatchlistQuotes();
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
        if (container) container.innerHTML = '<span class="wl-strip-empty">暂无自选股</span>';
        if (countEl) countEl.textContent = '0';
        return;
    }

    const codes = state.watchlist.map(w => w.code).join(',');
    let stocks = [];
    try {
        const r = await fetch(`/api/stocks/batch?codes=${encodeURIComponent(codes)}`);
        stocks = await r.json();
    } catch(e) { console.error('Watchlist quotes error:', e); }

    const lookup = {};
    stocks.forEach(s => { lookup[s.code] = s; });

    if (!container) return;
    container.innerHTML = state.watchlist.map(w => {
        const s = lookup[w.code];
        if (!s) return `<div class="wl-strip-mini">
            <span class="wls-code">${w.code}</span>
            <span class="wls-name">${w.name}</span>
            <span class="wls-remove" data-code="${w.code}">✕</span></div>`;

        const cls = s.change_pct >= 0 ? 'wl-up' : s.change_pct < 0 ? 'wl-down' : '';
        const activeCls = state.currentStock && state.currentStock.code === w.code ? 'active' : '';
        return `<div class="wl-strip-mini ${activeCls}" data-code="${w.code}" data-market="${w.code.startsWith('6')?1:0}" data-name="${s.name}">
            <span class="wls-code">${w.code}</span>
            <span class="wls-name">${s.name}</span>
            <span class="wls-price ${cls}">${s.price>0?s.price.toFixed(2):'--'}</span>
            <span class="wls-change ${cls}">${colorSign(s.change_pct)}${s.change_pct.toFixed(2)}%</span>
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

    // Load chart based on period
    if (period === 'intraday') {
        await loadIntradayChart(code, market, name);
    } else {
        await loadKlineChart(code, market, period);
    }
    await loadStockInfo(code, market);

    if (header) header.classList.remove('hidden');
    if (tabs) tabs.classList.remove('hidden');
    if (wrap) wrap.classList.remove('hidden');
    $('#stock-loading').classList.add('hidden');
}

async function loadStockInfo(code, market) {
    try {
        const r = await fetch(`/api/stock/${code}/info`);
        const info = await r.json();
        if (info.error) return;

        $('#sdh-name').textContent = info.name || '--';
        $('#sdh-code').textContent = `${market===1?'SH':'SZ'}${code}`;
        const price = info.price || 0;
        const chgPct = info.change_pct || 0;

        $('#sdh-price').textContent = price > 0 ? price.toFixed(2) : '--';
        $('#sdh-price').className = 'sdh-price ' + (chgPct > 0 ? 'sdh-up' : chgPct < 0 ? 'sdh-down' : '');
        $('#sdh-change').textContent = `${colorSign(chgPct)}${info.change_amt?.toFixed(2)||'--'}  ${colorSign(chgPct)}${chgPct.toFixed(2)}%`;
        $('#sdh-change').className = 'sdh-change ' + (chgPct > 0 ? 'sdh-up' : chgPct < 0 ? 'sdh-down' : '');

        $('#sdh-open').textContent = info.open ? info.open.toFixed(2) : '--';
        $('#sdh-high').textContent = info.high ? info.high.toFixed(2) : '--';
        $('#sdh-low').textContent = info.low ? info.low.toFixed(2) : '--';
        $('#sdh-preclose').textContent = info.preclose ? info.preclose.toFixed(2) : '--';
        $('#sdh-volume').textContent = formatVol(info.volume);
        $('#sdh-amount').textContent = formatAmt(info.amount);
        $('#sdh-turnover').textContent = info.turnover_rate ? info.turnover_rate.toFixed(2)+'%' : '--';
        $('#sdh-amplitude').textContent = info.amplitude ? info.amplitude.toFixed(2)+'%' : '--';
    } catch(e) { console.error('Info error:', e); }
}

/* ═══════════════════════════════════════════════════════
   Charts (ECharts)
   ═══════════════════════════════════════════════════════ */

const UP_COLOR = '#f85149', DOWN_COLOR = '#3fb950';

function initChart(domId) {
    const dom = $(`#${domId}`);
    if (!dom) return null;
    let instance = state.chartInstances[domId];
    if (!instance || instance.isDisposed()) {
        instance = echarts.init(dom, null, {backgroundColor: chartBg()});
        state.chartInstances[domId] = instance;
    }
    return instance;
}

function disposeAllCharts() {
    Object.values(state.chartInstances).forEach(c => { try { c.dispose(); } catch(e) {} });
    state.chartInstances = {};
}

async function loadIntradayChart(code, market, name) {
    // Dispose old instance to ensure no K-line residue
    const oldChart = state.chartInstances['chart-main'];
    if (oldChart && !oldChart.isDisposed()) oldChart.dispose();
    delete state.chartInstances['chart-main'];

    // Clear indicator sub-charts as well
    ['chart-indicator', 'chart-indicator2'].forEach(id => {
        const c = state.chartInstances[id];
        if (c && !c.isDisposed()) c.dispose();
        delete state.chartInstances[id];
    });

    const chart = initChart('chart-main');
    if (!chart) return;
    chart.showLoading();

    try {
        const r = await fetch(`/api/stock/${code}/intraday`);
        const data = await r.json();
        chart.hideLoading();

        if (!data.minutes || !data.minutes.length) {
            chart.setOption({
                title: {text: '暂无分时数据', subtext: '当日非交易时段或无数据源', left: 'center', top: 'center',
                    textStyle: {color: chartText(), fontSize: 14},
                    subtextStyle: {color: '#8b949e', fontSize: 11}},
            }, true);
            return;
        }

        const times = data.minutes.map(d => d.time);
        const prices = data.minutes.map(d => d.price);
        const avgs = data.minutes.map(d => d.avg_price);
        const volumes = data.minutes.map(d => d.volume);
        const preclose = data.preclose || prices[0] || 0;
        const tradeDate = data.trade_date || '';
        const isToday = data.is_today !== false;

        const allPrices = [...prices, preclose, ...avgs.filter(v=>v>0)];
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
                {name:'昨收',type:'line',xAxisIndex:0,yAxisIndex:0,data:Array(times.length).fill(preclose),
                    smooth:false,symbol:'none',lineStyle:{width:0.5,color:chartText(),type:'dotted'}},
                {name:'量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:volumes.map(v=>({value:v,
                    itemStyle:{color:v>0?'rgba(248,81,73,0.4)':'rgba(63,185,80,0.4)'}}))},
            ],
        }, true);
    } catch(e) {
        chart.hideLoading();
        chart.setOption({
            title: {text: '分时数据加载失败', subtext: '请稍后重试', left: 'center', top: 'center',
                textStyle: {color: '#f85149', fontSize: 14},
                subtextStyle: {color: '#8b949e', fontSize: 11}},
        }, true);
        console.error('Intraday error:', e);
    }
}

async function loadKlineChart(code, market, period) {
    // Dispose old instance to ensure no intraday residue
    const oldChart = state.chartInstances['chart-main'];
    if (oldChart && !oldChart.isDisposed()) oldChart.dispose();
    delete state.chartInstances['chart-main'];
    // Clear indicator sub-charts
    ['chart-indicator', 'chart-indicator2'].forEach(id => {
        const c = state.chartInstances[id];
        if (c && !c.isDisposed()) c.dispose();
        delete state.chartInstances[id];
    });

    const mainChart = initChart('chart-main');
    if (!mainChart) return;
    mainChart.showLoading();

    const indDom = $('#chart-indicator');
    const ind2Dom = $('#chart-indicator2');
    if (indDom) indDom.classList.toggle('show', state.activeIndicators.has('macd') || state.activeIndicators.has('rsi'));
    if (ind2Dom) ind2Dom.classList.toggle('show', state.activeIndicators.has('kdj'));

    try {
        const r = await fetch(`/api/stock/${code}/chart?period=${period}`);
        const result = await r.json();
        mainChart.hideLoading();
        if (!result.data || !result.data.length) {
            mainChart.setOption({title:{text:'暂无数据',left:'center',top:'center',textStyle:{color:chartText()}}}, true);
            return;
        }

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
            name:'VOL',type:'bar',xAxisIndex:1,yAxisIndex:1,
            data: volumes.map((v, i) => ({value: v,
                itemStyle: {color: ohlc[i][1] >= ohlc[i][0] ? 'rgba(248,81,73,0.5)' : 'rgba(63,185,80,0.5)'}})),
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
            series: [...mainSeries, volSeries],
        }, true);

        // ── Manual wheel handler (more precise speed control) ──
        const dom = mainChart.getDom();
        if (dom._wheelHandler) dom.removeEventListener('wheel', dom._wheelHandler);
        dom._wheelHandler = (e) => {
            const dzOpt = mainChart.getOption().dataZoom[0];
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
            const c = initChart('chart-indicator');
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
            const c = initChart('chart-indicator');
            if (c) c.clear();
        }
        if (!state.activeIndicators.has('kdj')) {
            const c = initChart('chart-indicator2');
            if (c) c.clear();
        }

        mainChart.resize();
    } catch(e) {
        mainChart.hideLoading();
        console.error('Kline error:', e);
    }
}

/* ═══════════════════════════════════════════════════════
   Hot News & Topics
   ═══════════════════════════════════════════════════════ */

async function loadHotNews() {
    try {
        const r = await fetch('/api/news/hot');
        const data = await r.json();

        // Render hot topics
        const topicsContainer = $('#hot-topics');
        if (data.hot_topics && topicsContainer) {
            topicsContainer.innerHTML = data.hot_topics.map(t =>
                `<span class="ht-tag" data-topic="${t}">${t}</span>`
            ).join('');

            topicsContainer.querySelectorAll('.ht-tag').forEach(tag => {
                tag.addEventListener('click', () => {
                    const topic = tag.dataset.topic;
                    // Switch to discover view and search
                    switchToDiscover(topic);
                });
            });
        }

        // Render news
        const newsContainer = $('#hot-news');
        if (!newsContainer) return;
        const allNews = [...(data.em_news || []), ...(data.web_news || [])];
        if (allNews.length) {
            newsContainer.innerHTML = allNews.slice(0, 12).map(n =>
                `<div class="news-card">
                    <a href="${n.url||'#'}" target="_blank" class="nc-title">${n.title||'无标题'}</a>
                    <div class="nc-summary">${(n.summary||'').substring(0,100)}</div>
                    <div class="nc-meta">
                        <span>${n.source||''}</span><span>${n.date||''}</span>
                    </div>
                </div>`
            ).join('');
        } else {
            newsContainer.innerHTML = '<div style="color:var(--tx3);text-align:center;padding:20px;">暂无热门资讯</div>';
        }
    } catch(e) { console.error('Hot news error:', e); }
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
    } else if (!state.discoverLoaded) {
        loadDiscoverDefault();
    }
}

/* ═══════════════════════════════════════════════════════
   Discover / Topic Matching
   ═══════════════════════════════════════════════════════ */

async function loadDiscoverDefault() {
    const defaultContainer = $('#discover-default');
    if (!defaultContainer) return;

    try {
        // Load hot topics
        const newsR = await fetch('/api/news/hot');
        const newsData = await newsR.json();

        const topicsContainer = $('#discover-hot-topics');
        if (newsData.hot_topics && topicsContainer) {
            topicsContainer.innerHTML = newsData.hot_topics.map(t =>
                `<span class="ht-tag" data-topic="${t}">${t}</span>`
            ).join('');

            topicsContainer.querySelectorAll('.ht-tag').forEach(tag => {
                tag.addEventListener('click', () => {
                    const topic = tag.dataset.topic;
                    $('#topic-search-input').value = topic;
                    searchTopic();
                });
            });
        }

        // Load hot stocks
        const stocksR = await fetch('/api/stocks/popular?top=10');
        const stocks = await stocksR.json();
        const stocksBody = $('#discover-hot-stocks-body');
        if (stocks.length && stocksBody) {
            stocksBody.innerHTML = stocks.map(s => {
                const chgCls = s.change_pct > 0 ? 'st-change-up' : s.change_pct < 0 ? 'st-change-down' : '';
                return `<tr data-code="${s.code}" data-name="${s.name}" data-market="${s.code.startsWith('6')?1:0}">
                    <td style="color:var(--tx3)">${s.code}</td>
                    <td style="font-weight:500">${s.name}</td>
                    <td>${s.price>0?s.price.toFixed(2):'--'}</td>
                    <td class="${chgCls}">${colorSign(s.change_pct)}${s.change_pct.toFixed(2)}%</td>
                    <td>${formatVol(s.volume)}</td>
                </tr>`;
            }).join('');

            stocksBody.querySelectorAll('tr').forEach(row => {
                row.addEventListener('click', () => {
                    selectStock(row.dataset.code, parseInt(row.dataset.market), row.dataset.name);
                });
            });
        }

        defaultContainer.classList.remove('hidden');
        state.discoverLoaded = true;
    } catch(e) { console.error('Discover default error:', e); }
}

function showDiscoverDefault() {
    const dd = $('#discover-default');
    if (dd) dd.classList.remove('hidden');
    $('#topic-empty').classList.add('hidden');
    $('#topic-loading').classList.add('hidden');
    $('#topic-concepts').classList.add('hidden');
    $('#topic-stocks-section').classList.add('hidden');
    $('#topic-news-section').classList.add('hidden');
}

async function searchTopic() {
    const query = $('#topic-search-input').value.trim();
    if (!query) {
        showDiscoverDefault();
        return;
    }

    // Hide default, show loading
    const dd = $('#discover-default');
    if (dd) dd.classList.add('hidden');
    $('#topic-empty').classList.add('hidden');
    $('#topic-loading').classList.remove('hidden');
    $('#topic-concepts').classList.add('hidden');
    $('#topic-stocks-section').classList.add('hidden');
    $('#topic-news-section').classList.add('hidden');

    try {
        const r = await fetch(`/api/match?query=${encodeURIComponent(query)}`);
        const data = await r.json();
        $('#topic-loading').classList.add('hidden');

        let hasResults = false;

        if (data.concepts && data.concepts.length) {
            $('#topic-concepts').innerHTML = data.concepts.map(c =>
                `<span class="concept-tag">${c.name} <small>${(c.score*100).toFixed(0)}%</small></span>`).join('');
            $('#topic-concepts').classList.remove('hidden');
            hasResults = true;
        }

        if (data.stocks && data.stocks.length) {
            $('#topic-stocks-count').textContent = `(共${data.total_related}只)`;
            $('#topic-stocks-body').innerHTML = data.stocks.map(s => {
                const chgCls = s.change_pct > 0 ? 'st-change-up' : s.change_pct < 0 ? 'st-change-down' : '';
                const relPct = Math.min(100, Math.round((s.relevance||0)*100));
                return `<tr data-code="${s.code}" data-name="${s.name}" data-market="${s.code.startsWith('6')?1:0}">
                    <td style="color:var(--tx3)">${s.code}</td>
                    <td style="font-weight:500">${s.name}</td>
                    <td>${s.price>0?s.price.toFixed(2):'--'}</td>
                    <td class="${chgCls}">${colorSign(s.change_pct)}${(s.change_pct||0).toFixed(2)}%</td>
                    <td style="color:var(--purple);font-size:.8rem">${s.matched_via||'--'}</td>
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
        if (allNews.length) {
            $('#topic-news-list').innerHTML = allNews.map(n =>
                `<div class="news-item">
                    <a href="${n.url||'#'}" target="_blank" class="news-title">${n.title||''}</a>
                    <div class="news-summary">${n.summary||''}</div>
                    <div class="news-meta"><span>${n.source||''}</span><span>${n.date||''}</span></div>
                </div>`).join('');
            $('#topic-news-section').classList.remove('hidden');
            hasResults = true;
        }

        if (!hasResults) {
            $('#topic-empty').classList.remove('hidden');
        }
    } catch(e) {
        $('#topic-loading').classList.add('hidden');
        console.error('Topic error:', e);
    }
}

/* ═══════════════════════════════════════════════════════
   Navigation & UI Events
   ═══════════════════════════════════════════════════════ */

function initNavigation() {
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
            if (viewId === 'discover' && !state.discoverLoaded) {
                loadDiscoverDefault();
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

            const indEl = $('#chart-indicator');
            const ind2El = $('#chart-indicator2');
            if (indEl) indEl.classList.toggle('show', state.activeIndicators.has('macd') || state.activeIndicators.has('rsi'));
            if (ind2El) ind2El.classList.toggle('show', state.activeIndicators.has('kdj'));
        });
    }

    // Close constituents
    const closeBtn = $('#btn-close-constituents');
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            $('#constituents-panel').classList.add('hidden');
            $$('.idx-card').forEach(c => c.classList.remove('selected'));
            state.selectedIndex = null;
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
        refreshBtn.addEventListener('click', () => {
            loadIndexQuotes();
            refreshWatchlistQuotes();
            loadPopularStocks();
            loadHotNews();
            if (state.currentStock) {
                if (state.currentPeriod === 'intraday') {
                    loadIntradayChart(state.currentStock.code, state.currentStock.market, state.currentStock.name);
                } else {
                    loadKlineChart(state.currentStock.code, state.currentStock.market, state.currentPeriod);
                }
                loadStockInfo(state.currentStock.code, state.currentStock.market);
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
                    const r = await fetch(`/api/stock/search?keyword=${encodeURIComponent(kw)}`);
                    const data = await r.json();
                    if (!data.length) { sdSug.classList.remove('show'); return; }
                    sdSug.innerHTML = data.map(s => `<div class="suggestion-item" data-code="${s.code}" data-market="${s.market}" data-name="${s.name}">
                        <span>${s.name}</span><span style="color:var(--tx3)">${s.full_code}</span></div>`).join('');
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
                    const r = await fetch(`/api/stock/search?keyword=${encodeURIComponent(kw)}`);
                    const data = await r.json();
                    if (!data.length) { wlSug.classList.remove('show'); return; }
                    wlSug.innerHTML = data.map(s => `<div class="ws-item" data-code="${s.code}" data-name="${s.name}">
                        <span>${s.name}</span><span class="ws-code">${s.code}</span></div>`).join('');
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

async function autoSelectStock(keyword, inputEl) {
    try {
        const r = await fetch(`/api/stock/search?keyword=${encodeURIComponent(keyword)}`);
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
    // Refresh index quotes + watchlist + popular stocks every 5 seconds during trading hours
    setInterval(() => {
        const now = new Date();
        const day = now.getDay();
        const h = now.getHours(), m = now.getMinutes();
        const t = h * 60 + m;
        if (day >= 1 && day <= 5 && ((t >= 570 && t < 690) || (t >= 780 && t < 900))) {
            loadIndexQuotes();
            refreshWatchlistQuotes();
            loadPopularStocks();
            if (state.currentStock) loadStockInfo(state.currentStock.code, state.currentStock.market);
        }
    }, 5000);

    // Refresh hot news every 5 minutes
    setInterval(loadHotNews, 300000);
}

/* ═══════════════════════════════════════════════════════
   Init
   ═══════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', async () => {
    loadTheme();
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

    console.log('📈 股探 v3 已就绪 — 深浅色主题 · 大盘 · 自选 · 人气股 · K线 · 指标 · 掘金');
});
