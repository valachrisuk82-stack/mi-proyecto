"""
╔══════════════════════════════════════════════════════════════════╗
║   NEXUS PRO — Servidor Python con Datos 100% Reales             ║
║   Binance API + Indicadores Reales + Claude AI                  ║
╚══════════════════════════════════════════════════════════════════╝

INSTALAR (copia y pega en terminal):
    pip install flask flask-cors anthropic requests pandas numpy

EJECUTAR:
    python nexus_server.py

Luego abre nexus_dashboard.html en tu navegador.
El servidor corre en http://localhost:5000
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import anthropic
import requests
import pandas as pd
import numpy as np
import json
import time
import threading
from datetime import datetime

# ══════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN — EDITA SOLO ESTA SECCIÓN
# ══════════════════════════════════════════════════════════════════
CONFIG = {
    "anthropic_api_key": "sk-ant-PEGA-TU-KEY-AQUI",
    "capital":    1000.0,   # Tu capital en USDT
    "risk_pct":   1.0,      # % de riesgo por operación
    "kline_tf":   "5m",     # Timeframe: 1m, 3m, 5m, 15m
    "kline_limit": 150,     # Velas a descargar para calcular indicadores
    "refresh_sec": 30,      # Cada cuántos segundos actualizar indicadores
}

PAIRS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT",
    "LINKUSDT","UNIUSDT","ATOMUSDT","LTCUSDT","ETCUSDT",
    "XLMUSDT","NEARUSDT","ALGOUSDT","FTMUSDT","SANDUSDT",
    "MANAUSDT","AAVEUSDT","SHIBUSDT","TRXUSDT",
]

# ══════════════════════════════════════════════════════════════════
#  BINANCE API
# ══════════════════════════════════════════════════════════════════
BASE = "https://api.binance.com/api/v3"

def get_klines(symbol, interval, limit):
    """Descarga velas reales de Binance"""
    try:
        r = requests.get(f"{BASE}/klines", params={
            "symbol": symbol, "interval": interval, "limit": limit
        }, timeout=8)
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "close_time","quote_vol","trades","tb_base","tb_quote","ignore"
        ])
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
    except Exception as e:
        print(f"[ERROR] klines {symbol}: {e}")
        return pd.DataFrame()

def get_all_tickers():
    """Precios y stats 24h de todos los pares"""
    try:
        syms = "[" + ",".join(f'"{p}"' for p in PAIRS) + "]"
        r = requests.get(f"{BASE}/ticker/24hr",
            params={"symbols": syms}, timeout=8)
        return {d["symbol"]: d for d in r.json()}
    except Exception as e:
        print(f"[ERROR] tickers: {e}")
        return {}

def get_orderbook(symbol, limit=20):
    """Presión compradores vs vendedores"""
    try:
        r = requests.get(f"{BASE}/depth",
            params={"symbol": symbol, "limit": limit}, timeout=5)
        d = r.json()
        bids = sum(float(b[1]) for b in d["bids"])
        asks = sum(float(a[1]) for a in d["asks"])
        total = bids + asks or 1
        return {
            "bid_pct": round(bids / total * 100, 1),
            "ask_pct": round(asks / total * 100, 1),
            "pressure": "COMPRADORES" if bids > asks else "VENDEDORES"
        }
    except:
        return {"bid_pct": 50, "ask_pct": 50, "pressure": "NEUTRAL"}

def get_fear_greed():
    """Fear & Greed Index"""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except:
        return {"value": 50, "label": "Neutral"}

# ══════════════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS — 100% REALES
# ══════════════════════════════════════════════════════════════════
def calc_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)

def calc_atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return round(float(tr.rolling(period).mean().iloc[-1]), 6)

def calc_ema(series, period):
    return round(float(series.ewm(span=period, adjust=False).mean().iloc[-1]), 6)

def calc_macd(df):
    e12 = df["close"].ewm(span=12, adjust=False).mean()
    e26 = df["close"].ewm(span=26, adjust=False).mean()
    line   = e12 - e26
    signal = line.ewm(span=9, adjust=False).mean()
    hist   = line - signal
    return {
        "line":   round(float(line.iloc[-1]), 6),
        "signal": round(float(signal.iloc[-1]), 6),
        "hist":   round(float(hist.iloc[-1]), 6),
    }

def calc_bollinger(df, period=20):
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    price = df["close"].iloc[-1]
    width = (upper - lower) / sma * 100
    pos   = (price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]) * 100
    return {
        "upper":  round(float(upper.iloc[-1]), 6),
        "middle": round(float(sma.iloc[-1]), 6),
        "lower":  round(float(lower.iloc[-1]), 6),
        "width":  round(float(width.iloc[-1]), 2),
        "pos":    round(float(pos), 1),
    }

def calc_stochastic(df, k=14, d=3):
    low_min  = df["low"].rolling(k).min()
    high_max = df["high"].rolling(k).max()
    k_val = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-9)
    d_val = k_val.rolling(d).mean()
    return {
        "k": round(float(k_val.iloc[-1]), 2),
        "d": round(float(d_val.iloc[-1]), 2),
    }

def calc_volume(df):
    avg = df["volume"].rolling(20).mean().iloc[-1]
    cur = df["volume"].iloc[-1]
    ratio = round(cur / avg, 2) if avg > 0 else 1
    if ratio > 2.0:   label = "MUY ALTO"
    elif ratio > 1.3: label = "ALTO"
    elif ratio < 0.7: label = "BAJO"
    else:             label = "NORMAL"
    return {"ratio": ratio, "label": label}

def calc_all_indicators(df):
    """Calcula TODOS los indicadores con datos reales"""
    if len(df) < 30:
        return {}
    return {
        "rsi":   calc_rsi(df),
        "atr":   calc_atr(df),
        "ema9":  calc_ema(df["close"], 9),
        "ema21": calc_ema(df["close"], 21),
        "ema50": calc_ema(df["close"], 50),
        "macd":  calc_macd(df),
        "bb":    calc_bollinger(df),
        "stoch": calc_stochastic(df),
        "vol":   calc_volume(df),
        "close": round(float(df["close"].iloc[-1]), 6),
        "high":  round(float(df["high"].iloc[-1]), 6),
        "low":   round(float(df["low"].iloc[-1]), 6),
        "candles": len(df),
    }

def compute_signal(ind):
    """Calcula señal local sin IA"""
    if not ind:
        return "WAIT", 0
    score = 0
    rsi  = ind.get("rsi", 50)
    hist = ind.get("macd", {}).get("hist", 0)
    ema9 = ind.get("ema9", 0)
    ema21= ind.get("ema21", 0)
    stk  = ind.get("stoch", {}).get("k", 50)
    bbp  = ind.get("bb", {}).get("pos", 50)

    if rsi < 35:  score += 2
    elif rsi > 65: score -= 2
    elif rsi < 45: score += 1
    elif rsi > 55: score -= 1

    if hist > 0: score += 1
    else:        score -= 1

    if ema9 > ema21: score += 1
    else:            score -= 1

    if stk < 25:  score += 1
    elif stk > 75: score -= 1

    if bbp < 20:  score += 1
    elif bbp > 80: score -= 1

    if score >= 3:   return "BUY",  min(95, 55 + score * 8)
    elif score <= -3: return "SELL", min(95, 55 + abs(score) * 8)
    return "WAIT", 40

# ══════════════════════════════════════════════════════════════════
#  ESTADO GLOBAL — Cache de datos
# ══════════════════════════════════════════════════════════════════
cache = {
    "tickers":    {},
    "indicators": {},
    "signals":    {},
    "fgi":        {"value": 50, "label": "Neutral"},
    "last_update": None,
    "updating":   False,
}

def update_all_data():
    """Actualiza todos los datos en background"""
    if cache["updating"]:
        return
    cache["updating"] = True
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Actualizando datos reales...")

    # Precios
    tickers = get_all_tickers()
    if tickers:
        cache["tickers"] = tickers

    # Fear & Greed
    cache["fgi"] = get_fear_greed()

    # Indicadores reales por par
    for pair in PAIRS:
        try:
            df = get_klines(pair, CONFIG["kline_tf"], CONFIG["kline_limit"])
            if not df.empty:
                ind = calc_all_indicators(df)
                cache["indicators"][pair] = ind
                signal, conf = compute_signal(ind)
                cache["signals"][pair] = {"signal": signal, "confidence": conf}
                print(f"  ✓ {pair}: RSI={ind.get('rsi','?')} → {signal} ({conf}%)")
            time.sleep(0.3)  # Respetar rate limits de Binance
        except Exception as e:
            print(f"  ✗ {pair}: {e}")

    cache["last_update"] = datetime.now().strftime("%H:%M:%S")
    cache["updating"] = False
    print(f"[OK] Actualización completa — {cache['last_update']}")

def background_updater():
    """Loop que actualiza datos cada N segundos"""
    while True:
        update_all_data()
        time.sleep(CONFIG["refresh_sec"])

# ══════════════════════════════════════════════════════════════════
#  CLAUDE AI
# ══════════════════════════════════════════════════════════════════
def analyze_with_ai(pair):
    """Llama a Claude con datos reales del par"""
    client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])

    ticker = cache["tickers"].get(pair, {})
    ind    = cache["indicators"].get(pair, {})
    fgi    = cache["fgi"]
    price  = float(ticker.get("lastPrice", 0))
    atr    = ind.get("atr", price * 0.012)
    capital = CONFIG["capital"]
    risk    = CONFIG["risk_pct"]

    ob = get_orderbook(pair)

    prompt = f"""Eres NEXUS PRO AI, sistema institucional de análisis crypto para scalping.
Responde ÚNICAMENTE con JSON válido, sin texto adicional, sin markdown.

═══ DATOS DE MERCADO ═══
Par: {pair}
Precio actual: ${price:,.6f}
Cambio 24h: {float(ticker.get('priceChangePercent', 0)):.2f}%
High 24h: ${float(ticker.get('highPrice', price)):,.4f}
Low 24h: ${float(ticker.get('lowPrice', price)):,.4f}
Volumen 24h: {float(ticker.get('volume', 0)):,.0f}
Timeframe análisis: {CONFIG['kline_tf']}
Velas analizadas: {ind.get('candles', 0)} reales de Binance

═══ INDICADORES TÉCNICOS REALES ═══
RSI(14): {ind.get('rsi', '?')}
ATR(14): {atr}
EMA 9:  {ind.get('ema9', '?')}
EMA 21: {ind.get('ema21', '?')}
EMA 50: {ind.get('ema50', '?')}
MACD línea:  {ind.get('macd', {}).get('line', '?')}
MACD señal:  {ind.get('macd', {}).get('signal', '?')}
MACD histog: {ind.get('macd', {}).get('hist', '?')}
BB superior: {ind.get('bb', {}).get('upper', '?')}
BB inferior: {ind.get('bb', {}).get('lower', '?')}
BB posición: {ind.get('bb', {}).get('pos', '?')}%
BB ancho:    {ind.get('bb', {}).get('width', '?')}%
Estocástico K: {ind.get('stoch', {}).get('k', '?')}
Estocástico D: {ind.get('stoch', {}).get('d', '?')}
Volumen:     {ind.get('vol', {}).get('label', '?')} (ratio {ind.get('vol', {}).get('ratio', '?')}x)

═══ ORDERBOOK ═══
Compradores: {ob['bid_pct']}%
Vendedores:  {ob['ask_pct']}%
Presión:     {ob['pressure']}

═══ SENTIMIENTO ═══
Fear & Greed Index: {fgi['value']} ({fgi['label']})

═══ GESTIÓN DE RIESGO ═══
Capital: ${capital} USDT
Riesgo por operación: {risk}% (${capital * risk / 100:.2f} USDT)
Fórmula SL: 1.5 × ATR desde entrada
Fórmula TP: 3.0 × ATR desde entrada (mínimo R:R 2:1)
Lot size: capital_riesgo / distancia_SL

Responde con este JSON exacto:
{{
  "signal": "BUY" o "SELL" o "WAIT",
  "confidence": número 0-100,
  "entry": número,
  "sl": número,
  "tp": número,
  "rr": número,
  "lot": número,
  "trend": "ALCISTA" o "BAJISTA" o "LATERAL",
  "strength": "FUERTE" o "MODERADO" o "DÉBIL",
  "reasoning": "explicación en 2 oraciones máximo en español",
  "key_support": número,
  "key_resistance": número,
  "warnings": ["advertencia si hay algo importante"]
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    clean = raw.replace("```json","").replace("```","").strip()

    import re
    match = re.search(r"\{[\s\S]*\}", clean)
    if not match:
        raise ValueError("No JSON encontrado")
    return json.loads(match.group())

# ══════════════════════════════════════════════════════════════════
#  FLASK SERVER
# ══════════════════════════════════════════════════════════════════
app = Flask(__name__)
CORS(app)

@app.route("/api/status")
def status():
    return jsonify({
        "ok": True,
        "last_update": cache["last_update"],
        "updating": cache["updating"],
        "pairs": len(PAIRS),
        "fgi": cache["fgi"],
    })

@app.route("/api/tickers")
def tickers():
    result = {}
    for pair in PAIRS:
        t = cache["tickers"].get(pair, {})
        s = cache["signals"].get(pair, {"signal": "SCAN", "confidence": 0})
        result[pair] = {
            "price":      float(t.get("lastPrice", 0)),
            "change":     float(t.get("priceChangePercent", 0)),
            "high":       float(t.get("highPrice", 0)),
            "low":        float(t.get("lowPrice", 0)),
            "volume":     float(t.get("volume", 0)),
            "signal":     s["signal"],
            "confidence": s["confidence"],
        }
    return jsonify(result)

@app.route("/api/indicators/<symbol>")
def indicators(symbol):
    ind = cache["indicators"].get(symbol.upper(), {})
    sig = cache["signals"].get(symbol.upper(), {"signal": "SCAN", "confidence": 0})
    return jsonify({"indicators": ind, "signal": sig})

@app.route("/api/klines/<symbol>")
def klines(symbol):
    tf    = request.args.get("tf", CONFIG["kline_tf"])
    limit = int(request.args.get("limit", 80))
    df = get_klines(symbol.upper(), tf, limit)
    if df.empty:
        return jsonify([])
    return jsonify(df[["time","open","high","low","close","volume"]].assign(
        time=df["time"].astype(str)
    ).to_dict(orient="records"))

@app.route("/api/analyze/<symbol>", methods=["POST"])
def analyze(symbol):
    try:
        result = analyze_with_ai(symbol.upper())
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/backtest/<symbol>")
def backtest(symbol):
    tf      = request.args.get("tf", "1h")
    limit   = int(request.args.get("limit", 300))
    capital = float(request.args.get("capital", 10000))
    risk    = float(request.args.get("risk", 1))

    df = get_klines(symbol.upper(), tf, limit)
    if df.empty:
        return jsonify({"ok": False, "error": "Sin datos"})

    trades, equity_curve = run_backtest_strategy(df, capital, risk)
    stats = calc_backtest_stats(trades, capital, equity_curve)
    return jsonify({"ok": True, "trades": trades[:50], "equity": equity_curve, "stats": stats})

def run_backtest_strategy(df, capital, risk_pct):
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    trades, curve = [], [capital]
    equity = capital
    wins = losses = 0

    for i in range(30, len(df) - 1):
        # Calcular indicadores en slice
        sl = df.iloc[:i+1]
        if len(sl) < 20:
            continue
        ind = calc_all_indicators(sl)
        signal, conf = compute_signal(ind)
        if signal == "WAIT":
            curve.append(equity)
            continue

        entry = closes[i]
        atr   = ind["atr"]
        sl_p  = entry - atr * 1.5 if signal == "BUY" else entry + atr * 1.5
        tp_p  = entry + atr * 3.0 if signal == "BUY" else entry - atr * 3.0
        risk_amt = equity * risk_pct / 100
        sl_dist  = abs(entry - sl_p)
        units    = risk_amt / sl_dist if sl_dist > 0 else 0

        # Simular resultado (basado en histórico real)
        next_close = closes[min(i+1, len(closes)-1)]
        hit_tp = (signal=="BUY"  and next_close >= tp_p) or \
                 (signal=="SELL" and next_close <= tp_p)
        hit_sl = (signal=="BUY"  and next_close <= sl_p) or \
                 (signal=="SELL" and next_close >= sl_p)

        if hit_tp:
            pnl = abs(tp_p - entry) * units; wins += 1
        elif hit_sl:
            pnl = -abs(sl_p - entry) * units; losses += 1
        else:
            pnl = (next_close - entry) * units * (1 if signal=="BUY" else -1)
            if pnl > 0: wins += 1
            else: losses += 1

        equity += pnl
        curve.append(round(equity, 2))
        trades.append({
            "num": len(trades)+1,
            "signal": signal,
            "entry": round(entry, 4),
            "sl": round(sl_p, 4),
            "tp": round(tp_p, 4),
            "rr": "2.0",
            "pnl": round(pnl, 2),
            "win": pnl > 0,
        })
        if len(trades) >= 80:
            break

    return trades, curve

def calc_backtest_stats(trades, capital, curve):
    if not trades:
        return {}
    equity = curve[-1]
    wins   = sum(1 for t in trades if t["win"])
    losses = len(trades) - wins
    win_rate = wins / len(trades) * 100
    total_ret = (equity - capital) / capital * 100
    win_pnls  = [t["pnl"] for t in trades if t["win"]]
    loss_pnls = [t["pnl"] for t in trades if not t["win"]]
    avg_win   = sum(win_pnls) / len(win_pnls)   if win_pnls  else 0
    avg_loss  = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    pf = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else 99

    peak = capital
    max_dd = 0
    for v in curve:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    return {
        "total_return": round(total_ret, 2),
        "final_equity": round(equity, 2),
        "win_rate":     round(win_rate, 1),
        "profit_factor":round(pf, 2),
        "max_drawdown": round(max_dd, 1),
        "total_trades": len(trades),
        "wins":  wins,
        "losses":losses,
    }

# ══════════════════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "═"*55)
    print("  NEXUS PRO — Servidor de Datos Reales")
    print("═"*55)
    print(f"  Capital:  ${CONFIG['capital']} USDT")
    print(f"  Riesgo:   {CONFIG['risk_pct']}%")
    print(f"  Timeframe:{CONFIG['kline_tf']}")
    print(f"  Pares:    {len(PAIRS)}")
    print("═"*55)
    print("\n  Descargando datos iniciales...\n")

    # Primera carga en hilo separado
    t = threading.Thread(target=update_all_data, daemon=True)
    t.start()

    # Actualizador automático en background
    bg = threading.Thread(target=background_updater, daemon=True)
    bg.start()

    print("  ✅ Servidor listo en http://localhost:5000")
    print("  → Abre nexus_dashboard.html en tu navegador\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
