"""Glance-board hint for the current BTC 5-minute window.

Public Binance BTC/USDT klines, depth, and aggregate trades only.
No API key, no Polymarket odds, no order placement.

The headline is a hint for humans. Polymarket BTC 5m settlement is the
Chainlink BTC/USD 60-second TWAP, not the Binance last price.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from statistics import pstdev
from typing import Any
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")
WINDOW_SEC = 300
SYMBOL = "BTCUSDT"
BINANCE_HOSTS = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
)
USER_AGENT = "family-desk-predict/1.0"
FETCH_TIMEOUT_SEC = 4.0
CACHE_SEC = 2.0
STALE_SEC = 30.0
CHAINLINK_TWAP_URL = "https://data.chain.link/streams/btc-usd-twap-60s-streams"

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def window_bounds(now: float | None = None) -> tuple[int, int, float]:
    """5-minute windows open when Unix time is divisible by 300."""
    ts = time.time() if now is None else float(now)
    start = int(ts) // WINDOW_SEC * WINDOW_SEC
    end = start + WINDOW_SEC
    remaining = max(0.0, end - ts)
    return start, end, remaining


def _fmt_hkt(ts: float) -> str:
    return datetime.fromtimestamp(ts, HKT).strftime("%m-%d %H:%M:%S")


def _fmt_px(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    n = abs(value)
    if n >= 1_000_000:
        return f"{sign}${n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{sign}${n / 1_000:.1f}k"
    return f"{sign}${n:,.0f}"


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binance_bundle() -> dict[str, Any]:
    """Public market data. Tries the main host, then the public vision mirror."""
    last_error: Exception | None = None
    for host in BINANCE_HOSTS:
        try:
            klines = _get_json(f"{host}/api/v3/klines?symbol={SYMBOL}&interval=5m&limit=200")
            depth = _get_json(f"{host}/api/v3/depth?symbol={SYMBOL}&limit=100")
            trades = _get_json(f"{host}/api/v3/aggTrades?symbol={SYMBOL}&limit=500")
            if not isinstance(klines, list) or len(klines) < 40:
                raise RuntimeError("klines short")
            if not isinstance(depth, dict) or not isinstance(trades, list):
                raise RuntimeError("book or trades missing")
            return {"host": host, "klines": klines, "depth": depth, "trades": trades}
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            last_error = exc
    raise RuntimeError("binance public data unavailable") from last_error


def _f(value: Any) -> float:
    return float(value)


def parse_klines(rows: list[Any]) -> dict[str, list[float]]:
    opens, highs, lows, closes, volumes = [], [], [], [], []
    open_times: list[int] = []
    for row in rows:
        open_times.append(int(row[0]) // 1000)
        opens.append(_f(row[1]))
        highs.append(_f(row[2]))
        lows.append(_f(row[3]))
        closes.append(_f(row[4]))
        volumes.append(_f(row[5]))
    return {
        "open_time": open_times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


def ema_last(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = value * k + ema * (1.0 - k)
    return ema


def ema_series(values: list[float], period: int) -> list[float | None]:
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    out: list[float | None] = [None] * (period - 1) + [ema]
    for value in values[period:]:
        ema = value * k + ema * (1.0 - k)
        out.append(ema)
    return out


def rsi_last(closes: list[float], period: int) -> float | None:
    if period <= 0 or len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd_last(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float | None] | None:
    if len(closes) < slow + signal:
        return None
    k_fast = 2.0 / (fast + 1.0)
    k_slow = 2.0 / (slow + 1.0)
    ema_fast = sum(closes[:fast]) / fast
    ema_slow = sum(closes[:slow]) / slow
    macd_values: list[float] = []
    for i, price in enumerate(closes):
        if i >= fast:
            ema_fast = price * k_fast + ema_fast * (1.0 - k_fast)
        if i >= slow:
            ema_slow = price * k_slow + ema_slow * (1.0 - k_slow)
        if i >= slow - 1:
            macd_values.append(ema_fast - ema_slow)
    if len(macd_values) < signal:
        return None
    k_sig = 2.0 / (signal + 1.0)
    sig = sum(macd_values[:signal]) / signal
    prev_hist: float | None = None
    hist = macd_values[signal - 1] - sig
    macd = macd_values[-1]
    for value in macd_values[signal:]:
        prev_hist = hist
        sig = value * k_sig + sig * (1.0 - k_sig)
        hist = value - sig
        macd = value
    return {"macd": macd, "signal": sig, "hist": hist, "prev_hist": prev_hist if prev_hist is not None else hist}


def bollinger_last(closes: list[float], period: int = 20, mult: float = 2.0) -> dict[str, float] | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    sigma = pstdev(window)
    upper = mid + mult * sigma
    lower = mid - mult * sigma
    width = upper - lower
    pct_b = 0.5 if width == 0 else (closes[-1] - lower) / width
    return {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b}


def williams_r_last(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period:
        return None
    highest = max(highs[-period:])
    lowest = min(lows[-period:])
    if highest == lowest:
        return -50.0
    return (highest - closes[-1]) / (highest - lowest) * -100.0


def supertrend_last(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 10,
    mult: float = 3.0,
) -> dict[str, Any] | None:
    """TradingView-style Supertrend. trend is 'up' or 'down'."""
    n = len(closes)
    if n < period + 2:
        return None
    trs: list[float] = []
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    atrs = [None] * n
    atrs[period] = sum(trs[:period]) / period
    for i in range(period + 1, n):
        atrs[i] = (atrs[i - 1] * (period - 1) + trs[i - 1]) / period

    lower: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    trend: list[int] = [0] * n
    line: list[float | None] = [None] * n
    for i in range(period, n):
        hl2 = (highs[i] + lows[i]) / 2.0
        basic_lower = hl2 - mult * float(atrs[i])
        basic_upper = hl2 + mult * float(atrs[i])
        if i == period:
            lower[i] = basic_lower
            upper[i] = basic_upper
            trend[i] = 1 if closes[i] >= hl2 else -1
        else:
            prev_lower = float(lower[i - 1])
            prev_upper = float(upper[i - 1])
            if closes[i - 1] > prev_lower:
                lower[i] = max(basic_lower, prev_lower)
            else:
                lower[i] = basic_lower
            if closes[i - 1] < prev_upper:
                upper[i] = min(basic_upper, prev_upper)
            else:
                upper[i] = basic_upper
            if trend[i - 1] == -1 and closes[i] > prev_upper:
                trend[i] = 1
            elif trend[i - 1] == 1 and closes[i] < prev_lower:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]
        line[i] = lower[i] if trend[i] == 1 else upper[i]
    return {
        "trend": "up" if trend[-1] == 1 else "down",
        "line": line[-1],
    }


def _levels(rows: list[Any], n: int) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in rows[:n]:
        price = _f(row[0])
        qty = _f(row[1])
        out.append({"price": price, "qty": qty, "notional": price * qty})
    return out


def summarize_book(depth: dict[str, Any], *, bias_levels: int = 20, show_levels: int = 8) -> dict[str, Any]:
    bids_all = _levels(depth.get("bids") or [], bias_levels)
    asks_all = _levels(depth.get("asks") or [], bias_levels)
    bid_notional = sum(row["notional"] for row in bids_all)
    ask_notional = sum(row["notional"] for row in asks_all)
    total = bid_notional + ask_notional
    imbalance = 0.0 if total <= 0 else (bid_notional - ask_notional) / total
    if imbalance >= 0.08:
        bias, tone = "買盤較厚", "up"
    elif imbalance <= -0.08:
        bias, tone = "賣盤較厚", "down"
    else:
        bias, tone = "兩邊接近", "flat"
    show_bids = bids_all[:show_levels]
    show_asks = asks_all[:show_levels]
    peak = max([row["qty"] for row in show_bids + show_asks] or [0.0])
    for row in show_bids + show_asks:
        row["bar"] = 0.0 if peak <= 0 else row["qty"] / peak
    return {
        "bid_notional": bid_notional,
        "ask_notional": ask_notional,
        "imbalance": imbalance,
        "bias": bias,
        "tone": tone,
        "bids": show_bids,
        "asks": show_asks,
        "levels": bias_levels,
    }


def summarize_trades(rows: list[Any], last_price: float | None) -> dict[str, Any]:
    buy_quote = sell_quote = buy_qty = sell_qty = 0.0
    prices: list[float] = []
    for row in rows:
        price = _f(row.get("p"))
        qty = _f(row.get("q"))
        quote = price * qty
        prices.append(price)
        # Binance m=true means the buyer is the maker, so the taker sold.
        if row.get("m"):
            sell_quote += quote
            sell_qty += qty
        else:
            buy_quote += quote
            buy_qty += qty
    qty = buy_qty + sell_qty
    quote = buy_quote + sell_quote
    vwap = None if qty <= 0 else quote / qty
    imbalance = 0.0 if quote <= 0 else (buy_quote - sell_quote) / quote
    if imbalance >= 0.08:
        flow, tone = "主動買較多", "up"
    elif imbalance <= -0.08:
        flow, tone = "主動賣較多", "down"
    else:
        flow, tone = "買賣接近", "flat"
    vs_last = "成交均價同現價接近"
    if vwap is not None and last_price:
        gap = vwap - last_price
        if gap > max(5.0, last_price * 0.00008):
            vs_last = "最近成交均價高過現價，偏向貴"
        elif gap < -max(5.0, last_price * 0.00008):
            vs_last = "最近成交均價低過現價，偏向平"
    return {
        "count": len(rows),
        "buy_quote": buy_quote,
        "sell_quote": sell_quote,
        "qty": qty,
        "vwap": vwap,
        "imbalance": imbalance,
        "flow": flow,
        "tone": tone,
        "vs_last": vs_last,
        "high": max(prices) if prices else None,
        "low": min(prices) if prices else None,
    }


def _sign(value: float, dead: float = 0.0) -> int:
    if value > dead:
        return 1
    if value < -dead:
        return -1
    return 0


def judge(features: dict[str, Any]) -> dict[str, Any]:
    """Pick 升/跌 and a continuation-vs-reversal lean from already-computed features."""
    move_sign = int(features.get("move_sign") or 0)
    remaining = float(features.get("remaining_sec") or 0)
    votes: list[tuple[str, int]] = []

    def add(name: str, sign: int) -> None:
        if sign:
            votes.append((name, 1 if sign > 0 else -1))

    add("ema", int(features.get("ema_sign") or 0))
    add("macd", int(features.get("macd_sign") or 0))
    add("supertrend", int(features.get("supertrend_sign") or 0))
    add("rsi", int(features.get("rsi_sign") or 0))
    add("book", int(features.get("book_sign") or 0))
    add("trades", int(features.get("trade_sign") or 0))

    pressure = 0.0 if not votes else sum(sign for _, sign in votes) / len(votes)
    agree = sum(1 for _, sign in votes if sign == move_sign) if move_sign else 0
    oppose = sum(1 for _, sign in votes if sign == -move_sign) if move_sign else 0
    stretch_n = int(features.get("stretch_n") or 0)
    stretch_against = bool(features.get("stretch_against"))

    if move_sign == 0:
        lean = "unclear"
    elif stretch_n >= 2 and oppose >= 1:
        lean = "reverse"
    elif oppose >= 3 and oppose > agree:
        lean = "reverse"
    elif agree >= 3 and stretch_n == 0:
        lean = "continue"
    elif agree >= 3 and stretch_n <= 1 and oppose == 0:
        lean = "continue"
    elif stretch_n >= 2 and agree < 2:
        lean = "reverse"
    elif oppose > agree:
        lean = "reverse"
    elif agree > oppose and stretch_n == 0:
        lean = "continue"
    else:
        lean = "unclear"

    if lean == "continue" and move_sign:
        direction_sign = move_sign
    elif lean == "reverse" and move_sign:
        direction_sign = -move_sign
    else:
        time_w = 1.4 if remaining < 60 else (0.9 if remaining < 180 else 0.45)
        blended = time_w * move_sign + pressure
        if blended > 0.12:
            direction_sign = 1
        elif blended < -0.12:
            direction_sign = -1
        elif move_sign:
            direction_sign = move_sign
        else:
            direction_sign = 1 if pressure >= 0 else -1

    if lean == "unclear" or move_sign == 0:
        confidence = "low"
    elif lean == "continue" and agree >= 4 and not stretch_against:
        confidence = "high"
    elif lean == "reverse" and stretch_n >= 2 and oppose >= 1:
        confidence = "mid" if stretch_n < 3 else "high"
    elif lean == "continue" and agree >= 2:
        confidence = "mid"
    else:
        confidence = "low"
    if remaining < 45 and lean == "reverse" and confidence == "high":
        confidence = "mid"

    way = "升" if move_sign > 0 else ("跌" if move_sign < 0 else "")
    if lean == "continue" and way:
        lean_text = f"價格呢個窗一路{way}。指標同掛單未顯得力竭，較似延續。"
    elif lean == "reverse" and way:
        lean_text = f"價格呢個窗一路{way}，但已經偏一邊，或者掛單同成交流向相反，較似反轉。"
    elif way:
        lean_text = f"價格呢個窗一路{way}，延續同反轉嘅證據差唔多，未分高低。"
    else:
        lean_text = "呢個窗相對 Binance 開盤幾乎冇走，延續同反轉都未有方向。"

    return {
        "direction": "up" if direction_sign > 0 else "down",
        "label": "升" if direction_sign > 0 else "跌",
        "lean": lean,
        "lean_label": {"continue": "較似延續", "reverse": "較似反轉", "unclear": "未分高低"}[lean],
        "lean_text": lean_text,
        "confidence": confidence,
        "confidence_label": {"high": "信心較高", "mid": "信心一般", "low": "信心弱"}[confidence],
        "pressure": pressure,
        "agree": agree,
        "oppose": oppose,
    }


def _tone_word(sign: int, up: str, down: str, flat: str) -> tuple[str, str]:
    if sign > 0:
        return up, "up"
    if sign < 0:
        return down, "down"
    return flat, "flat"


def compose_payload(bundle: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    candles = parse_klines(bundle["klines"])
    closes = candles["close"]
    if len(closes) < 40:
        return {"ok": False, "error": "K 線不夠，未有提示。"}
    start, end, remaining = window_bounds(now)
    price = closes[-1]
    if int(candles["open_time"][-1]) == start:
        window_open = candles["open"][-1]
    elif len(candles["open_time"]) >= 2 and int(candles["open_time"][-2]) == start:
        window_open = candles["open"][-2]
    else:
        window_open = candles["open"][-1]
    delta = price - window_open
    flat_band = max(10.0, window_open * 0.00012)
    move_sign = _sign(delta, flat_band)

    ema7 = ema_last(closes, 7)
    ema25 = ema_last(closes, 25)
    ema99 = ema_last(closes, 99)
    ema_sign = 0
    if None not in (ema7, ema25, ema99):
        if price > ema7 > ema25 > ema99:
            ema_sign = 1
        elif price < ema7 < ema25 < ema99:
            ema_sign = -1
    rsi6 = rsi_last(closes, 6)
    rsi14 = rsi_last(closes, 14)
    rsi_sign = 0
    if rsi14 is not None:
        if rsi14 >= 56:
            rsi_sign = 1
        elif rsi14 <= 44:
            rsi_sign = -1
    macd = macd_last(closes)
    macd_sign = 0 if not macd else _sign(macd["hist"])
    bb = bollinger_last(closes, 20, 2.0)
    wr = williams_r_last(candles["high"], candles["low"], closes, 14)
    st = supertrend_last(candles["high"], candles["low"], closes, 10, 3.0)
    st_sign = 0 if not st else (1 if st["trend"] == "up" else -1)

    book = summarize_book(bundle.get("depth") or {})
    book_sign = 1 if book["tone"] == "up" else (-1 if book["tone"] == "down" else 0)
    trades = summarize_trades(bundle.get("trades") or [], price)
    trade_sign = 1 if trades["tone"] == "up" else (-1 if trades["tone"] == "down" else 0)

    stretch_up = []
    stretch_down = []
    # Stretch means the move is already extended, not merely "RSI is above 70" in a trend.
    if rsi14 is not None and rsi14 >= 80:
        stretch_up.append("RSI 14 超買")
    if rsi14 is not None and rsi14 <= 20:
        stretch_down.append("RSI 14 超賣")
    if rsi6 is not None and rsi6 >= 88:
        stretch_up.append("RSI 6 超買")
    if rsi6 is not None and rsi6 <= 12:
        stretch_down.append("RSI 6 超賣")
    if bb is not None and bb["pct_b"] >= 1:
        stretch_up.append("價高出布林上軌")
    if bb is not None and bb["pct_b"] <= 0:
        stretch_down.append("價低過布林下軌")
    if wr is not None and wr >= -5 and rsi14 is not None and rsi14 >= 78:
        stretch_up.append("威廉指標超買")
    if wr is not None and wr <= -95 and rsi14 is not None and rsi14 <= 22:
        stretch_down.append("威廉指標超賣")
    stretch_against = (move_sign > 0 and bool(stretch_up)) or (move_sign < 0 and bool(stretch_down))
    stretch_n = len(stretch_up if move_sign > 0 else stretch_down) if stretch_against else 0

    decision = judge(
        {
            "move_sign": move_sign,
            "remaining_sec": remaining,
            "ema_sign": ema_sign,
            "macd_sign": macd_sign,
            "supertrend_sign": st_sign,
            "rsi_sign": rsi_sign,
            "book_sign": book_sign,
            "trade_sign": trade_sign,
            "stretch_against": stretch_against,
            "stretch_n": stretch_n,
        }
    )

    completed = list(zip(candles["open"][:-1], closes[:-1]))
    last3 = completed[-3:]
    up3 = sum(1 for o, c in last3 if c > o)
    down3 = sum(1 for o, c in last3 if c < o)

    reasons: list[str] = []
    if move_sign > 0:
        reasons.append("Binance 呢個窗而家高過開盤")
    elif move_sign < 0:
        reasons.append("Binance 呢個窗而家低過開盤")
    if ema_sign > 0:
        reasons.append("EMA 7／25／99 多頭排列")
    elif ema_sign < 0:
        reasons.append("EMA 7／25／99 空頭排列")
    if macd_sign > 0:
        reasons.append("MACD 柱為正")
    elif macd_sign < 0:
        reasons.append("MACD 柱為負")
    if st_sign > 0:
        reasons.append("超級趨勢向上")
    elif st_sign < 0:
        reasons.append("超級趨勢向下")
    if book_sign > 0:
        reasons.append("掛單買盤較厚")
    elif book_sign < 0:
        reasons.append("掛單賣盤較厚")
    if trade_sign > 0:
        reasons.append("最近成交主動買較多")
    elif trade_sign < 0:
        reasons.append("最近成交主動賣較多")
    for note in (stretch_up if move_sign > 0 else stretch_down):
        reasons.append(note)

    ema_read, ema_tone = _tone_word(ema_sign, "多頭排列", "空頭排列", "交錯")
    macd_read = "—"
    macd_tone = "flat"
    if macd:
        expanding = macd["prev_hist"] is not None and abs(macd["hist"]) > abs(macd["prev_hist"])
        side = "柱正" if macd["hist"] > 0 else ("柱負" if macd["hist"] < 0 else "柱平")
        macd_read = f"{side} · {'柱擴' if expanding else '柱縮'}"
        macd_tone = "up" if macd["hist"] > 0 else ("down" if macd["hist"] < 0 else "flat")
    if rsi14 is None:
        rsi_read, rsi_tone = "—", "flat"
    elif rsi14 >= 70:
        rsi_read, rsi_tone = "超買", "warn"
    elif rsi14 <= 30:
        rsi_read, rsi_tone = "超賣", "warn"
    elif rsi14 >= 56:
        rsi_read, rsi_tone = "偏高", "up"
    elif rsi14 <= 44:
        rsi_read, rsi_tone = "偏低", "down"
    else:
        rsi_read, rsi_tone = "中性", "flat"
    if rsi6 is None:
        rsi6_read, rsi6_tone = "—", "flat"
    elif rsi6 >= 80:
        rsi6_read, rsi6_tone = "超買", "warn"
    elif rsi6 <= 20:
        rsi6_read, rsi6_tone = "超賣", "warn"
    elif rsi6 >= 60:
        rsi6_read, rsi6_tone = "偏高", "up"
    elif rsi6 <= 40:
        rsi6_read, rsi6_tone = "偏低", "down"
    else:
        rsi6_read, rsi6_tone = "中性", "flat"
    if wr is None:
        wr_read, wr_tone = "—", "flat"
    elif wr >= -20:
        wr_read, wr_tone = "超買", "warn"
    elif wr <= -80:
        wr_read, wr_tone = "超賣", "warn"
    else:
        wr_read, wr_tone = "中性", "flat"
    if bb is None:
        bb_read, bb_tone = "—", "flat"
    elif bb["pct_b"] >= 1:
        bb_read, bb_tone = "高出上軌", "warn"
    elif bb["pct_b"] <= 0:
        bb_read, bb_tone = "低過下軌", "warn"
    elif bb["pct_b"] >= 0.66:
        bb_read, bb_tone = "軌內偏上", "up"
    elif bb["pct_b"] <= 0.34:
        bb_read, bb_tone = "軌內偏下", "down"
    else:
        bb_read, bb_tone = "軌內中間", "flat"
    st_read, st_tone = ("—", "flat") if not st else _tone_word(st_sign, "升趨勢", "跌趨勢", "—")

    def num(value: float | None, digits: int = 2) -> str:
        if value is None:
            return "—"
        return f"{value:,.{digits}f}"

    indicators = [
        {
            "name": "EMA 7／25／99",
            "value": f"{num(ema7)}／{num(ema25)}／{num(ema99)}",
            "read": ema_read,
            "tone": ema_tone,
        },
        {
            "name": "布林 20, 2",
            "value": "—" if not bb else f"中 {num(bb['mid'])} · 上 {num(bb['upper'])} · 下 {num(bb['lower'])}",
            "read": bb_read,
            "tone": bb_tone,
        },
        {
            "name": "超級趨勢 10, 3",
            "value": "—" if not st else num(st["line"]),
            "read": st_read,
            "tone": st_tone,
        },
        {"name": "威廉 %R 14", "value": "—" if wr is None else f"{wr:.1f}", "read": wr_read, "tone": wr_tone},
        {"name": "RSI 6", "value": "—" if rsi6 is None else f"{rsi6:.1f}", "read": rsi6_read, "tone": rsi6_tone},
        {"name": "RSI 14", "value": "—" if rsi14 is None else f"{rsi14:.1f}", "read": rsi_read, "tone": rsi_tone},
        {
            "name": "MACD 12, 26, 9",
            "value": "—" if not macd else f"{macd['macd']:.2f}／{macd['signal']:.2f}／{macd['hist']:.2f}",
            "read": macd_read,
            "tone": macd_tone,
        },
    ]

    spark_closes = closes[-48:]
    ema7_series = [v for v in ema_series(closes, 7)[-48:]]

    notes = [
        "只係提示，唔係已證明嘅優勢，亦唔係官方結算。",
        "官方結算係 Chainlink BTC/USD TWAP 60 秒，唔係 Binance 最後價。",
    ]
    if remaining < 60:
        notes.append("剩唔夠一分鐘。Binance 現價同官方 TWAP 仍然可以唔同。")

    return {
        "ok": True,
        "stale": False,
        "source_label": "Binance BTC/USDT 公開行情",
        "symbol": SYMBOL,
        "server_now": time.time() if now is None else float(now),
        "window_start": start,
        "window_end": end,
        "window_start_hkt": _fmt_hkt(start),
        "window_end_hkt": _fmt_hkt(end),
        "remaining_sec": round(remaining, 1),
        "price": price,
        "price_text": _fmt_px(price),
        "window_open": window_open,
        "window_open_text": _fmt_px(window_open),
        "delta": delta,
        "delta_text": f"{delta:+,.2f}",
        "move_label": "高過窗開" if move_sign > 0 else ("低過窗開" if move_sign < 0 else "貼住窗開"),
        "prior_bars": f"打前 3 支已收 5 分鐘 K：{up3} 升 {down3} 跌",
        "direction": decision["direction"],
        "label": decision["label"],
        "lean": decision["lean"],
        "lean_label": decision["lean_label"],
        "lean_text": decision["lean_text"],
        "confidence": decision["confidence"],
        "confidence_label": decision["confidence_label"],
        "reasons": reasons[:7],
        "indicators": indicators,
        "book": {
            **book,
            "bid_notional_text": _fmt_usd(book["bid_notional"]),
            "ask_notional_text": _fmt_usd(book["ask_notional"]),
            "imbalance_text": f"{book['imbalance'] * 100:+.0f}%",
        },
        "trades": {
            **trades,
            "buy_quote_text": _fmt_usd(trades["buy_quote"]),
            "sell_quote_text": _fmt_usd(trades["sell_quote"]),
            "qty_text": f"{trades['qty']:.3f} BTC",
            "vwap_text": _fmt_px(trades["vwap"]),
        },
        "spark": {"closes": spark_closes, "ema7": ema7_series},
        "notes": notes,
        "settlement_url": CHAINLINK_TWAP_URL,
    }


def _failed(message: str) -> dict[str, Any]:
    start, end, remaining = window_bounds()
    return {
        "ok": False,
        "stale": False,
        "error": message,
        "server_now": time.time(),
        "window_start": start,
        "window_end": end,
        "window_start_hkt": _fmt_hkt(start),
        "window_end_hkt": _fmt_hkt(end),
        "remaining_sec": round(remaining, 1),
        "settlement_url": CHAINLINK_TWAP_URL,
        "source_label": "Binance BTC/USDT 公開行情",
    }


def build_hint_payload(*, now: float | None = None, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the glance-board payload. Pass bundle in tests to avoid the network."""
    if bundle is not None:
        try:
            return compose_payload(bundle, now=now)
        except (TypeError, ValueError, IndexError, KeyError):
            return _failed("行情格式唔對，未有提示。")

    now_mono = time.monotonic()
    with _cache_lock:
        cached = _cache["payload"]
        age = now_mono - float(_cache["ts"] or 0)
        if cached and age <= CACHE_SEC:
            return json.loads(json.dumps(cached))
    try:
        fetched = fetch_binance_bundle()
    except Exception:
        with _cache_lock:
            cached = _cache["payload"]
            age = now_mono - float(_cache["ts"] or 0)
            if cached and age <= STALE_SEC:
                stale = json.loads(json.dumps(cached))
                stale["stale"] = True
                stale["error"] = "暫時更新唔到，顯示上一版。"
                return stale
        return _failed("暫時攞唔到 Binance 公開行情。頁面冇估方向。")
    payload = compose_payload(fetched, now=now)
    with _cache_lock:
        _cache["ts"] = time.monotonic()
        _cache["payload"] = payload
    return json.loads(json.dumps(payload))
