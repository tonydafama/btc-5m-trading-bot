# Mid-band Profile A (live-prep)

- Ask **0.80–0.85**, range **≥ $100**, window **T-75 → T-70** (`MIDBAND_T_MIN=70`, `MIDBAND_T_MAX=75`).
- Unit **$3** (`MIDBAND_UNIT_USD`).
- Skip if either side ask **> 0.94**.

## Before deposit

`config/midband_range80_live.env` stays **disarmed** (`MIDBAND_LIVE_ARMED=0`, `DRY_RUN=1`). Live unit **stopped**.

Reset desk: `bash scripts/midband_reset_desk_for_deposit.sh` (archives prior ledgers).

## After user confirms deposit (finance chat)

1. Update session `capital_usd` from wallet; new `live-midband-range80-*` session if needed.
2. Set live env: `DRY_RUN=0`, `REAL_MONEY=1`, `TRADING_MODE=live`, `MIDBAND_LIVE_ARMED=1`, `MIDBAND_REAL_MONEY=1`, `MIDBAND_RANGE80_LIVE=1`.
3. `systemctl restart btc-midband-range80-live` (only mid-band unit).