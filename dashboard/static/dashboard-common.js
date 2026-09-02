function fmtPnl(v) {
  const s = v >= 0 ? '+' : '-';
  return s + '$' + Math.abs(v).toFixed(2);
}

function fmtEthReason(reason) {
  if (!reason) return '';
  return String(reason).replace(/btc_move/g, 'eth_move');
}

function logRowKind(t) {
  const result = String(t.trade_result || '').toUpperCase();
  const reason = String(t.strategy_reason || '').toLowerCase();
  const skipCat = t.skip_category || '';
  let skipType = 'strategy';
  if (skipCat === 'outside_trading_period' || reason.includes('outside strategy trading period')) {
    skipType = 'outside_session';
  } else if (skipCat === 'timing_filter' || reason.includes('timing/filter')) {
    skipType = 'timing';
  } else if (reason.includes('mid tier disabled')) {
    skipType = 'mid_disabled';
  } else if (skipCat === 'no_ask' || reason.includes('no ask') || reason.includes('no_checkpoint')) {
    skipType = 'no_ask';
  }

  let status = 'skipped';
  if (t.entered) {
    if (result === 'WIN') status = 'win';
    else if (result === 'LOSS') status = 'loss';
    else if (result === 'NO_FILL') status = 'no_fill';
    else if (result === 'FAILED') status = 'failed';
    else status = 'pending';
  } else if (result === 'NO_FILL') {
    status = 'no_fill';
  } else if (result === 'FAILED') {
    status = 'failed';
  }

  const tier = String(t.tier || (t.entered ? 'unknown' : 'skip')).toLowerCase();
  return { status, skipType, tier };
}

function matchesLogFilter(t, filters) {
  const kind = logRowKind(t);
  const status = filters.status || 'all';
  const skip = filters.skip || 'all';
  const tier = filters.tier || 'all';
  const q = (filters.q || '').trim().toLowerCase();

  if (status === 'entered' && !t.entered) return false;
  if (status === 'skipped' && t.entered) return false;
  if (status !== 'all' && status !== 'entered' && status !== 'skipped' && kind.status !== status) return false;

  if (skip !== 'all' && kind.skipType !== skip) return false;
  if (tier !== 'all' && kind.tier !== tier) return false;

  const day = filters.date || 'all';
  if (day !== 'all' && logTimestamp(t).slice(0, 10) !== day) return false;

  if (q) {
    const hay = `${t.market || ''} ${t.strategy_reason || ''} ${t.slug || ''} ${t.bought_label || ''}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function createLogFilterController(cfg) {
  const defaults = { status: 'all', skip: 'all', tier: 'all', date: 'all', q: '', sortKey: 'date', sortDir: 'desc', groupByDate: true };
  let rows = [];
  let filters = { ...defaults };

  function collectLogDates(data) {
    const map = {};
    (data || []).forEach(r => {
      const d = logTimestamp(r).slice(0, 10);
      if (!d) return;
      if (!map[d]) map[d] = { date: d, count: 0, entered: 0, closed: 0, wins: 0, losses: 0, pnl: 0, skipped: 0 };
      const bucket = map[d];
      bucket.count += 1;
      if (r.entered) bucket.entered += 1;
      else bucket.skipped += 1;
      if (r.trade_result === 'WIN') {
        bucket.closed += 1;
        bucket.wins += 1;
        bucket.pnl += parseFloat(r.pnl) || 0;
      } else if (r.trade_result === 'LOSS') {
        bucket.closed += 1;
        bucket.losses += 1;
        bucket.pnl += parseFloat(r.pnl) || 0;
      }
    });
    return Object.values(map).sort((a, b) => b.date.localeCompare(a.date));
  }

  function updateDatePills() {
    const el = document.getElementById(cfg.datePillsId);
    if (!el) return;
    const dates = collectLogDates(rows);
    const today = todayHktStr();
    let html = `<button type="button" class="filter-pill date-pill${filters.date === 'all' ? ' active' : ''}" data-filter="date" data-value="all">全部日期 <span style="color:var(--muted);font-weight:400">(${rows.length})</span></button>`;
    dates.forEach(d => {
      const active = filters.date === d.date;
      const pnlClass = d.pnl >= 0 ? 'pos' : 'neg';
      const todayTag = d.date === today ? ' · 今日' : '';
      html += `<button type="button" class="filter-pill date-pill${active ? ' active' : ''}" data-filter="date" data-value="${d.date}" title="${d.date}">`
        + `${d.date.slice(5)}${todayTag} · <span class="pill-pnl ${pnlClass}">${fmtPnl(d.pnl)}</span>`
        + `<span style="color:var(--muted);font-weight:400"> · ${d.count}筆/${d.entered}成</span></button>`;
    });
    el.innerHTML = html;
  }

  function updateDaySummary(filtered) {
    const el = document.getElementById(cfg.daySummaryId);
    if (!el) return;
    if (filters.date === 'all') {
      el.classList.remove('visible');
      el.innerHTML = '';
      return;
    }
    const s = daySummary(rows, filters.date);
    const dayRows = rows.filter(r => logTimestamp(r).slice(0, 10) === filters.date);
    const closed = dayRows.filter(r => r.trade_result === 'WIN' || r.trade_result === 'LOSS');
    const wins = closed.filter(r => r.trade_result === 'WIN').length;
    const losses = closed.length - wins;
    const wr = closed.length ? Math.round(wins / closed.length * 100) : null;
    const fav = closed.filter(r => r.tier === 'favorite');
    const dog = closed.filter(r => r.tier === 'underdog');
    const favPnl = fav.reduce((n, r) => n + (parseFloat(r.pnl) || 0), 0);
    const dogPnl = dog.reduce((n, r) => n + (parseFloat(r.pnl) || 0), 0);
    const label = filters.date === todayHktStr() ? `${filters.date}（今日 HKT）` : filters.date;
    const pnlClass = s.pnl >= 0 ? 'pos' : 'neg';
    el.innerHTML = `
      <div class="ds-item"><span class="ds-label">已選日期</span><span class="ds-value">${label}</span></div>
      <div class="ds-item"><span class="ds-label">窗口 / 成交</span><span class="ds-value">${s.count} / ${s.entered}</span></div>
      <div class="ds-item"><span class="ds-label">已結算</span><span class="ds-value">${closed.length} 筆</span></div>
      <div class="ds-item"><span class="ds-label">勝率</span><span class="ds-value">${wr != null ? wr + '% (' + wins + 'W/' + losses + 'L)' : '—'}</span></div>
      <div class="ds-item"><span class="ds-label">日 PnL</span><span class="ds-value ${pnlClass}">${fmtPnl(s.pnl)}</span></div>
      <div class="ds-item"><span class="ds-label">Fav / Dog PnL</span><span class="ds-value">${fmtPnl(favPnl)} / ${fmtPnl(dogPnl)}</span></div>
      <div class="ds-item"><span class="ds-label">目前篩選</span><span class="ds-value">${filtered.length} 筆顯示中</span></div>
    `;
    el.classList.add('visible');
  }

  function loadSaved() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(cfg.storageKey) || '{}');
      filters = { ...defaults, ...saved };
      if (saved.groupByDate === undefined) filters.groupByDate = true;
    } catch (_) {
      filters = { ...defaults };
    }
  }

  function saveFilters() {
    try {
      sessionStorage.setItem(cfg.storageKey, JSON.stringify(filters));
    } catch (_) { /* ignore */ }
  }

  function syncUi() {
    const root = document.getElementById(cfg.rootId);
    if (!root) return;
    root.querySelectorAll('.filter-pill[data-filter]').forEach(btn => {
      const key = btn.dataset.filter;
      btn.classList.toggle('active', (filters[key] || 'all') === btn.dataset.value);
    });
    const search = document.getElementById(cfg.searchId);
    if (search && search.value !== filters.q) search.value = filters.q || '';

    const sortRoot = document.getElementById(cfg.sortRootId);
    if (sortRoot) {
      sortRoot.querySelectorAll('.sort-pill[data-sort-key]').forEach(btn => {
        const on = btn.dataset.sortKey === filters.sortKey && btn.dataset.sortDir === filters.sortDir;
        btn.classList.toggle('active', on);
      });
    }
    const groupCb = document.getElementById(cfg.groupDateId);
    if (groupCb) groupCb.checked = !!filters.groupByDate;

    updateDatePills();

    const table = document.getElementById(cfg.tableId);
    if (table) {
      table.querySelectorAll('thead th.th-sortable').forEach(th => {
        const key = th.dataset.sortKey;
        const active = key === filters.sortKey;
        th.classList.toggle('active-sort', active);
        const arrow = th.querySelector('.sort-arrow');
        if (arrow) {
          arrow.textContent = active ? (filters.sortDir === 'asc' ? '▲' : '▼') : '↕';
        }
      });
    }
  }

  function setSort(sortKey, sortDir) {
    filters.sortKey = sortKey;
    filters.sortDir = sortDir;
    saveFilters();
    syncUi();
    render();
  }

  function toggleGroupByDate(checked) {
    filters.groupByDate = checked;
    saveFilters();
    syncUi();
    render();
  }

  function render() {
    const filtered = rows.filter(r => matchesLogFilter(r, filters));
    const emptyMsg = rows.length === 0 ? (cfg.noDataMsg || cfg.emptyMsg) : cfg.emptyMsg;
    const groupByDate = filters.groupByDate && filters.date === 'all';
    renderLogTable(document.getElementById(cfg.bodyId), filtered, emptyMsg, cfg.ethTable, {
      sortKey: filters.sortKey,
      sortDir: filters.sortDir,
      groupByDate,
    });
    updateDaySummary(filtered);
    const countEl = document.getElementById(cfg.countId);
    if (countEl) {
      const dateNote = filters.date !== 'all' ? ` · ${filters.date}` : '';
      countEl.textContent = filtered.length === rows.length
        ? `${rows.length} 筆${dateNote}`
        : `顯示 ${filtered.length} / ${rows.length} 筆${dateNote}`;
    }
  }

  function setRows(nextRows) {
    rows = nextRows || [];
    updateDatePills();
    if (filters.date !== 'all' && !rows.some(r => logTimestamp(r).slice(0, 10) === filters.date)) {
      filters.date = 'all';
      saveFilters();
    }
    render();
  }

  function setFilter(key, value) {
    filters[key] = value;
    if (key === 'date' && value !== 'all') {
      filters.groupByDate = false;
    }
    saveFilters();
    syncUi();
    render();
  }

  function reset() {
    filters = { ...defaults };
    saveFilters();
    syncUi();
    updateDatePills();
    render();
  }

  function bind() {
    const wrap = document.getElementById(cfg.wrapId);
    if (wrap) {
      wrap.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.filter-pill[data-filter]');
        if (!btn) return;
        setFilter(btn.dataset.filter, btn.dataset.value);
      });
    }
    const root = document.getElementById(cfg.rootId);
    const search = document.getElementById(cfg.searchId);
    if (search) {
      let timer;
      search.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => setFilter('q', search.value), 180);
      });
    }
    const resetBtn = document.getElementById(cfg.resetId);
    if (resetBtn) resetBtn.addEventListener('click', reset);

    const sortRoot = document.getElementById(cfg.sortRootId);
    if (sortRoot) {
      sortRoot.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.sort-pill[data-sort-key]');
        if (!btn) return;
        setSort(btn.dataset.sortKey, btn.dataset.sortDir);
      });
    }
    const groupCb = document.getElementById(cfg.groupDateId);
    if (groupCb) {
      groupCb.addEventListener('change', () => toggleGroupByDate(groupCb.checked));
    }

    const table = document.getElementById(cfg.tableId);
    if (table) {
      table.querySelector('thead')?.addEventListener('click', (ev) => {
        const th = ev.target.closest('th.th-sortable');
        if (!th) return;
        const key = th.dataset.sortKey;
        const nextDir = (filters.sortKey === key && filters.sortDir === 'desc') ? 'asc' : 'desc';
        setSort(key, nextDir);
      });
    }
  }

  loadSaved();
  bind();
  syncUi();
  updateDatePills();
  return { setRows, reset, getFilters: () => ({ ...filters }) };
}

const btcLogFilters = createLogFilterController({
  wrapId: 'btc-log-filters-wrap',
  rootId: 'btc-log-filters',
  datePillsId: 'btc-date-pills',
  daySummaryId: 'btc-day-summary',
  bodyId: 'log-body',
  tableId: 'btc-log-table',
  sortRootId: 'btc-log-sort',
  groupDateId: 'btc-group-date',
  countId: 'btc-log-filter-count',
  searchId: 'btc-log-search',
  resetId: 'btc-log-reset',
  storageKey: 'btc-log-filters-v3',
  emptyMsg: '沒有符合篩選條件的紀錄',
  noDataMsg: '尚無本 session 紀錄（等待下一個 5 分鐘窗口）',
  ethTable: false,
});

const ethLogFilters = createLogFilterController({
  wrapId: 'eth-log-filters-wrap',
  rootId: 'eth-log-filters',
  datePillsId: 'eth-date-pills',
  daySummaryId: 'eth-day-summary',
  bodyId: 'eth-log-body',
  tableId: 'eth-log-table',
  sortRootId: 'eth-log-sort',
  groupDateId: 'eth-group-date',
  countId: 'eth-log-filter-count',
  searchId: 'eth-log-search',
  resetId: 'eth-log-reset',
  storageKey: 'eth-log-filters-v3',
  emptyMsg: '沒有符合篩選條件的 ETH 紀錄',
  noDataMsg: 'ETH paper 尚無紀錄',
  ethTable: true,
});

function logTimestamp(t) {
  return t.timestamp_hkt || t.timestamp || '';
}

function todayHktStr() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Hong_Kong' });
}

function formatDateCell(dateStr) {
  if (!dateStr || dateStr.length < 10) return '—';
  const mmdd = dateStr.slice(5);
  if (dateStr === todayHktStr()) {
    return `<span class="date-today">今日</span><br><span class="date-wd">${mmdd}</span>`;
  }
  const d = new Date(dateStr + 'T12:00:00');
  const wd = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
  return `${mmdd}<br><span class="date-wd">週${wd}</span>`;
}

function formatDateGroupLabel(dateStr, summary) {
  if (!dateStr) return '—';
  const label = dateStr === todayHktStr() ? `${dateStr}（今日 HKT）` : dateStr;
  const pnlClass = summary.pnl >= 0 ? 'pos' : 'neg';
  const pnlPart = summary.closed
    ? ` · 已結算 <span class="day-pnl ${pnlClass}">${fmtPnl(summary.pnl)}</span>`
    : '';
  return `📅 ${label} · ${summary.count} 筆 · 成交 ${summary.entered}${pnlPart}`;
}

function daySummary(rows, dateStr) {
  const day = rows.filter(r => logTimestamp(r).slice(0, 10) === dateStr);
  const closed = day.filter(r => r.trade_result === 'WIN' || r.trade_result === 'LOSS');
  const pnl = closed.reduce((s, r) => s + (parseFloat(r.pnl) || 0), 0);
  return {
    count: day.length,
    entered: day.filter(r => r.entered).length,
    closed: closed.length,
    pnl,
  };
}

function applyLogSort(rows, sortKey, sortDir, groupByDate) {
  const mul = sortDir === 'asc' ? 1 : -1;
  const list = [...(rows || [])];
  const cmp = (a, b) => {
    if (sortKey === 'date') {
      return mul * logTimestamp(a).localeCompare(logTimestamp(b));
    }
    if (sortKey === 'pnl') {
      return mul * ((parseFloat(a.pnl) || 0) - (parseFloat(b.pnl) || 0));
    }
    if (sortKey === 'ask') {
      return mul * ((parseFloat(a.entry_ask) || 0) - (parseFloat(b.entry_ask) || 0));
    }
    if (sortKey === 'result') {
      return mul * String(a.trade_result || '').localeCompare(String(b.trade_result || ''));
    }
    return 0;
  };
  if (groupByDate && sortKey === 'date') {
    const byDate = {};
    list.forEach(r => {
      const d = logTimestamp(r).slice(0, 10) || 'unknown';
      (byDate[d] = byDate[d] || []).push(r);
    });
    const dates = Object.keys(byDate).sort((a, b) => mul * a.localeCompare(b));
    return dates.flatMap(d => byDate[d].sort((a, b) => mul * logTimestamp(a).localeCompare(logTimestamp(b))));
  }
  return list.sort(cmp);
}

function renderLogRow(t, ethTable) {
  const result = t.trade_result || '—';
  const pnl = result === 'WIN' || result === 'LOSS'
    ? `<span class="${t.pnl >= 0 ? 'pos' : 'neg'}">${fmtPnl(t.pnl || 0)}</span>`
    : '—';
  const stake = t.entered ? `$${(t.stake || 0).toFixed(2)}` : '<span style="color:#8b949e">$0.00</span>';
  const ts = logTimestamp(t);
  const dateStr = ts.slice(0, 10);
  const timeStr = ts.length >= 19 ? ts.slice(11, 19) : '—';
  return `<tr class="${t.entered ? '' : 'row-skipped'}">
    <td class="col-date">${formatDateCell(dateStr)}</td>
    <td class="col-time">${timeStr}</td>
    <td title="${(t.market || '').replace(/"/g, '&quot;')}">${(t.market || '').slice(0, 28)}${(t.market || '').length > 28 ? '…' : ''}</td>
    <td>${t.bought_label || '—'}</td>
    <td>${t.entry_ask != null ? Number(t.entry_ask).toFixed(2) : '—'}</td>
    <td>${stake}</td>
    <td>${t.multiplier || t.tier || '—'}</td>
    <td>${t.resolved_label || 'Pending'}</td>
    <td class="result-${result}">${result}</td>
    <td>${pnl}</td>
    <td style="max-width:240px;font-size:0.72rem;color:#8b949e">${ethTable ? fmtEthReason(t.strategy_reason) : (t.strategy_reason || '')}</td>
  </tr>`;
}

function renderLogTable(tb, rows, emptyMsg, ethTable, sortOpts) {
  const opts = sortOpts || { sortKey: 'date', sortDir: 'desc', groupByDate: true };
  const sorted = applyLogSort(rows, opts.sortKey, opts.sortDir, opts.groupByDate);
  if (!sorted.length) {
    tb.innerHTML = `<tr><td colspan="11" style="color:#8b949e;text-align:center;padding:2rem">${emptyMsg || '尚無紀錄'}</td></tr>`;
    return;
  }
  const parts = [];
  let lastDate = null;
  sorted.forEach(t => {
    const dateStr = logTimestamp(t).slice(0, 10);
    if (opts.groupByDate && dateStr && dateStr !== lastDate) {
      parts.push(`<tr class="date-group-row"><td colspan="11">${formatDateGroupLabel(dateStr, daySummary(sorted, dateStr))}</td></tr>`);
      lastDate = dateStr;
    }
    parts.push(renderLogRow(t, ethTable));
  });
  tb.innerHTML = parts.join('');
}
