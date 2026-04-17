#!/usr/bin/env python3
"""
NEXUS APEX — Motor de Backtest Profesional
Cobertura completa: Crypto (Binance) + FX/Indices/Commodities (yfinance)
Timeframes: M1, M5, M15, 1H, 4H, 1D
"""

import pandas as pd
import numpy as np
import yfinance as yf
import urllib.request
import json
import ssl
import certifi
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
CAPITAL_INICIAL = 10000.0
RISK_PCT        = 1.0
COMISION        = 0.001
SLIPPAGE        = 0.0005

CRYPTO = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
}

EXTERNOS = {
    "EUR":   "EURUSD=X",
    "GBP":   "GBPUSD=X",
    "GOLD":  "GC=F",
    "OIL":   "BZ=F",
    "SP500": "^GSPC",
    "NAS":   "^IXIC",
}

# Timeframes Binance → (intervalo_binance, límite_días, label)
BINANCE_TF = {
    "M1":  ("1m",  7,   "1m"),
    "M5":  ("5m",  60,  "5m"),
    "M15": ("15m", 60,  "15m"),
    "1H":  ("1h",  730, "1h"),
    "4H":  ("4h",  730, "4h"),
    "1D":  ("1d",  1825,"1d"),
}

YFINANCE_TF = {
    "1H":  ("1h",  729),
    "4H":  ("1h",  729),
    "1D":  ("1d",  1825),
}

# Min score por timeframe (más exigente en TF cortos)
MIN_SCORE_TF = {
    "M1":  72,
    "M5":  70,
    "M15": 68,
    "1H":  66,
    "4H":  64,
    "1D":  62,
}

# ─── DESCARGA BINANCE ─────────────────────────────────────────────────────────

def download_binance(symbol, interval, days):
    """Descarga datos históricos de Binance sin API key."""
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    limit    = 1000
    all_data = []
    current  = start_ms

    while current < end_ms:
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={symbol}&interval={interval}"
               f"&startTime={current}&limit={limit}")
        try:
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(url, timeout=10, context=ctx) as r:
                data = json.loads(r.read())
            if not data:
                break
            all_data.extend(data)
            current = data[-1][0] + 1
            if len(data) < limit:
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"ERROR Binance: {e}")
            break

    if not all_data:
        return None

    df = pd.DataFrame(all_data, columns=[
        "ts","open","high","low","close","volume",
        "close_ts","qav","trades","tbbav","tbqav","ignore"
    ])
    df["ts"]    = pd.to_datetime(df["ts"], unit="ms")
    df          = df.set_index("ts")
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col])
    return df[["open","high","low","close","volume"]].dropna()

# ─── DESCARGA YFINANCE ────────────────────────────────────────────────────────

def download_yfinance(ticker, tf_yf, days):
    end   = datetime.now()
    start = end - timedelta(days=days)
    try:
        df = yf.download(ticker, start=start, end=end,
                         interval=tf_yf, auto_adjust=True, progress=False)
        if df.empty:
            return None
        # Fix columnas tupla (yfinance moderno)
        if isinstance(df.columns[0], tuple):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        if "adj close" in df.columns:
            df = df.rename(columns={"adj close": "close"})
        df = df[["open","high","low","close","volume"]].dropna()
        return df
    except:
        return None

def resample_4h(df):
    """Agrupa datos 1h en velas 4h."""
    return df.resample("4h").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum"
    }).dropna()

# ─── INDICADORES ──────────────────────────────────────────────────────────────

def calc_rsi(df, period=14):
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(df):
    e12  = df["close"].ewm(span=12).mean()
    e26  = df["close"].ewm(span=26).mean()
    macd = e12 - e26
    return macd, macd.ewm(span=9).mean()

def calc_bollinger(df, period=20):
    ma  = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    return ma + 2*std, ma - 2*std

def calc_stochastic(df, k=14, d=3):
    lo   = df["low"].rolling(k).min()
    hi   = df["high"].rolling(k).max()
    rang = (hi - lo).replace(0, np.nan)
    pk   = 100 * (df["close"] - lo) / rang
    return pk, pk.rolling(d).mean()

def calc_atr(df, period=14):
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_ema(df, period):
    return df["close"].ewm(span=period).mean()

def prepare_indicators(df):
    df = df.copy()
    df["rsi"]              = calc_rsi(df)
    df["macd"], df["macd_sig"] = calc_macd(df)
    df["bb_up"], df["bb_lo"]   = calc_bollinger(df)
    df["stk_k"], df["stk_d"]   = calc_stochastic(df)
    df["atr"]   = calc_atr(df)
    df["ema9"]  = calc_ema(df, 9)
    df["ema21"] = calc_ema(df, 21)
    df["ema50"] = calc_ema(df, 50)
    df["ema200"]= calc_ema(df, 200)
    df["vol_ma"]= df["volume"].rolling(20).mean()
    # Regime vectorizado (rápido)
    df["regime"] = "range"
    bull_mask = (df["close"] > df["ema21"]) & (df["ema21"] > df["ema50"])
    bear_mask = (df["close"] < df["ema21"]) & (df["ema21"] < df["ema50"])
    df.loc[bull_mask, "regime"] = "bull"
    df.loc[bear_mask, "regime"] = "bear"
    # Volatilidad relativa vectorizada
    atr_roll = df["atr"].rolling(100).rank(pct=True)
    df["vol_tier"] = "medium"
    df.loc[atr_roll >= 0.75, "vol_tier"] = "high"
    df.loc[atr_roll <= 0.25, "vol_tier"] = "low"
    return df.dropna()

# ─── SCORING ──────────────────────────────────────────────────────────────────

def detect_regime(df, lookback=50):
    """Detecta régimen de mercado: bull / bear / range"""
    if len(df) < lookback:
        return 'range'
    close = df['close'].values
    highs = df['high'].values
    lows  = df['low'].values
    # HH/HL = bull, LH/LL = bear, resto = range
    hh = highs[-1] > highs[-lookback//2:].max() * 0.995
    hl = lows[-1]  > lows[-lookback:].min()  * 1.005
    lh = highs[-1] < highs[-lookback//2:].max() * 1.005
    ll = lows[-1]  < lows[-lookback:].max()  * 0.995
    ema_fast = df['ema21'].iloc[-1]
    ema_slow = df['ema50'].iloc[-1]
    price    = close[-1]
    if price > ema_fast > ema_slow and (hh or hl):
        return 'bull'
    elif price < ema_fast < ema_slow and (lh or ll):
        return 'bear'
    else:
        return 'range'

def atr_percentile(df, lookback=100):
    """Devuelve si la volatilidad actual es alta, media o baja vs historia"""
    if len(df) < lookback:
        return 'medium'
    current_atr = df['atr'].iloc[-1]
    hist_atr    = df['atr'].iloc[-lookback:]
    pct = (hist_atr < current_atr).sum() / len(hist_atr)
    if   pct >= 0.75: return 'high'
    elif pct <= 0.25: return 'low'
    else:             return 'medium'

def score_candle(row, asset_type="generic"):
    """
    Scoring calibrado por tipo de activo:
    - crypto : alta volatilidad, tendencias fuertes, filtro ATR
    - fx     : mercado de rango, RSI dominante, sin EMA200
    - index  : tendencia alcista estructural, solo BUY
    - commodity: volatilidad media, tendencia + reversión
    - generic: scoring estándar
    """
    close  = row.get("close", 1)
    rsi    = row.get("rsi", 50)
    macd   = row.get("macd", 0)
    macd_s = row.get("macd_sig", 0)
    bb_up  = row.get("bb_up", close*1.02)
    bb_lo  = row.get("bb_lo", close*0.98)
    ema9   = row.get("ema9",  close)
    ema21  = row.get("ema21", close)
    ema50  = row.get("ema50", close)
    ema200 = row.get("ema200", close)
    stk_k  = row.get("stk_k", 50)
    vol    = row.get("volume", 0)
    vol_ma = row.get("vol_ma", vol)
    atr    = row.get("atr", close * 0.01)
    atr_pct = atr / close * 100 if close > 0 else 1.0

    bull = 0
    bear = 0

    bb_range = bb_up - bb_lo if bb_up > bb_lo else close * 0.04
    bb_pos   = (close - bb_lo) / bb_range

    # ── CRYPTO ────────────────────────────────────────────────────────────────
    # Alta volatilidad → zonas RSI más extremas, MACD + volumen dominantes
    # Filtro ATR: solo operar si volatilidad es suficiente (evita consolidación)
    if asset_type == "crypto":
        regime   = row.get("regime", "range")
        vol_tier = row.get("vol_tier", "medium")

        if atr_pct < 0.2 or atr_pct > 15:
            return 50, "HOLD"

        if regime == "bull":
            if macd > macd_s and macd > 0: bull += 30
            if ema9 > ema21 > ema50:       bull += 25
            if rsi <= 45:                   bull += 20
            elif rsi <= 55:                 bull += 10
            if bb_pos <= 0.35:              bull += 15
            if vol > vol_ma * 1.5:          bull += 10
            bear = 0

        elif regime == "bear":
            if macd < macd_s and macd < 0: bear += 30
            if ema9 < ema21 < ema50:       bear += 25
            if rsi >= 55:                   bear += 20
            elif rsi >= 45:                 bear += 10
            if bb_pos >= 0.65:              bear += 15
            if vol > vol_ma * 1.5:          bear += 10
            bull = 0

        else:
            if   rsi <= 25: bull += 35
            elif rsi <= 35: bull += 20
            elif rsi >= 75: bear += 35
            elif rsi >= 65: bear += 20
            if   bb_pos <= 0.05: bull += 35
            elif bb_pos <= 0.15: bull += 18
            elif bb_pos >= 0.95: bear += 35
            elif bb_pos >= 0.85: bear += 18
            if   stk_k <= 15: bull += 20
            elif stk_k <= 25: bull += 10
            elif stk_k >= 85: bear += 20
            elif stk_k >= 75: bear += 10
            if vol > vol_ma * 1.8:
                bull += 10 if bull > bear else 0
                bear += 10 if bear > bull else 0
    elif asset_type == "fx":
        # RSI — zonas de reversión clásicas (peso 35)
        if   rsi <= 25: bull += 35
        elif rsi <= 35: bull += 20
        elif rsi <= 42: bull += 8
        elif rsi >= 75: bear += 35
        elif rsi >= 65: bear += 20
        elif rsi >= 58: bear += 8

        # Stochastic (peso 30) — más peso en FX
        if   stk_k <= 10: bull += 30
        elif stk_k <= 20: bull += 18
        elif stk_k <= 30: bull += 8
        elif stk_k >= 90: bear += 30
        elif stk_k >= 80: bear += 18
        elif stk_k >= 70: bear += 8

        # Bollinger reversión (peso 25)
        if   bb_pos <= 0.05: bull += 25
        elif bb_pos <= 0.15: bull += 12
        elif bb_pos >= 0.95: bear += 25
        elif bb_pos >= 0.85: bear += 12

        # MACD confirma (peso 10) — menos peso en rango
        if macd > macd_s: bull += 10
        else:             bear += 10

    # ── ÍNDICES ───────────────────────────────────────────────────────────────
    # Tendencia alcista estructural → solo BUY, seguir tendencia
    elif asset_type == "index":
        # Solo BUY — los índices tienen sesgo alcista de largo plazo
        # MACD tendencia (peso 35)
        if macd > macd_s:
            bull += 35 if macd > 0 else 18

        # EMA alcista (peso 30)
        if ema9 > ema21 > ema50:   bull += 30
        elif ema9 > ema21:          bull += 15

        # RSI — no sobrecomprado (peso 20)
        if   rsi <= 40: bull += 20
        elif rsi <= 50: bull += 10
        elif rsi >= 75: bull -= 15  # reducir convicción si sobrecomprado

        # Pullback a EMA (peso 15) — comprar en retroceso
        dist_ema21 = (close - ema21) / ema21 * 100
        if -2 <= dist_ema21 <= 0:  bull += 15  # tocando EMA21 desde arriba
        elif 0 < dist_ema21 <= 1:  bull += 8

        # Nunca señal SELL en índices
        bear = 0

    # ── COMMODITIES ───────────────────────────────────────────────────────────
    # Volatilidad media, mezcla tendencia + reversión
    elif asset_type == "commodity":
        # RSI (peso 28)
        if   rsi <= 28: bull += 28
        elif rsi <= 38: bull += 15
        elif rsi <= 45: bull += 6
        elif rsi >= 72: bear += 28
        elif rsi >= 62: bear += 15
        elif rsi >= 55: bear += 6

        # MACD (peso 22)
        if macd > macd_s:
            bull += 22 if macd > 0 else 11
        else:
            bear += 22 if macd < 0 else 11

        # EMA (peso 22)
        if ema9 > ema21 > ema50:   bull += 22
        elif ema9 > ema21:          bull += 11
        elif ema9 < ema21 < ema50: bear += 22
        elif ema9 < ema21:          bear += 11

        # Bollinger (peso 18)
        if   bb_pos <= 0.08: bull += 18
        elif bb_pos <= 0.2:  bull += 9
        elif bb_pos >= 0.92: bear += 18
        elif bb_pos >= 0.8:  bear += 9

        # Volumen (peso 10)
        if vol > vol_ma * 1.5:
            bull += 10 if bull > bear else 0
            bear += 10 if bear > bull else 0

    # ── GENÉRICO ──────────────────────────────────────────────────────────────
    else:
        if   rsi <= 25: bull += 25
        elif rsi <= 35: bull += 15
        elif rsi >= 75: bear += 25
        elif rsi >= 65: bear += 15

        if macd > macd_s: bull += 20
        else:             bear += 20

        if ema9 > ema21:  bull += 15
        else:             bear += 15

        if   bb_pos <= 0.1: bull += 20
        elif bb_pos >= 0.9: bear += 20

        if   stk_k <= 20: bull += 10
        elif stk_k >= 80: bear += 10

        if vol > vol_ma * 1.5:
            bull += 10 if bull > bear else 0
            bear += 10 if bear > bull else 0

    # Score final
    total = bull + bear
    score = round((bull / total * 100) if total > 0 else 50)
    score = max(0, min(100, score))

    # Umbrales por tipo
    if asset_type == "index":
        signal = "BUY" if score >= 55 else "HOLD"
    elif asset_type == "fx":
        if   score >= 60: signal = "BUY"
        elif score <= 40: signal = "SELL"
        else:             signal = "HOLD"
    elif asset_type == "crypto":
        if   score >= 65: signal = "BUY"
        elif score <= 35: signal = "SELL"
        else:             signal = "HOLD"
    else:
        if   score >= 62: signal = "BUY"
        elif score <= 38: signal = "SELL"
        else:             signal = "HOLD"

    return score, signal

# ─── BACKTEST ─────────────────────────────────────────────────────────────────

def run_backtest(df, capital=CAPITAL_INICIAL, risk_pct=RISK_PCT,
                 atr_mult=2.0, min_score=65, rr=2.0, asset_type="generic"):
    equity   = capital
    trades   = []
    curve    = [capital]
    in_trade = False
    entry = sl = tp = sig_dir = 0

    for i in range(50, len(df)):
        row   = df.iloc[i].to_dict()
        score, signal = score_candle(row, asset_type=asset_type)
        price = row["close"]
        atr   = row["atr"]

        if in_trade:
            pnl = 0; win = False; closed = False
            if sig_dir == "BUY":
                if price <= sl:
                    pnl = -(risk_pct/100) * equity; closed = True
                elif price >= tp:
                    pnl = (risk_pct/100) * equity * rr; win = True; closed = True
            else:
                if price >= sl:
                    pnl = -(risk_pct/100) * equity; closed = True
                elif price <= tp:
                    pnl = (risk_pct/100) * equity * rr; win = True; closed = True

            if closed:
                pnl *= (1 - COMISION - SLIPPAGE)
                equity += pnl
                trades.append({"pnl": round(pnl,2), "win": win,
                                "signal": sig_dir, "date": str(df.index[i])[:10]})
                curve.append(equity)
                in_trade = False

        if not in_trade and signal != "HOLD" and score >= min_score:
            ema50  = row.get("ema50",  price)
            ema200 = row.get("ema200", price)
            # Filtro tendencia: precio debe estar del lado correcto de EMA50 y EMA200
            if signal == "BUY"  and (price < ema50 or price < ema200): continue
            if signal == "SELL" and (price > ema50 or price > ema200): continue

            entry   = price * (1 + SLIPPAGE if signal=="BUY" else 1 - SLIPPAGE)
            sl_dist = atr * atr_mult
            sl      = entry - sl_dist if signal=="BUY" else entry + sl_dist
            tp      = entry + sl_dist*rr if signal=="BUY" else entry - sl_dist*rr
            sig_dir = signal
            in_trade = True

        if equity <= capital * 0.3:
            break

    return trades, curve

# ─── ESTADÍSTICAS ─────────────────────────────────────────────────────────────

def calc_stats(trades, capital, curve):
    if not trades:
        return None
    final_eq = curve[-1]
    wins = sum(1 for t in trades if t["win"])
    wr   = wins / len(trades) * 100
    tr   = (final_eq - capital) / capital * 100
    wP   = [t["pnl"] for t in trades if t["win"]]
    lP   = [t["pnl"] for t in trades if not t["win"]]
    avgW = sum(wP)/len(wP) if wP else 0
    avgL = sum(lP)/len(lP) if lP else 0
    pf   = abs(avgW*wins / (avgL*(len(trades)-wins))) if lP and avgL != 0 else 99

    peak = capital; maxDD = 0
    for v in curve:
        if v > peak: peak = v
        dd = (peak-v)/peak*100
        if dd > maxDD: maxDD = dd

    returns = [(curve[i]-curve[i-1])/curve[i-1] for i in range(1, len(curve))]
    mu  = np.mean(returns) if returns else 0
    std = np.std(returns)  if returns else 0
    sharpe = round((mu/std)*np.sqrt(252), 2) if std > 0 else 0

    return {
        "trades": len(trades), "wins": wins,
        "win_rate": round(wr, 1),
        "total_return": round(tr, 2),
        "final_equity": round(final_eq, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(maxDD, 1),
        "sharpe": sharpe,
    }

# ─── REPORTE ──────────────────────────────────────────────────────────────────

def estado(s):
    if s["win_rate"] >= 55 and s["total_return"] > 15 and s["profit_factor"] >= 1.3:
        return "✅ PRODUCCIÓN"
    elif s["win_rate"] >= 50 and s["total_return"] > 0:
        return "⚠️  AJUSTE"
    else:
        return "❌ NO LANZAR"

def print_row(nombre, tf, s):
    print(f"  {nombre:<6} [{tf:<3}]  "
          f"Trades:{s['trades']:>4}  WR:{s['win_rate']:>5.1f}%  "
          f"Return:{s['total_return']:>7.1f}%  "
          f"PF:{s['profit_factor']:>5.2f}  "
          f"DD:{s['max_drawdown']:>5.1f}%  "
          f"Sharpe:{s['sharpe']:>5.2f}  {estado(s)}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 85)
    print("  NEXUS APEX — BACKTEST PROFESIONAL COMPLETO")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  "
          f"Capital: ${CAPITAL_INICIAL:,.0f}  |  Riesgo: {RISK_PCT}%/trade")
    print("=" * 85)

    resultados = []

    # ── CRYPTO via Binance ────────────────────────────────────────────────────
    print("\n  CRYPTO (Binance API — datos reales)")
    print("  " + "─"*75)

    for nombre, symbol in CRYPTO.items():
        for tf_label, (interval, days, _) in BINANCE_TF.items():
            print(f"    {nombre} [{tf_label}] descargando...", end=" ", flush=True)
            df = download_binance(symbol, interval, days)
            if df is None or len(df) < 100:
                print("sin datos")
                continue
            df = prepare_indicators(df)
            min_sc = MIN_SCORE_TF[tf_label]
            trades, curve = run_backtest(df, min_score=min_sc, asset_type="crypto")
            s = calc_stats(trades, CAPITAL_INICIAL, curve)
            if s:
                print(f"OK ({len(df)} velas)")
                print_row(nombre, tf_label, s)
                resultados.append({"activo": nombre, "tf": tf_label, **s})
            else:
                print("sin trades")

    # ── EXTERNOS via yfinance ────────────────────────────────────────────────
    print("\n  EXTERNOS (yfinance — FX / Commodities / Indices)")
    print("  " + "─"*75)

    for nombre, ticker in EXTERNOS.items():
        for tf_label, (tf_yf, days) in YFINANCE_TF.items():
            print(f"    {nombre} [{tf_label}] descargando...", end=" ", flush=True)
            df = download_yfinance(ticker, tf_yf, days)
            if df is None or len(df) < 100:
                print("sin datos")
                continue
            if tf_label == "4H":
                df = resample_4h(df)
            if len(df) < 100:
                print("insuficiente tras resample")
                continue
            df = prepare_indicators(df)
            min_sc = MIN_SCORE_TF[tf_label]
            if nombre in ["EUR", "GBP"]: atype = "fx"
            elif nombre in ["SP500", "NAS"]: atype = "index"
            elif nombre in ["GOLD", "OIL"]: atype = "commodity"
            else: atype = "generic"
            trades, curve = run_backtest(df, min_score=min_sc, asset_type=atype)
            s = calc_stats(trades, CAPITAL_INICIAL, curve)
            if s:
                print(f"OK ({len(df)} velas)")
                print_row(nombre, tf_label, s)
                resultados.append({"activo": nombre, "tf": tf_label, **s})
            else:
                print("sin trades")

    # ── Resumen ───────────────────────────────────────────────────────────────
    print()
    print("=" * 85)
    print("  RESUMEN FINAL")
    print("=" * 85)

    if resultados:
        df_r = pd.DataFrame(resultados)
        listos = df_r[df_r["win_rate"] >= 55].sort_values("total_return", ascending=False)
        ajuste = df_r[(df_r["win_rate"] >= 50) & (df_r["win_rate"] < 55)]
        malos  = df_r[df_r["win_rate"] < 50]

        if not listos.empty:
            print(f"\n  ✅ LISTOS PARA PRODUCCIÓN ({len(listos)}):")
            for _, r in listos.iterrows():
                print(f"     {r.activo} [{r.tf}]  WR:{r.win_rate}%  "
                      f"Return:{r.total_return}%  Sharpe:{r.sharpe}  PF:{r.profit_factor}")

        if not ajuste.empty:
            print(f"\n  ⚠️  NECESITAN AJUSTE ({len(ajuste)}):")
            for _, r in ajuste.iterrows():
                print(f"     {r.activo} [{r.tf}]  WR:{r.win_rate}%  Return:{r.total_return}%")

        if not malos.empty:
            print(f"\n  ❌ NO LANZAR ({len(malos)}):")
            for _, r in malos.iterrows():
                print(f"     {r.activo} [{r.tf}]  WR:{r.win_rate}%")

        print(f"\n  Win Rate promedio (producción): "
              f"{listos.win_rate.mean():.1f}%" if not listos.empty else "")
        print(f"  Return promedio (producción):   "
              f"{listos.total_return.mean():.1f}%" if not listos.empty else "")

    print()
    print("=" * 85)
    print("  Completado — " + datetime.now().strftime("%H:%M:%S"))
    print("=" * 85)

if __name__ == "__main__":
    main()
