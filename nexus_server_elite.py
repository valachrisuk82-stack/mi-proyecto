"""
╔══════════════════════════════════════════════════════════════════╗
║   NEXUS PRO ULTIMATE — Servidor Completo                        ║
║   Binance + Claude AI + Telegram + Patrones + Multi-TF          ║
╚══════════════════════════════════════════════════════════════════╝

INSTALAR:
    pip3 install flask flask-cors anthropic requests pandas numpy

EJECUTAR:
    python3 nexus_server.py
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
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════
CONFIG = {
    "anthropic_api_key": "TU_API_KEY_AQUI",

    # Telegram — pon tu token y chat_id
    "telegram_token":  "8683659808:AAF241Fhd9yUmDcQsUgv1DfkM8CbckJ21zo",
    "telegram_chat_id": "8204656882",

    "capital":      1000.0,
    "risk_pct":     1.0,
    "kline_tf":     "5m",
    "kline_limit":  150,
    "refresh_sec":  30,
    "min_confidence_alert": 75,  # Confianza mínima para enviar alerta Telegram
}

PAIRS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT",
    "LINKUSDT","UNIUSDT","ATOMUSDT","LTCUSDT","ETCUSDT",
    "XLMUSDT","NEARUSDT","ALGOUSDT","FTMUSDT","SANDUSDT",
    "MANAUSDT","AAVEUSDT","SHIBUSDT","TRXUSDT",
]

BASE = "https://api.binance.com/api/v3"

# ══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════
def send_telegram(message: str):
    """Envía alerta a Telegram"""
    token = CONFIG["telegram_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if "PEGA_TU" in token or "PEGA_TU" in chat_id:
        print(f"[TELEGRAM] No configurado — mensaje: {message[:50]}")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
        print(f"[TELEGRAM ✓] Alerta enviada")
    except Exception as e:
        print(f"[TELEGRAM ✗] Error: {e}")

def format_telegram_alert(pair, signal, conf, entry, sl, tp, rr, reasoning):
    emoji = "🟢" if signal == "BUY" else "🔴" if signal == "SELL" else "⚪"
    return f"""
{emoji} <b>NEXUS PRO — {signal}</b>
━━━━━━━━━━━━━━━━━━━━
📊 Par: <b>{pair}</b>
💰 Entrada: <b>${entry:,.4f}</b>
🛑 Stop Loss: <b>${sl:,.4f}</b>
🎯 Take Profit: <b>${tp:,.4f}</b>
⚖️ R:R: <b>1:{rr}</b>
🎯 Confianza: <b>{conf}%</b>
━━━━━━━━━━━━━━━━━━━━
💬 {reasoning}
🕐 {datetime.now().strftime('%H:%M:%S')} London
"""

# ══════════════════════════════════════════════════════════════════
#  BINANCE API
# ══════════════════════════════════════════════════════════════════
def get_klines(symbol, interval, limit):
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
    try:
        syms = "[" + ",".join(f'"{p}"' for p in PAIRS) + "]"
        r = requests.get(f"{BASE}/ticker/24hr", params={"symbols": syms}, timeout=8)
        return {d["symbol"]: d for d in r.json()}
    except:
        return {}

def get_orderbook(symbol, limit=20):
    try:
        r = requests.get(f"{BASE}/depth", params={"symbol": symbol, "limit": limit}, timeout=5)
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
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except:
        return {"value": 50, "label": "Neutral"}

# ══════════════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════════
def calc_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 2)

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
    pos   = (price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]) * 100
    return {
        "upper":  round(float(upper.iloc[-1]), 6),
        "middle": round(float(sma.iloc[-1]), 6),
        "lower":  round(float(lower.iloc[-1]), 6),
        "width":  round(float(((upper - lower) / sma * 100).iloc[-1]), 2),
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

# ══════════════════════════════════════════════════════════════════
#  PATRONES DE VELAS JAPONESAS
# ══════════════════════════════════════════════════════════════════
def detect_patterns(df):
    patterns = []
    if len(df) < 3:
        return patterns

    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values

    i = len(df) - 1  # última vela

    body     = abs(c[i] - o[i])
    full_rng = h[i] - l[i]
    upper_wick = h[i] - max(c[i], o[i])
    lower_wick = min(c[i], o[i]) - l[i]

    # Doji
    if full_rng > 0 and body / full_rng < 0.1:
        patterns.append({"name": "DOJI", "type": "neutral", "desc": "Indecisión — posible reversión"})

    # Martillo (hammer) — alcista
    if (lower_wick > body * 2 and upper_wick < body * 0.5
            and c[i] > o[i] and full_rng > 0):
        patterns.append({"name": "MARTILLO", "type": "bull", "desc": "Señal alcista — posible rebote"})

    # Shooting star — bajista
    if (upper_wick > body * 2 and lower_wick < body * 0.5
            and c[i] < o[i] and full_rng > 0):
        patterns.append({"name": "SHOOTING STAR", "type": "bear", "desc": "Señal bajista — posible caída"})

    # Engulfing alcista
    if (i > 0 and c[i-1] < o[i-1] and c[i] > o[i]
            and c[i] > o[i-1] and o[i] < c[i-1]):
        patterns.append({"name": "ENGULFING ALCISTA", "type": "bull", "desc": "Vela envolvente — reversión al alza"})

    # Engulfing bajista
    if (i > 0 and c[i-1] > o[i-1] and c[i] < o[i]
            and c[i] < o[i-1] and o[i] > c[i-1]):
        patterns.append({"name": "ENGULFING BAJISTA", "type": "bear", "desc": "Vela envolvente — reversión a la baja"})

    # Morning star (3 velas)
    if (i >= 2 and c[i-2] < o[i-2]
            and abs(c[i-1] - o[i-1]) < (h[i-1] - l[i-1]) * 0.3
            and c[i] > o[i] and c[i] > (o[i-2] + c[i-2]) / 2):
        patterns.append({"name": "MORNING STAR", "type": "bull", "desc": "Patrón de 3 velas — reversión alcista fuerte"})

    # Evening star (3 velas)
    if (i >= 2 and c[i-2] > o[i-2]
            and abs(c[i-1] - o[i-1]) < (h[i-1] - l[i-1]) * 0.3
            and c[i] < o[i] and c[i] < (o[i-2] + c[i-2]) / 2):
        patterns.append({"name": "EVENING STAR", "type": "bear", "desc": "Patrón de 3 velas — reversión bajista fuerte"})

    # Marubozu alcista
    if (c[i] > o[i] and upper_wick < body * 0.05
            and lower_wick < body * 0.05 and body > 0):
        patterns.append({"name": "MARUBOZU ALCISTA", "type": "bull", "desc": "Vela sólida — presión compradora fuerte"})

    # Marubozu bajista
    if (c[i] < o[i] and upper_wick < body * 0.05
            and lower_wick < body * 0.05 and body > 0):
        patterns.append({"name": "MARUBOZU BAJISTA", "type": "bear", "desc": "Vela sólida — presión vendedora fuerte"})

    return patterns

# ══════════════════════════════════════════════════════════════════
#  SOPORTE Y RESISTENCIA
# ══════════════════════════════════════════════════════════════════
def calc_support_resistance(df, lookback=50):
    """Detecta niveles de soporte y resistencia por swing points"""
    if len(df) < lookback:
        return {"support": [], "resistance": []}

    highs  = df["high"].values[-lookback:]
    lows   = df["low"].values[-lookback:]
    closes = df["close"].values[-lookback:]
    price  = closes[-1]

    resistances = []
    supports    = []

    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            if highs[i] > price:
                resistances.append(round(float(highs[i]), 4))

        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            if lows[i] < price:
                supports.append(round(float(lows[i]), 4))

    # Tomar los 3 niveles más cercanos
    supports    = sorted(set(supports), reverse=True)[:3]
    resistances = sorted(set(resistances))[:3]

    return {"support": supports, "resistance": resistances}

# ══════════════════════════════════════════════════════════════════
#  ANÁLISIS MULTI-TIMEFRAME
# ══════════════════════════════════════════════════════════════════
def multi_timeframe_analysis(symbol):
    """Analiza el mismo par en 3 timeframes"""
    timeframes = {"1m": None, "5m": None, "15m": None}
    for tf in timeframes:
        df = get_klines(symbol, tf, 80)
        if not df.empty and len(df) >= 30:
            rsi  = calc_rsi(df)
            ema9 = calc_ema(df["close"], 9)
            ema21= calc_ema(df["close"], 21)
            macd = calc_macd(df)
            trend = "ALCISTA" if ema9 > ema21 else "BAJISTA"
            timeframes[tf] = {
                "rsi": rsi,
                "trend": trend,
                "macd_hist": macd["hist"],
                "ema_cross": ema9 > ema21,
            }

    # Confluencia — cuántos TF están de acuerdo
    bull_count = sum(1 for v in timeframes.values()
                     if v and v["ema_cross"] and v["rsi"] < 60)
    bear_count = sum(1 for v in timeframes.values()
                     if v and not v["ema_cross"] and v["rsi"] > 40)

    confluence = "ALCISTA FUERTE" if bull_count >= 2 else \
                 "BAJISTA FUERTE" if bear_count >= 2 else "NEUTRAL"

    return {"timeframes": timeframes, "confluence": confluence,
            "bull_count": bull_count, "bear_count": bear_count}

# ══════════════════════════════════════════════════════════════════
#  TODOS LOS INDICADORES
# ══════════════════════════════════════════════════════════════════
def calc_all_indicators(df):
    if len(df) < 30:
        return {}
    return {
        "rsi":      calc_rsi(df),
        "atr":      calc_atr(df),
        "ema9":     calc_ema(df["close"], 9),
        "ema21":    calc_ema(df["close"], 21),
        "ema50":    calc_ema(df["close"], 50),
        "macd":     calc_macd(df),
        "bb":       calc_bollinger(df),
        "stoch":    calc_stochastic(df),
        "vol":      calc_volume(df),
        "patterns": detect_patterns(df),
        "sr":       calc_support_resistance(df),
        "close":    round(float(df["close"].iloc[-1]), 6),
        "high":     round(float(df["high"].iloc[-1]), 6),
        "low":      round(float(df["low"].iloc[-1]), 6),
        "candles":  len(df),
    }

def compute_signal(ind):
    if not ind:
        return "WAIT", 0
    score = 0
    rsi   = ind.get("rsi", 50)
    hist  = ind.get("macd", {}).get("hist", 0)
    ema9  = ind.get("ema9", 0)
    ema21 = ind.get("ema21", 0)
    stk   = ind.get("stoch", {}).get("k", 50)
    bbp   = ind.get("bb", {}).get("pos", 50)

    if rsi < 35:   score += 2
    elif rsi > 65: score -= 2
    elif rsi < 45: score += 1
    elif rsi > 55: score -= 1

    if hist > 0: score += 1
    else:        score -= 1

    if ema9 > ema21: score += 1
    else:            score -= 1

    if stk < 25:   score += 1
    elif stk > 75: score -= 1

    if bbp < 20:   score += 1
    elif bbp > 80: score -= 1

    # Bonus por patrones
    patterns = ind.get("patterns", [])
    for p in patterns:
        if p["type"] == "bull": score += 1
        elif p["type"] == "bear": score -= 1

    if score >= 3:    return "BUY",  min(95, 55 + score * 7)
    elif score <= -3: return "SELL", min(95, 55 + abs(score) * 7)
    return "WAIT", 40

# ══════════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════════
cache = {
    "tickers":    {},
    "indicators": {},
    "signals":    {},
    "mtf":        {},
    "fgi":        {"value": 50, "label": "Neutral"},
    "signal_history": [],
    "last_update": None,
    "updating":   False,
    "last_alerts": {},  # Para no repetir alertas
}

def update_all_data():
    if cache["updating"]:
        return
    cache["updating"] = True
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Actualizando datos reales...")

    tickers = get_all_tickers()
    if tickers:
        cache["tickers"] = tickers

    cache["fgi"] = get_fear_greed()

    for pair in PAIRS:
        try:
            df = get_klines(pair, CONFIG["kline_tf"], CONFIG["kline_limit"])
            if not df.empty:
                ind = calc_all_indicators(df)
                cache["indicators"][pair] = ind
                signal, conf = compute_signal(ind)
                cache["signals"][pair] = {"signal": signal, "confidence": conf}
                print(f"  ✓ {pair}: RSI={ind.get('rsi','?')} → {signal} ({conf}%)")

                # Telegram alert
                if signal in ["BUY","SELL"] and conf >= CONFIG["min_confidence_alert"]:
                    last = cache["last_alerts"].get(pair, {})
                    if last.get("signal") != signal:
                        price = float(cache["tickers"].get(pair, {}).get("lastPrice", 0))
                        atr   = ind.get("atr", price * 0.012)
                        sl = price - atr*1.5 if signal=="BUY" else price + atr*1.5
                        tp = price + atr*3   if signal=="BUY" else price - atr*3
                        rr = 2.0
                        patterns = ind.get("patterns", [])
                        pattern_str = " | ".join(p["name"] for p in patterns) if patterns else "Sin patrón especial"
                        msg = format_telegram_alert(pair, signal, conf, price, sl, tp, rr, pattern_str)
                        send_telegram(msg)
                        cache["last_alerts"][pair] = {"signal": signal, "time": datetime.now()}

                        # Guardar en historial
                        cache["signal_history"].insert(0, {
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "pair": pair,
                            "signal": signal,
                            "confidence": conf,
                            "price": round(price, 4),
                        })
                        if len(cache["signal_history"]) > 50:
                            cache["signal_history"] = cache["signal_history"][:50]

            time.sleep(0.3)
        except Exception as e:
            print(f"  ✗ {pair}: {e}")

    cache["last_update"] = datetime.now().strftime("%H:%M:%S")
    cache["updating"] = False
    print(f"[OK] Actualización completa — {cache['last_update']}")

def background_updater():
    while True:
        update_all_data()
        time.sleep(CONFIG["refresh_sec"])

# ══════════════════════════════════════════════════════════════════
#  CLAUDE AI
# ══════════════════════════════════════════════════════════════════
def analyze_with_ai(pair):
    client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    ticker = cache["tickers"].get(pair, {})
    ind    = cache["indicators"].get(pair, {})
    fgi    = cache["fgi"]
    mtf    = cache.get("mtf", {}).get(pair, {})
    price  = float(ticker.get("lastPrice", 0))
    atr    = ind.get("atr", price * 0.012)
    capital = CONFIG["capital"]
    risk    = CONFIG["risk_pct"]
    ob      = get_orderbook(pair)
    patterns = ind.get("patterns", [])
    sr       = ind.get("sr", {})
    pattern_str = ", ".join(p["name"] for p in patterns) if patterns else "Ninguno detectado"
    mtf_str = ""
    if mtf and "timeframes" in mtf:
        for tf, data in mtf["timeframes"].items():
            if data:
                mtf_str += f"\n  {tf}: RSI={data['rsi']} Tendencia={data['trend']}"

    prompt = f"""Eres NEXUS PRO AI, sistema institucional de trading crypto para scalping profesional.
Responde ÚNICAMENTE con JSON válido, sin texto extra.

PAR: {pair} | PRECIO: ${price:,.6f}
CAMBIO 24H: {float(ticker.get('priceChangePercent', 0)):.2f}%
TIMEFRAME: {CONFIG['kline_tf']} | VELAS: {ind.get('candles', 0)}
F&G INDEX: {fgi['value']} ({fgi['label']})

INDICADORES REALES:
RSI(14): {ind.get('rsi','?')} | ATR: {atr}
EMA 9: {ind.get('ema9','?')} | EMA 21: {ind.get('ema21','?')} | EMA 50: {ind.get('ema50','?')}
MACD hist: {ind.get('macd',{}).get('hist','?')}
BB pos: {ind.get('bb',{}).get('pos','?')}% | ancho: {ind.get('bb',{}).get('width','?')}%
Estocástico K: {ind.get('stoch',{}).get('k','?')} D: {ind.get('stoch',{}).get('d','?')}
Volumen: {ind.get('vol',{}).get('label','?')}

PATRONES DE VELAS: {pattern_str}

SOPORTES: {sr.get('support', [])}
RESISTENCIAS: {sr.get('resistance', [])}

ORDERBOOK: Compradores {ob['bid_pct']}% | Vendedores {ob['ask_pct']}%

MULTI-TIMEFRAME:{mtf_str if mtf_str else ' No disponible'}
CONFLUENCIA: {mtf.get('confluence', 'N/A') if mtf else 'N/A'}

GESTIÓN RIESGO:
Capital: ${capital} | Riesgo: {risk}% (${capital*risk/100:.2f})
SL = 1.5x ATR | TP = 3x ATR

JSON requerido:
{{
  "signal": "BUY"|"SELL"|"WAIT",
  "confidence": 0-100,
  "entry": número,
  "sl": número,
  "tp": número,
  "rr": número,
  "lot": número,
  "trend": "ALCISTA"|"BAJISTA"|"LATERAL",
  "strength": "FUERTE"|"MODERADO"|"DÉBIL",
  "reasoning": "máximo 2 oraciones en español",
  "key_support": número,
  "key_resistance": número,
  "warnings": []
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
    result = json.loads(match.group())

    # Guardar en historial si es señal
    if result.get("signal") in ["BUY","SELL"]:
        cache["signal_history"].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "pair": pair,
            "signal": result["signal"],
            "confidence": result.get("confidence", 0),
            "price": result.get("entry", price),
            "sl": result.get("sl", 0),
            "tp": result.get("tp", 0),
            "source": "AI"
        })

    return result

# ══════════════════════════════════════════════════════════════════
#  FLASK
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
        "telegram": "PEGA_TU" not in CONFIG["telegram_token"],
    })

@app.route("/api/tickers")
def tickers():
    result = {}
    for pair in PAIRS:
        t = cache["tickers"].get(pair, {})
        s = cache["signals"].get(pair, {"signal": "SCAN", "confidence": 0})
        ind = cache["indicators"].get(pair, {})
        result[pair] = {
            "price":      float(t.get("lastPrice", 0)),
            "change":     float(t.get("priceChangePercent", 0)),
            "high":       float(t.get("highPrice", 0)),
            "low":        float(t.get("lowPrice", 0)),
            "volume":     float(t.get("volume", 0)),
            "signal":     s["signal"],
            "confidence": s["confidence"],
            "patterns":   ind.get("patterns", []),
            "rsi":        ind.get("rsi", 0),
        }
    return jsonify(result)

@app.route("/api/indicators/<symbol>")
def indicators(symbol):
    ind = cache["indicators"].get(symbol.upper(), {})
    sig = cache["signals"].get(symbol.upper(), {"signal": "SCAN", "confidence": 0})
    mtf = cache.get("mtf", {}).get(symbol.upper(), {})
    return jsonify({"indicators": ind, "signal": sig, "mtf": mtf})

@app.route("/api/klines/<symbol>")
def klines(symbol):
    tf    = request.args.get("tf", CONFIG["kline_tf"])
    limit = int(request.args.get("limit", 120))
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
        # Enviar alerta Telegram si confianza alta
        if result.get("signal") in ["BUY","SELL"] and result.get("confidence", 0) >= CONFIG["min_confidence_alert"]:
            msg = format_telegram_alert(
                symbol.upper(), result["signal"], result["confidence"],
                result.get("entry", 0), result.get("sl", 0), result.get("tp", 0),
                result.get("rr", 2), result.get("reasoning", "")
            )
            send_telegram(msg)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/mtf/<symbol>")
def mtf(symbol):
    try:
        result = multi_timeframe_analysis(symbol.upper())
        cache.setdefault("mtf", {})[symbol.upper()] = result
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/history")
def history():
    return jsonify(cache["signal_history"])

@app.route("/api/backtest/<symbol>")
def backtest(symbol):
    tf      = request.args.get("tf", "1h")
    limit   = int(request.args.get("limit", 300))
    capital = float(request.args.get("capital", 10000))
    risk    = float(request.args.get("risk", 1))
    df = get_klines(symbol.upper(), tf, limit)
    if df.empty:
        return jsonify({"ok": False, "error": "Sin datos"})
    trades, curve = run_backtest(df, capital, risk)
    stats = calc_stats(trades, capital, curve)
    return jsonify({"ok": True, "trades": trades[:50], "equity": curve, "stats": stats})

def run_backtest(df, capital, risk_pct):
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    trades, curve = [], [capital]
    equity = capital
    wins = losses = 0

    for i in range(30, len(df) - 1):
        sl = df.iloc[:i+1]
        if len(sl) < 20: continue
        ind = calc_all_indicators(sl)
        signal, conf = compute_signal(ind)
        if signal == "WAIT":
            curve.append(equity); continue

        entry = closes[i]
        atr   = ind["atr"]
        sl_p  = entry - atr*1.5 if signal=="BUY" else entry + atr*1.5
        tp_p  = entry + atr*3   if signal=="BUY" else entry - atr*3
        risk_amt = equity * risk_pct / 100
        sl_dist  = abs(entry - sl_p)
        units    = risk_amt / sl_dist if sl_dist > 0 else 0

        next_close = closes[min(i+1, len(closes)-1)]
        hit_tp = (signal=="BUY"  and next_close >= tp_p) or \
                 (signal=="SELL" and next_close <= tp_p)
        hit_sl = (signal=="BUY"  and next_close <= sl_p) or \
                 (signal=="SELL" and next_close >= sl_p)

        if hit_tp:   pnl = abs(tp_p - entry) * units;  wins += 1
        elif hit_sl: pnl = -abs(sl_p - entry) * units; losses += 1
        else:
            pnl = (next_close - entry) * units * (1 if signal=="BUY" else -1)
            if pnl > 0: wins += 1
            else: losses += 1

        equity += pnl
        curve.append(round(equity, 2))
        trades.append({
            "num": len(trades)+1, "signal": signal,
            "entry": round(entry, 4), "sl": round(sl_p, 4),
            "tp": round(tp_p, 4), "rr": "2.0",
            "pnl": round(pnl, 2), "win": pnl > 0,
        })
        if len(trades) >= 80: break

    return trades, curve

def calc_stats(trades, capital, curve):
    if not trades: return {}
    equity = curve[-1]
    wins   = sum(1 for t in trades if t["win"])
    losses = len(trades) - wins
    win_rate   = wins / len(trades) * 100
    total_ret  = (equity - capital) / capital * 100
    win_pnls   = [t["pnl"] for t in trades if t["win"]]
    loss_pnls  = [t["pnl"] for t in trades if not t["win"]]
    avg_win    = sum(win_pnls) / len(win_pnls)   if win_pnls  else 0
    avg_loss   = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    pf = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else 99
    peak = capital; max_dd = 0
    for v in curve:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd
    return {
        "total_return":  round(total_ret, 2),
        "final_equity":  round(equity, 2),
        "win_rate":      round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown":  round(max_dd, 1),
        "total_trades":  len(trades),
        "wins": wins, "losses": losses,
    }

# ══════════════════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "═"*55)
    print("  NEXUS PRO ULTIMATE — Servidor Completo")
    print("═"*55)
    print(f"  Capital:   ${CONFIG['capital']} USDT")
    print(f"  Riesgo:    {CONFIG['risk_pct']}%")
    print(f"  Timeframe: {CONFIG['kline_tf']}")
    print(f"  Pares:     {len(PAIRS)}")
    telegram_ok = "PEGA_TU" not in CONFIG["telegram_token"]
    print(f"  Telegram:  {'✅ ACTIVO' if telegram_ok else '⚠️  No configurado'}")
    print("═"*55)

    t = threading.Thread(target=update_all_data, daemon=True)
    t.start()
    bg = threading.Thread(target=background_updater, daemon=True)
    bg.start()

    print("\n  ✅ Servidor listo en http://localhost:5000")
    print("  → Abre nexus_dashboard.html en tu navegador\n")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
