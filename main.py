from __future__ import annotations

import html
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from config import load_config
from exchange import make_exchange
from scanner import Scanner
from state import State
from telegram import Notifier
from trader import Trader, px

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("main")


def fmt_signal(s, regime: str, cfg, tradable: bool = True) -> str:
    long = s.side == "long"
    d = 1 if long else -1
    tp1 = s.entry + d * cfg.tp1_r * s.risk
    return (
        f"{'🟢' if long else '🔴'} <b>{s.side.upper()} {s.symbol}</b> · {s.setup}\n"
        f"Score <b>{s.score:.0f}/100</b> | BTC: {regime}\n"
        f"Entrada ≈ {px(s.entry)}\n"
        f"SL {px(s.stop)} ({s.risk / s.entry * 100:.2f}%) | TP1 {px(tp1)} ({cfg.tp1_r}R)\n"
        f"Plan: {cfg.partial_pct:.0f}% en TP1, stop a break-even y trailing EMA{cfg.trail_ema} {cfg.trail_tf}\n"
        + "\n".join(f"• {r}" for r in s.reasons)
        + ("" if tradable or cfg.mode == "signals"
           else f"\nℹ️ Score por debajo de MIN_SCORE ({cfg.min_score:.0f}): solo aviso, el bot no la ejecuta")
    )


def fmt_cand(c, cfg) -> str:
    long = c.side == "long"
    return (
        f"{'🟢' if long else '🔴'} <b>{c.symbol}</b> {c.side.upper()} · {c.setup}\n"
        f"   Precio {px(c.price)} → gatillo {px(c.trigger)} (a {c.dist_pct:.2f}%)\n"
        f"   ENTRAR si una vela 15m CIERRA {'&gt;' if long else '&lt;'} {px(c.trigger)} "
        f"con RVOL ≥ {cfg.rvol_min:g}x (ahora {c.rvol:.1f}x)\n"
        f"   SL ≈ {px(c.stop)} ({c.risk / c.trigger * 100:.2f}%) | ADR {c.adr:.1f}% | "
        f"caja 1h {c.rng:.2f}% | previo {c.prior:.0f}%"
    )


def fmt_watch(cands, cfg, title="👀 <b>Candidatas a explotar</b> (aún sin ruptura)") -> str:
    return title + "\n\n" + "\n\n".join(fmt_cand(c, cfg) for c in cands)


def fmt_digest(res, cfg) -> str:
    """Resumen del último escaneo: prueba de vida + por qué no hay señales."""
    if res is None:
        return "Aún no hay ningún escaneo completado."
    st = res.stats
    age = max(0, int((time.time() - res.ts) / 60))
    out = [
        f"🩺 <b>Escáner</b> · último escaneo hace {age} min (duró {res.secs:.0f}s)",
        f"BTC: {res.regime} | Líquidos: {res.universe} | Analizados: {st['analizados']}",
        f"Filtro diario OK: {st['filtro diario ok']} | Cajas 1h válidas: {st['cajas de consolidación válidas']} | "
        f"Candidatas: {len(res.candidates)} | Señales: {len(res.signals)}",
    ]
    if res.candidates:
        out.append("\n<b>Top candidatas</b> (/watchlist para el detalle):")
        out += [f"• {c.symbol} {c.side.upper()} gatillo {px(c.trigger)} (a {c.dist_pct:.2f}%)"
                for c in res.candidates[:5]]
    if res.movers:
        out.append("\n<b>Con volumen ahora</b> (RVOL 15m · %24h):")
        out += [f"• {s} {rv:.1f}x · {p:+.1f}%" for s, rv, p in res.movers[:5]]
    rs = res.reasons(5)
    if rs:
        out.append("\n<b>Por qué se descartan las demás:</b>")
        out += [f"• {k}: {v}" for k, v in rs]
    if st["errores de datos"]:
        out.append(f"\n⚠️ Errores de datos en {st['errores de datos']} símbolos. Ej.: "
                   + html.escape(res.errors[0] if res.errors else ""))
    return "\n".join(out)


def fmt_day(prev: dict) -> str:
    return (f"📊 <b>Resumen {prev['date']}</b>\nOperaciones: {prev['trades']} | "
            f"PnL realizado: ${prev['realized']:+.2f}")


def main():
    cfg = load_config()
    if cfg.mode == "live" and not (cfg.api_key and cfg.api_secret):
        raise SystemExit("TRADING_MODE=live requiere BINGX_API_KEY y BINGX_API_SECRET")

    st = State(cfg.data_dir, cfg.paper_equity)
    tg = Notifier(cfg)
    scan_ex = make_exchange(cfg, auth=False)                 # datos públicos, sin claves
    trade_ex = make_exchange(cfg, auth=cfg.mode == "live")
    scanner = Scanner(scan_ex, cfg)
    trader = Trader(trade_ex, cfg, st, tg.send)
    stop = threading.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    last = {"res": None, "digest": 0.0}

    def scan_loop():
        while not stop.is_set():
            t0 = time.time()
            try:
                res = scanner.scan()
                last["res"] = res

                # 1) Señales: ruptura confirmada (aviso desde ALERT_MIN_SCORE; ejecuta desde MIN_SCORE)
                alerts = [x for x in res.signals if x.score >= cfg.alert_min_score]
                for s in alerts[: cfg.max_signals_per_scan]:
                    if st.cooldown_active(s.symbol, cfg.cooldown_min):
                        continue
                    tradable = s.score >= cfg.min_score
                    tg.send(fmt_signal(s, res.regime, cfg, tradable))
                    st.set_cooldown(s.symbol)
                    if cfg.mode != "signals" and tradable:
                        ok, why = trader.open(s)
                        if not ok:
                            tg.send(f"⏭ {s.symbol} no ejecutada: {why}")

                # 2) Candidatas: consolidando cerca del gatillo (solo las nuevas)
                new = [c for c in res.candidates[: cfg.watch_max]
                       if st.watch_due(c.key, cfg.watch_realert_min)]
                if new:
                    tg.send(fmt_watch(new, cfg))
                    st.watch_mark([c.key for c in new])

                # 3) Resumen periódico (prueba de vida + embudo de descartes)
                if cfg.digest_min > 0 and time.time() - last["digest"] >= cfg.digest_min * 60:
                    tg.send(fmt_digest(res, cfg))
                    last["digest"] = time.time()
            except Exception as e:
                log.exception("scan error")
                tg.alert_once("scan", f"⚠️ Error en el escaneo: {html.escape(str(e))[:300]}")
            stop.wait(max(5, cfg.scan_interval - (time.time() - t0)))

    def manage_loop():
        while not stop.is_set():
            try:
                prev = st.ensure_day(trader.equity)
                if prev:
                    tg.send(fmt_day(prev))
                trader.manage()
            except Exception as e:
                log.exception("manage error")
                tg.alert_once("manage", f"⚠️ Error gestionando posiciones: {html.escape(str(e))[:300]}")
            stop.wait(cfg.manage_interval)

    def cmd_pause():
        st.data["paused"] = True
        st.save()
        return "⏸ Bot en pausa: no abrirá nuevas operaciones (las abiertas se siguen gestionando)."

    def cmd_resume():
        st.data["paused"] = False
        st.save()
        return "▶️ Bot reanudado."

    def cmd_closeall():
        return f"🧹 Posiciones cerradas: {trader.close_all()}"

    def cmd_watchlist():
        res = last["res"]
        if res is None:
            return "Aún no hay ningún escaneo completado (el primero tarda 1-2 min)."
        if not res.candidates:
            return "Sin candidatas ahora mismo. Usa /scan para ver por qué."
        return fmt_watch(res.candidates[: cfg.watch_max], cfg, "👀 <b>Candidatas ahora</b>")

    handlers = {
        "/watchlist": cmd_watchlist,
        "/scan": lambda: fmt_digest(last["res"], cfg),
        "/status": trader.summary,
        "/positions": trader.positions_text,
        "/pause": cmd_pause,
        "/resume": cmd_resume,
        "/closeall": cmd_closeall,
        "/help": lambda: "/watchlist /scan /status /positions /pause /resume /closeall",
    }

    prev = st.ensure_day(trader.equity)
    if not tg.enabled:
        log.error("TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID vacíos: NO se enviará nada a Telegram")
    started = tg.send(
        f"🤖 <b>Qullamaggie-BingX bot iniciado</b>\n"
        f"Modo: <b>{cfg.mode.upper()}</b>{' (DEMO)' if cfg.demo else ''} | Escaneo cada {cfg.scan_interval}s\n"
        f"Riesgo/op {cfg.risk_pct}% | Lev {cfg.leverage}x | Score mín {cfg.min_score:.0f}\n"
        f"Avisa señales desde score {cfg.alert_min_score:.0f}"
        f"{'' if cfg.mode == 'signals' else f' (ejecuta desde {cfg.min_score:.0f})'} y candidatas a ≤{cfg.watch_dist_pct}% del gatillo\n"
        f"Comandos: /watchlist /scan /status /positions /pause /resume /closeall"
    )
    if tg.enabled and not started:
        log.error("El mensaje de arranque NO llegó a Telegram: revisa token, chat_id y haber pulsado /start en el bot")
    for fn in (scan_loop, manage_loop, lambda: tg.listen(handlers, stop)):
        threading.Thread(target=fn, daemon=True).start()
    stop.wait()
    log.info("Apagando...")


if __name__ == "__main__":
    main()
