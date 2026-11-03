#!/usr/bin/env python3
"""Make the gold-box bet size update on screen and survive the 20s refresh."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/btc-5m-bot")

HELPERS = r"""
  function snapshotDeskBets() {
    const snap = {};
    document.querySelectorAll('#hl-body .bet-size-input').forEach((input) => {
      const id = input.getAttribute('data-bot-id');
      if (!id) return;
      snap[id] = { value: input.value, focus: document.activeElement === input };
    });
    return snap;
  }
  function paintDeskBet(card, bet) {
    if (!card) return;
    const meta = deskBotMeta({ id: card.getAttribute('data-desk-bot-id') });
    if (!meta) return;
    const n = Number(bet);
    if (!Number.isFinite(n)) return;
    const stake = Math.round(n * Number(meta.mult) * 100) / 100;
    const rule = deskRuleLine(meta.rule, n, meta.mult);
    const stakeTxt = '$' + deskFmt(stake);
    const per = stakeTxt + '/注';
    const stakeEl = card.querySelector('[data-stake]');
    if (stakeEl) stakeEl.textContent = stakeTxt;
    const ruleEl = card.querySelector('[data-rule]');
    if (ruleEl) ruleEl.textContent = rule;
    card.querySelectorAll('[data-stake-label]').forEach((el) => { el.textContent = per; });
  }
  const _deskBetTimer = {};
  function saveDeskBet(botId, bet, delay) {
    const n = Number(bet);
    const card = document.querySelector('[data-desk-bot-id="' + botId + '"]');
    if (!Number.isFinite(n) || n < 0.5 || n > 500) {
      const hint = card && card.querySelector('[data-bot-hint]');
      if (hint) {
        hint.textContent = 'bet size 要喺 0.5 到 500';
        hint.classList.add('is-error');
      }
      return;
    }
    clearTimeout(_deskBetTimer[botId]);
    if (delay) {
      _deskBetTimer[botId] = setTimeout(() => postDeskBot(botId, 'set_bet_size', n), delay);
      return;
    }
    postDeskBot(botId, 'set_bet_size', n);
  }
"""

OLD_BIND = """  function bindDeskSplitBots() {
    const body = $('hl-body');
    if (!body || body.dataset.deskBound === '1') return;
    body.dataset.deskBound = '1';
    body.addEventListener('click', (ev) => {
      const step = ev.target.closest('[data-bet-step]');
      if (step) {
        const botId = step.dataset.botId;
        const card = document.querySelector('[data-desk-bot-id="' + botId + '"]');
        const input = card && card.querySelector('.bet-size-input');
        if (!input) return;
        const next = Math.round((Number(input.value || 0) + Number(step.dataset.betStep)) * 100) / 100;
        input.value = String(Math.min(500, Math.max(0.5, next)));
        postDeskBot(botId, 'set_bet_size', Number(input.value));
        return;
      }
      const apply = ev.target.closest('[data-bet-apply]');
      if (apply) {
        const botId = apply.dataset.botId;
        const card = document.querySelector('[data-desk-bot-id="' + botId + '"]');
        const input = card && card.querySelector('.bet-size-input');
        const bet = Number(input && input.value);
        if (!Number.isFinite(bet) || bet < 0.5 || bet > 500) {
          const hint = card && card.querySelector('[data-bot-hint]');
          if (hint) hint.textContent = 'bet size 要喺 0.5 到 500';
          return;
        }
        postDeskBot(botId, 'set_bet_size', bet);
        return;
      }"""

NEW_BIND = HELPERS + """  function bindDeskSplitBots() {
    const body = $('hl-body');
    if (!body || body.dataset.deskBound === '1') return;
    body.dataset.deskBound = '1';
    body.addEventListener('input', (ev) => {
      const input = ev.target.closest && ev.target.closest('.bet-size-input');
      if (!input) return;
      const botId = input.getAttribute('data-bot-id');
      const card = input.closest('[data-desk-bot-id]');
      paintDeskBet(card, input.value);
      saveDeskBet(botId, input.value, 350);
    });
    body.addEventListener('click', (ev) => {
      const step = ev.target.closest('[data-bet-step]');
      if (step) {
        const botId = step.dataset.botId;
        const card = document.querySelector('[data-desk-bot-id="' + botId + '"]');
        const input = card && card.querySelector('.bet-size-input');
        if (!input) return;
        const next = Math.round((Number(input.value || 0) + Number(step.dataset.betStep)) * 100) / 100;
        input.value = String(Math.min(500, Math.max(0.5, next)));
        paintDeskBet(card, input.value);
        saveDeskBet(botId, input.value, 0);
        return;
      }
      const apply = ev.target.closest('[data-bet-apply]');
      if (apply) {
        const botId = apply.dataset.botId;
        const card = document.querySelector('[data-desk-bot-id="' + botId + '"]');
        const input = card && card.querySelector('.bet-size-input');
        paintDeskBet(card, input && input.value);
        saveDeskBet(botId, input && input.value, 0);
        return;
      }"""

OLD_WIRE = """  function wireDeskCards(rows) {
    (rows || []).forEach((r) => {
      if (!DESK_BOT_IDS.includes(String(r.id || ''))) return;
      const card = document.querySelector('[data-desk-bot-id="' + r.id + '"]');
      if (!card) return;
      paintDeskCard(card, deskBotMode(r));
      if (r.bet_size != null) {
        const input = card.querySelector('.bet-size-input');
        if (input && document.activeElement !== input) input.value = r.bet_size;
      }
      if (r.rule_line) {
        const ruleEl = card.querySelector('[data-rule]');
        if (ruleEl) ruleEl.textContent = r.rule_line;
      }
      if (r.stake_usd != null) {
        const stakeEl = card.querySelector('[data-stake]');
        if (stakeEl) stakeEl.textContent = '$' + r.stake_usd;
      }
    });
    bindDeskSplitBots();
  }"""

NEW_WIRE = """  function wireDeskCards(rows, betSnap) {
    const snap = betSnap || {};
    (rows || []).forEach((r) => {
      if (!DESK_BOT_IDS.includes(String(r.id || ''))) return;
      const card = document.querySelector('[data-desk-bot-id="' + r.id + '"]');
      if (!card) return;
      paintDeskCard(card, deskBotMode(r));
      const input = card.querySelector('.bet-size-input');
      const keep = snap[String(r.id || '')];
      if (keep && input) {
        input.value = keep.value;
        paintDeskBet(card, keep.value);
        if (keep.focus) {
          input.focus();
          const end = String(input.value || '').length;
          try { input.setSelectionRange(end, end); } catch (err) {}
        }
      } else if (input && r.bet_size != null) {
        input.value = r.bet_size;
        if (r.rule_line) {
          const ruleEl = card.querySelector('[data-rule]');
          if (ruleEl) ruleEl.textContent = r.rule_line;
        }
        if (r.stake_usd != null) {
          const stakeEl = card.querySelector('[data-stake]');
          if (stakeEl) stakeEl.textContent = '$' + r.stake_usd;
        }
      }
    });
    bindDeskSplitBots();
  }"""

OLD_RENDER = """  function renderHighLive(board) {
    const body = $('hl-body');
    const noteEl = $('hl-note');
    const badge = $('hl-badge');
    if (!body) return;
"""

NEW_RENDER = """  function renderHighLive(board) {
    const body = $('hl-body');
    const noteEl = $('hl-note');
    const badge = $('hl-badge');
    if (!body) return;
    const betSnap = snapshotDeskBets();
"""

OLD_CALL = "    wireDeskCards(rows);\n"
NEW_CALL = "    wireDeskCards(rows, betSnap);\n"

OLD_SUB = """            <div class="hl-subline">${esc(r.status_label || r.status || '')} · ${esc(stake)} · 本金 ${esc(cap)}</div>"""
NEW_SUB = """            <div class="hl-subline">${esc(r.status_label || r.status || '')} · <span data-stake-label>${esc(stake)}</span> · 本金 ${esc(cap)}</div>"""

OLD_STAT = """            <div class="hl-stat"><div class="k">注碼</div><div class="v">${esc(stake)}</div></div>"""
NEW_STAT = """            <div class="hl-stat"><div class="k">注碼</div><div class="v" data-stake-label>${esc(stake)}</div></div>"""


def main() -> None:
    path = ROOT / "dashboard" / "templates" / "v1_only.html"
    text = path.read_text(encoding="utf-8")
    if "function paintDeskBet" in text and "function snapshotDeskBets" in text:
        print("already interactive")
        return
    steps = (
        ("bind", OLD_BIND, NEW_BIND),
        ("wire", OLD_WIRE, NEW_WIRE),
        ("render", OLD_RENDER, NEW_RENDER),
        ("call", OLD_CALL, NEW_CALL),
        ("sub", OLD_SUB, NEW_SUB),
        ("stat", OLD_STAT, NEW_STAT),
    )
    for name, old, new in steps:
        count = text.count(old)
        if name == "stat":
            if count != 2:
                raise SystemExit(f"{name} count {count}")
            text = text.replace(old, new)
        else:
            if count != 1:
                raise SystemExit(f"{name} count {count}")
            text = text.replace(old, new, 1)
        print(name, "ok")
    path.write_text(text, encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
