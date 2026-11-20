"""Configuración 100% por variables de entorno (ideal para Railway)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _s(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _i(name: str, default: int) -> int:
    return int(_f(name, default))


def _b(name: str, default: bool) -> bool:
    return _s(name, "true" if default else "false").lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Config:
    # modo y credenciales
    mode: str = "signals"
    api_key: str = ""
    api_secret: str = ""
    demo: bool = False
    hedge_mode: bool = True
    tg_token: str = ""
    tg_chat_id: str = ""
    data_dir: str = "data"

    # riesgo
    leverage: int = 5
    margin_mode: str = "isolated"
    risk_pct: float = 0.5
    max_margin_pct: float = 20.0
    max_open: int = 3
    max_trades_day: int = 6
    daily_loss_pct: float = 3.0
    paper_equity: float = 1000.0
    fee_pct: float = 0.05

    # escaneo
    scan_interval: int = 300
    manage_interval: int = 15
    trail_interval: int = 300
    min_volume_usd: float = 3_000_000
    max_symbols: int = 250
    prefilter_rvol: float = 1.8
    prefilter_move24: float = 15.0
    min_score: float = 65.0         # score mínimo para EJECUTAR (paper/live)
    alert_min_score: float = 50.0   # score mínimo para AVISAR por Telegram
    cooldown_min: int = 240
    max_signals_per_scan: int = 3
    exclude_regex: str = r"^(NCSK|NCCO|NCFX|NCSI)"
    enable_longs: bool = True
    enable_shorts: bool = True

    # candidatas (watchlist) y resúmenes
    watch_dist_pct: float = 1.5     # candidata si el precio está a <= X% del gatillo
    watch_max: int = 8              # máx. candidatas mostradas
    watch_realert_min: int = 360    # no repetir el aviso de la misma candidata antes de X min
    digest_min: int = 60            # resumen periódico del escáner (0 = desactivado)

    # estrategia (Qullamaggie adaptado a cripto intradía)
    adr_min: float = 4.0            # ADR diario mínimo (%) -> "stocks que se mueven"
    runup_min: float = 20.0         # subida/caída previa 30d (%)
    consol_bars: int = 12           # velas de 1h de consolidación
    consol_range_adr: float = 0.8   # rango de consolidación <= 0.8 * ADR
    rvol_min: float = 2.0           # volumen relativo mínimo en vela de ruptura (15m)
    max_chase_pct: float = 1.5      # no perseguir: máx % por encima del nivel roto
    ep_move_15m: float = 3.0        # Episodic Pivot: movimiento mínimo en 15m (%)
    ep_rvol: float = 4.0
    ep_max_prior: float = 40.0      # EP: no debe venir ya extendido (%)
    para_gain5: float = 40.0        # Parabolic short: subida mínima 5d (%)
    para_ext_atr: float = 3.0       # distancia a EMA20 diaria en ATR
    min_stop_atr: float = 0.8
    max_stop_atr: float = 3.0
    max_stop_pct: float = 8.0

    # gestión
    tp1_r: float = 1.5
    partial_pct: float = 50.0
    max_hold_h: float = 24.0
    trail_tf: str = "1h"
    trail_ema: int = 10


def load_config() -> Config:
    mode = _s("TRADING_MODE", "signals").lower()
    if mode not in {"signals", "paper", "live"}:
        raise SystemExit("TRADING_MODE debe ser signals, paper o live")
    d = Config()
    return Config(
        mode=mode,
        api_key=_s("BINGX_API_KEY"),
        api_secret=_s("BINGX_API_SECRET"),
        demo=_b("BINGX_DEMO", d.demo),
        hedge_mode=_b("HEDGE_MODE", d.hedge_mode),
        tg_token=_s("TELEGRAM_BOT_TOKEN"),
        tg_chat_id=_s("TELEGRAM_CHAT_ID"),
        data_dir=_s("DATA_DIR", d.data_dir),
        leverage=_i("LEVERAGE", d.leverage),
        margin_mode=_s("MARGIN_MODE", d.margin_mode).lower(),
        risk_pct=_f("RISK_PER_TRADE_PCT", d.risk_pct),
        max_margin_pct=_f("MAX_MARGIN_PCT", d.max_margin_pct),
        max_open=_i("MAX_OPEN_POSITIONS", d.max_open),
        max_trades_day=_i("MAX_TRADES_PER_DAY", d.max_trades_day),
        daily_loss_pct=_f("DAILY_LOSS_LIMIT_PCT", d.daily_loss_pct),
        paper_equity=_f("PAPER_EQUITY", d.paper_equity),
        fee_pct=_f("FEE_PCT", d.fee_pct),
        scan_interval=_i("SCAN_INTERVAL_SEC", d.scan_interval),
        manage_interval=_i("MANAGE_INTERVAL_SEC", d.manage_interval),
        trail_interval=_i("TRAIL_INTERVAL_SEC", d.trail_interval),
        min_volume_usd=_f("MIN_24H_VOLUME_USD", d.min_volume_usd),
        max_symbols=_i("SCAN_MAX_SYMBOLS", d.max_symbols),
        prefilter_rvol=_f("PREFILTER_RVOL", d.prefilter_rvol),
        prefilter_move24=_f("PREFILTER_MOVE24_PCT", d.prefilter_move24),
        min_score=_f("MIN_SCORE", d.min_score),
        alert_min_score=_f("ALERT_MIN_SCORE", d.alert_min_score),
        watch_dist_pct=_f("WATCH_DIST_PCT", d.watch_dist_pct),
        watch_max=_i("WATCH_MAX", d.watch_max),
        watch_realert_min=_i("WATCH_REALERT_MIN", d.watch_realert_min),
        digest_min=_i("DIGEST_MIN", d.digest_min),
        cooldown_min=_i("COOLDOWN_MIN", d.cooldown_min),
        max_signals_per_scan=_i("MAX_SIGNALS_PER_SCAN", d.max_signals_per_scan),
        exclude_regex=_s("EXCLUDE_REGEX", d.exclude_regex),
        enable_longs=_b("ENABLE_LONGS", d.enable_longs),
        enable_shorts=_b("ENABLE_SHORTS", d.enable_shorts),
        adr_min=_f("ADR_MIN_PCT", d.adr_min),
        runup_min=_f("RUNUP_MIN_PCT", d.runup_min),
        consol_bars=_i("CONSOL_BARS", d.consol_bars),
        consol_range_adr=_f("CONSOL_RANGE_ADR", d.consol_range_adr),
        rvol_min=_f("RVOL_MIN", d.rvol_min),
        max_chase_pct=_f("MAX_CHASE_PCT", d.max_chase_pct),
        ep_move_15m=_f("EP_MOVE_15M_PCT", d.ep_move_15m),
        ep_rvol=_f("EP_RVOL", d.ep_rvol),
        ep_max_prior=_f("EP_MAX_PRIOR_PCT", d.ep_max_prior),
        para_gain5=_f("PARABOLIC_GAIN_5D_PCT", d.para_gain5),
        para_ext_atr=_f("PARABOLIC_EXT_ATR", d.para_ext_atr),
        min_stop_atr=_f("MIN_STOP_ATR", d.min_stop_atr),
        max_stop_atr=_f("MAX_STOP_ATR", d.max_stop_atr),
        max_stop_pct=_f("MAX_STOP_PCT", d.max_stop_pct),
        tp1_r=_f("TP1_R", d.tp1_r),
        partial_pct=_f("PARTIAL_PCT", d.partial_pct),
        max_hold_h=_f("MAX_HOLD_HOURS", d.max_hold_h),
        trail_tf=_s("TRAIL_TIMEFRAME", d.trail_tf),
        trail_ema=_i("TRAIL_EMA", d.trail_ema),
    )
