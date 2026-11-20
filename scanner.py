"""Escanea todos los perpetuos USDT de BingX: señales (ruptura confirmada) y candidatas (pre-ruptura)."""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from indicators import rvol, to_df
from strategies import (adjust_score, btc_regime, daily_ctx, evaluate, trend_gate,
                        watch_candidates, why_no_trend)

BTC = "BTC/USDT:USDT"
DAILY_TTL = 1200     # s: la vela diaria cambia despacio, no hace falta pedirla en cada escaneo
HOURLY_TTL = 600     # s: velas 1h cerradas para candidatas (para señales se piden frescas)
log = logging.getLogger("scanner")

# Claves del embudo (el resto de claves del Counter son motivos de descarte)
FUNNEL_KEYS = ("analizados", "filtro diario ok", "con volumen ahora", "cajas de consolidación válidas",
               "errores de datos")


@dataclass
class ScanResult:
    signals: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    movers: list = field(default_factory=list)       # (símbolo, rvol15m, %24h)
    regime: str = "neutral"
    stats: Counter = field(default_factory=Counter)
    errors: list = field(default_factory=list)
    universe: int = 0
    secs: float = 0.0
    ts: float = field(default_factory=time.time)

    def reasons(self, top: int = 5):
        return [(k, v) for k, v in self.stats.most_common() if k not in FUNNEL_KEYS][:top]


class Scanner:
    def __init__(self, ex, cfg):
        self.ex = ex
        self.cfg = cfg
        self.exclude = re.compile(cfg.exclude_regex) if cfg.exclude_regex else None
        self._cache = {}

    def liquid_symbols(self):
        tickers = self.ex.fetch_tickers()
        out = []
        for sym, t in tickers.items():
            mk = self.ex.markets.get(sym)
            if not mk or not (mk.get("swap") and mk.get("linear") and mk.get("quote") == "USDT"):
                continue
            if mk.get("active") is False:
                continue
            if self.exclude and self.exclude.search(str(mk.get("id", "")).replace("-", "")):
                continue
            qv = float(t.get("quoteVolume") or 0)
            if qv < self.cfg.min_volume_usd:
                continue
            out.append((sym, qv, float(t.get("percentage") or 0)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[: self.cfg.max_symbols]

    def _df(self, sym, tf, limit):
        return to_df(self.ex.fetch_ohlcv(sym, tf, limit=limit))

    def _get(self, sym, tf, limit, ttl, fresh=False, drop_last=False):
        """Velas con caché corta. fresh=True fuerza descarga (símbolos con señal potencial)."""
        key, now = (sym, tf), time.time()
        hit = self._cache.get(key)
        if hit and not fresh and now - hit[0] < ttl:
            return hit[1]
        df = self._df(sym, tf, limit)
        if drop_last:
            df = df.iloc[:-1]
        self._cache[key] = (now, df)
        return df

    def funding(self, sym):
        try:
            return float(self.ex.fetch_funding_rate(sym).get("fundingRate"))
        except Exception:
            return None

    def scan(self) -> ScanResult:
        t0, cfg = time.time(), self.cfg
        res = ScanResult()
        self._cache = {k: v for k, v in self._cache.items() if t0 - v[0] < 3600}
        universe = self.liquid_symbols()
        res.universe = len(universe)
        try:
            res.regime = btc_regime(self._df(BTC, "1h", 120).iloc[:-1])
        except Exception as e:
            log.warning("No se pudo calcular el régimen BTC: %s", e)

        sides = [s for s, on in (("long", cfg.enable_longs), ("short", cfg.enable_shorts)) if on]
        st = res.stats
        for sym, _qv, pct in universe:
            try:
                m = self._df(sym, "15m", 130)
                if len(m) < 110:
                    st["historial 15m insuficiente"] += 1
                    continue
                m = m.iloc[:-1]                      # descarta la vela en curso
                st["analizados"] += 1
                rv = rvol(m, 20)
                hot = rv >= cfg.prefilter_rvol or abs(pct) >= cfg.prefilter_move24

                d = self._get(sym, "1d", 60, DAILY_TTL, fresh=hot)
                if len(d) < 30:
                    st["historial diario insuficiente"] += 1
                    continue
                ctx = daily_ctx(d)
                priors = {}
                for side in sides:
                    p = trend_gate(side, ctx, cfg)
                    if p is not None:
                        priors[side] = p
                if priors:
                    st["filtro diario ok"] += 1
                else:
                    st["no pasa filtro diario: " + why_no_trend(ctx, cfg)] += 1
                if not hot and not priors:
                    continue

                h = self._get(sym, "1h", 120, HOURLY_TTL, fresh=hot, drop_last=True)
                if hot:                               # algo se mueve AHORA: busca ruptura confirmada
                    st["con volumen ahora"] += 1
                    res.movers.append((sym, rv, pct))
                    for s in evaluate(sym, d, h, m, cfg, st):
                        adjust_score(s, res.regime, self.funding(sym))
                        res.signals.append(s)
                if priors:                            # consolidando cerca del gatillo: candidata
                    res.candidates.extend(watch_candidates(sym, priors, ctx, h, m, cfg, st))
            except Exception as e:                    # un símbolo roto no debe parar el escaneo...
                st["errores de datos"] += 1           # ...pero ya NO se silencia
                if len(res.errors) < 3:
                    res.errors.append(f"{sym}: {type(e).__name__}: {e}"[:160])

        res.signals.sort(key=lambda s: s.score, reverse=True)
        res.candidates.sort(key=lambda c: c.score, reverse=True)
        res.movers.sort(key=lambda x: x[1], reverse=True)
        res.secs = time.time() - t0
        log.info("Escaneo %.0fs: %d líquidos, %d analizados, %d filtro diario, %d cajas válidas, "
                 "%d candidatas, %d señales | BTC %s", res.secs, res.universe, st["analizados"],
                 st["filtro diario ok"], st["cajas de consolidación válidas"], len(res.candidates),
                 len(res.signals), res.regime)
        if st["errores de datos"]:
            log.warning("Errores de datos en %d símbolos. Ejemplos: %s",
                        st["errores de datos"], " || ".join(res.errors))
        return res
