/** Tab navigation + lazy per-bot refresh (avoids loading all 3 bots every poll). */

(function () {
  const LOG_LIMIT = 150;
  const POLL_MS = 15000;
  const TAB_KEY = 'dashboard-active-tab';
  const VALID_TABS = new Set(['overview', 'competition', 'btc', 'eth', 'sports', 'timetable', 'fee']);

  function todayHkt() {
    return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Hong_Kong' });
  }

  function getActiveTab() {
    try {
      const tab = sessionStorage.getItem(TAB_KEY) || 'competition';
      return VALID_TABS.has(tab) ? tab : 'competition';
    } catch (_) {
      return 'competition';
    }
  }

  function setActiveTab(tab) {
    if (!VALID_TABS.has(tab)) tab = 'competition';
    try {
      sessionStorage.setItem(TAB_KEY, tab);
    } catch (_) { /* ignore */ }
    document.querySelectorAll('.bot-tab').forEach((btn) => {
      const on = btn.dataset.tab === tab;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.bot-panel').forEach((panel) => {
      panel.classList.toggle('active', panel.id === `panel-${tab}`);
    });
    window.__dashActiveTab = tab;
    window.dispatchEvent(new CustomEvent('dashboard:tab', { detail: { tab } }));
  }

  function bindTabs() {
    document.querySelectorAll('.bot-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        if (!tab) return;
        setActiveTab(tab);
        if (typeof window.refreshActiveBot === 'function') {
          window.refreshActiveBot(true);
        }
      });
    });
    setActiveTab(getActiveTab());
  }

  function bindQuickFilters(prefix, controller) {
    const bar = document.getElementById(`${prefix}-quick-filters`);
    if (!bar || !controller) return;
    bar.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-quick]');
      if (!btn) return;
      const q = btn.dataset.quick;
      if (q === 'all') {
        controller.reset();
        return;
      }
      if (q === 'today') {
        controller.setFilter('date', todayHkt());
        controller.setFilter('status', 'all');
        return;
      }
      if (q === 'entered') {
        controller.setFilter('status', 'entered');
        controller.setFilter('date', 'all');
        return;
      }
      if (['win', 'loss', 'skipped', 'pending'].includes(q)) {
        controller.setFilter('status', q);
        controller.setFilter('date', 'all');
      }
    });
  }

  function bindFilterToggle(prefix) {
    const toggle = document.getElementById(`${prefix}-filter-toggle`);
    const adv = document.getElementById(`${prefix}-filters-advanced`);
    if (!toggle || !adv) return;
    const key = `${prefix}-filters-open`;
    let open = false;
    try {
      open = sessionStorage.getItem(key) === '1';
    } catch (_) { /* ignore */ }
    adv.classList.toggle('open', open);
    toggle.textContent = open ? '進階篩選 ▲' : '進階篩選 ▼';
    toggle.addEventListener('click', () => {
      open = !open;
      adv.classList.toggle('open', open);
      toggle.textContent = open ? '進階篩選 ▲' : '進階篩選 ▼';
      try {
        sessionStorage.setItem(key, open ? '1' : '0');
      } catch (_) { /* ignore */ }
    });
  }

  window.DashboardHub = {
    LOG_LIMIT,
    POLL_MS,
    bindTabs,
    bindQuickFilters,
    bindFilterToggle,
    getActiveTab,
    setActiveTab,
  };
})();
