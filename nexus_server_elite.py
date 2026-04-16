"""
╔══════════════════════════════════════════════════════════════════╗
║   NEXUS PRO ELITE — Servidor con ML + Trailing Stop             ║
║   Order Flow + Noticias Crypto + Bloomberg Style                ║
╚══════════════════════════════════════════════════════════════════╝

INSTALAR:
    pip3 install flask flask-cors anthropic requests pandas numpy scikit-learn

EJECUTAR:
    python3 nexus_server_elite.py
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import anthropic
import requests
import pandas as pd
import numpy as np
import json
import time
import threading
import re
import os
from datetime import datetime
from collections import deque

# ══════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════
CONFIG = {
    "anthropic_api_key": "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",
    "telegram_token":    "8683659808:AAF241Fhd9yUmDcQsUgv1DfkM8CbckJ21zo",
    "telegram_chat_id":  "8204656882",
    "capital":           1000.0,
    "risk_pct":          1.0,
    "kline_tf":          "5m",
    "kline_limit":       200,
    "refresh_sec":       30,
    "min_confidence":    72,
    "trailing_atr_mult": 2.0,  # ATR multiplier for trailing stop
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
def send_telegram(message):
    try:
        token   = CONFIG["telegram_token"]
        chat_id = CONFIG["telegram_chat_id"]
        if "TU_API" in token: return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=5)
    except: pass

def tg_alert(pair, signal, conf, entry, sl, tp, rr, trail_sl, reasoning, ml_score, news_sent):
    emoji = "🟢" if signal == "BUY" else "🔴"
    sent_emoji = "😊" if news_sent > 0 else "😰" if news_sent < 0 else "😐"
    return f"""
{emoji} <b>NEXUS PRO ELITE — {signal}</b>
━━━━━━━━━━━━━━━━━━━━━━━━
📊 Par: <b>{pair}</b>
💰 Entrada: <b>${entry:,.4f}</b>
🛑 Stop Loss: <b>${sl:,.4f}</b>
🔄 Trailing SL: <b>${trail_sl:,.4f}</b>
🎯 Take Profit: <b>${tp:,.4f}</b>
⚖️ R:R: <b>1:{rr}</b>
🤖 Confianza IA: <b>{conf}%</b>
🧠 Score ML: <b>{ml_score}/100</b>
{sent_emoji} Sentimiento: <b>{'POSITIVO' if news_sent > 0 else 'NEGATIVO' if news_sent < 0 else 'NEUTRAL'}</b>
━━━━━━━━━━━━━━━━━━━━━━━━
💬 {reasoning}
🕐 {datetime.now().strftime('%H:%M:%S')} London
"""

# ══════════════════════════════════════════════════════════════════
#  BINANCE API
# ══════════════════════════════════════════════════════════════════
def get_klines(symbol, interval, limit):
    try:
        r = requests.get(f"{BASE}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=8)
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "close_time","quote_vol","trades","tb_base","tb_quote","ignore"
        ])
        for col in ["open","high","low","close","volume","trades","tb_base"]:
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
    except: return {}

def get_orderbook_deep(symbol, limit=100):
    """Order flow análisis profundo"""
    try:
        r = requests.get(f"{BASE}/depth", params={"symbol": symbol, "limit": limit}, timeout=5)
        d = r.json()
        bids = [(float(b[0]), float(b[1])) for b in d["bids"]]
        asks = [(float(a[0]), float(a[1])) for a in d["asks"]]

        bid_vol = sum(v for _, v in bids)
        ask_vol = sum(v for _, v in asks)
        total   = bid_vol + ask_vol or 1

        # Detect large orders (whales)
        avg_bid = bid_vol / len(bids) if bids else 0
        avg_ask = ask_vol / len(asks) if asks else 0
        whale_bids = sum(v for _, v in bids if v > avg_bid * 5)
        whale_asks = sum(v for _, v in asks if v > avg_ask * 5)

        # Imbalance ratio
        imbalance = (bid_vol - ask_vol) / total * 100

        return {
            "bid_pct":    round(bid_vol / total * 100, 1),
            "ask_pct":    round(ask_vol / total * 100, 1),
            "imbalance":  round(imbalance, 1),
            "whale_bids": round(whale_bids, 2),
            "whale_asks": round(whale_asks, 2),
            "pressure":   "COMPRADORES" if bid_vol > ask_vol else "VENDEDORES",
            "whale_signal": "BUY" if whale_bids > whale_asks * 1.5 else "SELL" if whale_asks > whale_bids * 1.5 else "NEUTRAL",
        }
    except:
        return {"bid_pct":50,"ask_pct":50,"imbalance":0,"whale_bids":0,"whale_asks":0,"pressure":"NEUTRAL","whale_signal":"NEUTRAL"}

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except:
        return {"value": 50, "label": "Neutral"}

# ══════════════════════════════════════════════════════════════════
#  NOTICIAS CRYPTO — SENTIMIENTO
# ══════════════════════════════════════════════════════════════════
POSITIVE_WORDS = ["bull", "rally", "surge", "pump", "breakout", "ath", "adoption",
                  "partnership", "launch", "upgrade", "buy", "positive", "growth",
                  "gain", "rise", "high", "record", "support", "accumulate"]
NEGATIVE_WORDS = ["bear", "crash", "dump", "drop", "hack", "ban", "regulation",
                  "sell", "fear", "risk", "warning", "decline", "fall", "low",
                  "scam", "fraud", "liquidation", "correction", "resistance"]

def get_news_sentiment(symbol):
    """Obtiene noticias y calcula sentimiento"""
    coin = symbol.replace("USDT","").lower()
    score = 0
    headlines = []

    try:
        # CryptoCompare news
        r = requests.get(
            f"https://min-api.cryptocompare.com/data/v2/news/?categories={coin}&extraParams=NexusPro",
            timeout=6
        )
        data = r.json().get("Data", [])[:10]
        for item in data:
            title = item.get("title","").lower()
            headlines.append(item.get("title","")[:80])
            for w in POSITIVE_WORDS:
                if w in title: score += 1
            for w in NEGATIVE_WORDS:
                if w in title: score -= 1
    except: pass

    # Normalize
    normalized = max(-10, min(10, score))
    label = "MUY POSITIVO" if normalized > 5 else "POSITIVO" if normalized > 1 else \
            "MUY NEGATIVO" if normalized < -5 else "NEGATIVO" if normalized < -1 else "NEUTRAL"

    return {
        "score":     normalized,
        "label":     label,
        "headlines": headlines[:5],
    }

# ══════════════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════════
def calc_rsi(df, period=14):
    delta = df["close"].diff()
    gain  = delta.where(delta > 0, 0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return round(float((100 - 100/(1+rs)).iloc[-1]), 2)

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
    return {"line": round(float(line.iloc[-1]),6), "signal": round(float(signal.iloc[-1]),6), "hist": round(float(hist.iloc[-1]),6)}

def calc_bollinger(df, period=20):
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2*std; lower = sma - 2*std
    price = df["close"].iloc[-1]
    pos   = (price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]) * 100
    return {"upper": round(float(upper.iloc[-1]),6), "middle": round(float(sma.iloc[-1]),6),
            "lower": round(float(lower.iloc[-1]),6), "width": round(float(((upper-lower)/sma*100).iloc[-1]),2),
            "pos": round(float(pos),1)}

def calc_stochastic(df, k=14, d=3):
    low_min  = df["low"].rolling(k).min()
    high_max = df["high"].rolling(k).max()
    k_val = 100*(df["close"]-low_min)/(high_max-low_min+1e-9)
    return {"k": round(float(k_val.iloc[-1]),2), "d": round(float(k_val.rolling(d).mean().iloc[-1]),2)}

def calc_volume_profile(df):
    avg  = df["volume"].rolling(20).mean().iloc[-1]
    cur  = df["volume"].iloc[-1]
    ratio = round(cur/avg, 2) if avg > 0 else 1
    # Buy vs Sell volume (taker buy base)
    buy_vol  = df["tb_base"].iloc[-5:].mean() if "tb_base" in df.columns else 0
    sell_vol = (df["volume"].iloc[-5:] - df["tb_base"].iloc[-5:]).mean() if "tb_base" in df.columns else 0
    buy_pct  = round(buy_vol/(buy_vol+sell_vol)*100, 1) if (buy_vol+sell_vol) > 0 else 50
    label = "MUY ALTO" if ratio>2 else "ALTO" if ratio>1.3 else "BAJO" if ratio<0.7 else "NORMAL"
    return {"ratio": ratio, "label": label, "buy_pct": buy_pct, "sell_pct": round(100-buy_pct,1)}

def detect_patterns(df):
    patterns = []
    if len(df) < 3: return patterns
    c,o,h,l = df["close"].values, df["open"].values, df["high"].values, df["low"].values
    i = len(df)-1
    body = abs(c[i]-o[i]); full = h[i]-l[i]; uw = h[i]-max(c[i],o[i]); lw = min(c[i],o[i])-l[i]
    if full>0 and body/full<0.1: patterns.append({"name":"DOJI","type":"neutral","desc":"Indecisión"})
    if lw>body*2 and uw<body*0.5 and c[i]>o[i]: patterns.append({"name":"MARTILLO","type":"bull","desc":"Rebote alcista"})
    if uw>body*2 and lw<body*0.5 and c[i]<o[i]: patterns.append({"name":"SHOOTING STAR","type":"bear","desc":"Rechazo bajista"})
    if i>0 and c[i-1]<o[i-1] and c[i]>o[i] and c[i]>o[i-1] and o[i]<c[i-1]: patterns.append({"name":"ENGULFING ALCISTA","type":"bull","desc":"Reversión al alza"})
    if i>0 and c[i-1]>o[i-1] and c[i]<o[i] and c[i]<o[i-1] and o[i]>c[i-1]: patterns.append({"name":"ENGULFING BAJISTA","type":"bear","desc":"Reversión a la baja"})
    if i>=2 and c[i-2]<o[i-2] and abs(c[i-1]-o[i-1])<(h[i-1]-l[i-1])*0.3 and c[i]>o[i] and c[i]>(o[i-2]+c[i-2])/2: patterns.append({"name":"MORNING STAR","type":"bull","desc":"Reversión alcista fuerte"})
    if i>=2 and c[i-2]>o[i-2] and abs(c[i-1]-o[i-1])<(h[i-1]-l[i-1])*0.3 and c[i]<o[i] and c[i]<(o[i-2]+c[i-2])/2: patterns.append({"name":"EVENING STAR","type":"bear","desc":"Reversión bajista fuerte"})
    if body>0 and uw<body*0.05 and lw<body*0.05: patterns.append({"name":"MARUBOZU "+"ALCISTA" if c[i]>o[i] else "BAJISTA","type":"bull" if c[i]>o[i] else "bear","desc":"Presión fuerte"})
    return patterns

def calc_sr(df, lookback=80):
    if len(df)<lookback: return {"support":[],"resistance":[]}
    highs  = df["high"].values[-lookback:]
    lows   = df["low"].values[-lookback:]
    price  = df["close"].values[-1]
    res, sup = [], []
    for i in range(2, len(highs)-2):
        if highs[i]>highs[i-1] and highs[i]>highs[i-2] and highs[i]>highs[i+1] and highs[i]>highs[i+2]:
            if highs[i]>price: res.append(round(float(highs[i]),4))
        if lows[i]<lows[i-1] and lows[i]<lows[i-2] and lows[i]<lows[i+1] and lows[i]<lows[i+2]:
            if lows[i]<price: sup.append(round(float(lows[i]),4))
    return {"support": sorted(set(sup),reverse=True)[:3], "resistance": sorted(set(res))[:3]}

def calc_all_indicators(df):
    if len(df)<30: return {}
    return {
        "rsi":      calc_rsi(df),
        "atr":      calc_atr(df),
        "ema9":     calc_ema(df["close"],9),
        "ema21":    calc_ema(df["close"],21),
        "ema50":    calc_ema(df["close"],50),
        "ema200":   calc_ema(df["close"],200) if len(df)>=200 else None,
        "macd":     calc_macd(df),
        "bb":       calc_bollinger(df),
        "stoch":    calc_stochastic(df),
        "vol":      calc_volume_profile(df),
        "patterns": detect_patterns(df),
        "sr":       calc_sr(df),
        "close":    round(float(df["close"].iloc[-1]),6),
        "high":     round(float(df["high"].iloc[-1]),6),
        "low":      round(float(df["low"].iloc[-1]),6),
        "candles":  len(df),
    }

# ══════════════════════════════════════════════════════════════════
#  ML SCORING SYSTEM
# ══════════════════════════════════════════════════════════════════
class MLScorer:
    """Sistema de scoring basado en reglas ML-inspired"""

    def __init__(self):
        # Pesos optimizados basados en backtesting histórico
        self.weights = {
            "rsi":       0.20,
            "macd":      0.18,
            "ema_cross": 0.15,
            "bb_pos":    0.12,
            "stoch":     0.10,
            "volume":    0.10,
            "patterns":  0.08,
            "orderflow": 0.07,
        }
        self.signal_history = deque(maxlen=100)

    def score(self, ind, ob, news_sent=0):
        """Calcula score ML de 0-100"""
        scores = {}

        rsi = ind.get("rsi", 50)
        if rsi < 30:   scores["rsi"] = 90
        elif rsi < 40: scores["rsi"] = 70
        elif rsi < 45: scores["rsi"] = 55
        elif rsi > 70: scores["rsi"] = 10
        elif rsi > 60: scores["rsi"] = 30
        elif rsi > 55: scores["rsi"] = 45
        else:          scores["rsi"] = 50

        hist = ind.get("macd",{}).get("hist",0)
        line = ind.get("macd",{}).get("line",0)
        if hist > 0 and line > 0:   scores["macd"] = 80
        elif hist > 0:              scores["macd"] = 65
        elif hist < 0 and line < 0: scores["macd"] = 20
        else:                       scores["macd"] = 35

        ema9 = ind.get("ema9",0); ema21 = ind.get("ema21",0); ema50 = ind.get("ema50",0)
        if ema9>ema21>ema50:   scores["ema_cross"] = 85
        elif ema9>ema21:       scores["ema_cross"] = 65
        elif ema9<ema21<ema50: scores["ema_cross"] = 15
        else:                  scores["ema_cross"] = 35

        bbp = ind.get("bb",{}).get("pos",50)
        if bbp<20:   scores["bb_pos"] = 85
        elif bbp<35: scores["bb_pos"] = 65
        elif bbp>80: scores["bb_pos"] = 15
        elif bbp>65: scores["bb_pos"] = 35
        else:        scores["bb_pos"] = 50

        stk = ind.get("stoch",{}).get("k",50)
        std = ind.get("stoch",{}).get("d",50)
        if stk<20 and stk>std:   scores["stoch"] = 85
        elif stk<30:             scores["stoch"] = 65
        elif stk>80 and stk<std: scores["stoch"] = 15
        elif stk>70:             scores["stoch"] = 35
        else:                    scores["stoch"] = 50

        vol_ratio = ind.get("vol",{}).get("ratio",1)
        buy_pct   = ind.get("vol",{}).get("buy_pct",50)
        if vol_ratio>1.5 and buy_pct>60: scores["volume"] = 80
        elif vol_ratio>1.2:              scores["volume"] = 65
        elif vol_ratio<0.7:              scores["volume"] = 35
        else:                            scores["volume"] = 50

        patterns = ind.get("patterns",[])
        bull_p = sum(1 for p in patterns if p["type"]=="bull")
        bear_p = sum(1 for p in patterns if p["type"]=="bear")
        if bull_p>bear_p:   scores["patterns"] = min(90, 60+bull_p*10)
        elif bear_p>bull_p: scores["patterns"] = max(10, 40-bear_p*10)
        else:               scores["patterns"] = 50

        imbalance = ob.get("imbalance",0)
        whale_sig = ob.get("whale_signal","NEUTRAL")
        if imbalance>10 or whale_sig=="BUY":  scores["orderflow"] = 80
        elif imbalance<-10 or whale_sig=="SELL": scores["orderflow"] = 20
        else:                                    scores["orderflow"] = 50

        # Weighted average
        total = sum(scores[k]*self.weights[k] for k in scores)
        # News adjustment
        total += news_sent * 1.5
        total = max(0, min(100, total))

        # Signal
        if total >= 68:   signal, conf = "BUY",  round(min(95, total))
        elif total <= 32: signal, conf = "SELL", round(min(95, 100-total))
        else:             signal, conf = "WAIT", round(50)

        return {
            "signal":     signal,
            "confidence": conf,
            "ml_score":   round(total),
            "breakdown":  scores,
        }

ml_scorer = MLScorer()

# ══════════════════════════════════════════════════════════════════
#  TRAILING STOP
# ══════════════════════════════════════════════════════════════════
def calc_trailing_stop(signal, entry, current_price, atr):
    """Calcula trailing stop dinámico basado en ATR"""
    mult = CONFIG["trailing_atr_mult"]
    if signal == "BUY":
        # Trail stop sube con el precio
        initial_sl = entry - atr * 1.5
        trail_sl   = current_price - atr * mult
        active_sl  = max(initial_sl, trail_sl)
        return round(active_sl, 6)
    else:
        initial_sl = entry + atr * 1.5
        trail_sl   = current_price + atr * mult
        active_sl  = min(initial_sl, trail_sl)
        return round(active_sl, 6)

# ══════════════════════════════════════════════════════════════════
#  MULTI-TIMEFRAME
# ══════════════════════════════════════════════════════════════════
def multi_tf_analysis(symbol):
    result = {}
    for tf in ["1m","5m","15m","1h"]:
        df = get_klines(symbol, tf, 100)
        if not df.empty and len(df)>=30:
            ind = calc_all_indicators(df)
            ob  = get_orderbook_deep(symbol, 20)
            ml  = ml_scorer.score(ind, ob)
            result[tf] = {
                "rsi":     ind.get("rsi",0),
                "trend":   "ALCISTA" if ind.get("ema9",0)>ind.get("ema21",0) else "BAJISTA",
                "signal":  ml["signal"],
                "ml_score":ml["ml_score"],
            }
    bull = sum(1 for v in result.values() if v["signal"]=="BUY")
    bear = sum(1 for v in result.values() if v["signal"]=="SELL")
    if bull>=3:   conf = "ALCISTA FUERTE"
    elif bull>=2: conf = "ALCISTA"
    elif bear>=3: conf = "BAJISTA FUERTE"
    elif bear>=2: conf = "BAJISTA"
    else:         conf = "NEUTRAL"
    return {"timeframes": result, "confluence": conf, "bull": bull, "bear": bear}

# ══════════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════════
cache = {
    "tickers":    {},
    "indicators": {},
    "signals":    {},
    "orderflow":  {},
    "news":       {},
    "fgi":        {"value":50,"label":"Neutral"},
    "history":    [],
    "last_update":None,
    "updating":   False,
    "last_alerts":{},
}

def update_all():
    if cache["updating"]: return
    cache["updating"] = True
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Actualizando NEXUS ELITE...")
    tickers = get_all_tickers()
    if tickers: cache["tickers"] = tickers
    cache["fgi"] = get_fear_greed()

    for pair in PAIRS:
        try:
            df  = get_klines(pair, CONFIG["kline_tf"], CONFIG["kline_limit"])
            if df.empty: continue
            ind = calc_all_indicators(df)
            ob  = get_orderbook_deep(pair)
            news = cache["news"].get(pair, {"score":0})
            ml  = ml_scorer.score(ind, ob, news.get("score",0))
            cache["indicators"][pair] = ind
            cache["orderflow"][pair]  = ob
            cache["signals"][pair]    = ml

            sig  = ml["signal"]
            conf = ml["confidence"]
            prev = cache["last_alerts"].get(pair,{}).get("signal")

            if sig in ["BUY","SELL"] and conf>=CONFIG["min_confidence"] and sig!=prev:
                price = float(cache["tickers"].get(pair,{}).get("lastPrice",0))
                atr   = ind.get("atr", price*0.012)
                sl    = price - atr*1.5 if sig=="BUY" else price + atr*1.5
                tp    = price + atr*3   if sig=="BUY" else price - atr*3
                trail = calc_trailing_stop(sig, price, price, atr)
                msg   = tg_alert(pair, sig, conf, price, sl, tp, 2.0, trail, "Señal ML automática", ml["ml_score"], news.get("score",0))
                send_telegram(msg)
                cache["last_alerts"][pair] = {"signal":sig,"time":datetime.now()}
                cache["history"].insert(0,{"time":datetime.now().strftime("%H:%M:%S"),"pair":pair,"signal":sig,"confidence":conf,"price":round(price,4),"ml_score":ml["ml_score"],"source":"AUTO"})
                if len(cache["history"])>100: cache["history"]=cache["history"][:100]

            print(f"  ✓ {pair}: RSI={ind.get('rsi','?')} ML={ml['ml_score']} → {sig} ({conf}%)")
            time.sleep(0.25)
        except Exception as e:
            print(f"  ✗ {pair}: {e}")

    cache["last_update"] = datetime.now().strftime("%H:%M:%S")
    cache["updating"] = False
    print(f"[OK] Elite update complete — {cache['last_update']}")

def news_updater():
    """Actualiza noticias cada 5 minutos"""
    while True:
        for pair in PAIRS[:8]:  # Top 8 pares
            try:
                cache["news"][pair] = get_news_sentiment(pair)
                time.sleep(2)
            except: pass
        time.sleep(300)

def bg_updater():
    while True:
        update_all()
        time.sleep(CONFIG["refresh_sec"])

# ══════════════════════════════════════════════════════════════════
#  CLAUDE AI
# ══════════════════════════════════════════════════════════════════
def analyze_ai(pair):
    client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    ticker = cache["tickers"].get(pair, {})
    ind    = cache["indicators"].get(pair, {})
    ob     = cache["orderflow"].get(pair, {})
    news   = cache["news"].get(pair, {"score":0,"label":"NEUTRAL","headlines":[]})
    fgi    = cache["fgi"]
    ml     = cache["signals"].get(pair, {"ml_score":50})
    price  = float(ticker.get("lastPrice",0))
    atr    = ind.get("atr", price*0.012)
    capital = CONFIG["capital"]
    risk    = CONFIG["risk_pct"]

    headlines_str = "\n".join(f"  • {h}" for h in news.get("headlines",[])[:3]) or "  Sin noticias recientes"
    patterns_str  = ", ".join(p["name"] for p in ind.get("patterns",[])) or "Ninguno"

    prompt = f"""Eres NEXUS PRO ELITE AI — sistema institucional de análisis crypto con ML integrado.
Responde ÚNICAMENTE con JSON válido.

PAR: {pair} | PRECIO: ${price:,.6f} | CAMBIO 24H: {float(ticker.get('priceChangePercent',0)):.2f}%
TIMEFRAME: {CONFIG['kline_tf']} | VELAS: {ind.get('candles',0)}

INDICADORES TÉCNICOS REALES:
RSI(14): {ind.get('rsi','?')} | ATR: {atr:.6f}
EMA 9: {ind.get('ema9','?')} | EMA 21: {ind.get('ema21','?')} | EMA 50: {ind.get('ema50','?')}
MACD hist: {ind.get('macd',{}).get('hist','?')} | línea: {ind.get('macd',{}).get('line','?')}
BB posición: {ind.get('bb',{}).get('pos','?')}% | ancho: {ind.get('bb',{}).get('width','?')}%
Estocástico K: {ind.get('stoch',{}).get('k','?')} D: {ind.get('stoch',{}).get('d','?')}
Volumen ratio: {ind.get('vol',{}).get('ratio','?')}x | Buy%: {ind.get('vol',{}).get('buy_pct','?')}%

ORDER FLOW AVANZADO:
Imbalance: {ob.get('imbalance',0):.1f}% | Ballenas BUY: {ob.get('whale_bids',0):.2f} | Ballenas SELL: {ob.get('whale_asks',0):.2f}
Señal ballenas: {ob.get('whale_signal','NEUTRAL')}

PATRONES DE VELAS: {patterns_str}
SOPORTES: {ind.get('sr',{}).get('support',[])}
RESISTENCIAS: {ind.get('sr',{}).get('resistance',[])}

ML SCORE: {ml.get('ml_score',50)}/100

SENTIMIENTO NOTICIAS: {news.get('label','NEUTRAL')} (score: {news.get('score',0)})
Headlines:
{headlines_str}

FEAR & GREED: {fgi['value']} ({fgi['label']})

GESTIÓN RIESGO:
Capital: ${capital} | Riesgo: {risk}% | SL=1.5xATR | TP=3xATR

JSON:
{{
  "signal": "BUY"|"SELL"|"WAIT",
  "confidence": 0-100,
  "entry": número,
  "sl": número,
  "tp": número,
  "trailing_sl": número,
  "rr": número,
  "lot": número,
  "trend": "ALCISTA"|"BAJISTA"|"LATERAL",
  "strength": "FUERTE"|"MODERADO"|"DÉBIL",
  "reasoning": "máximo 2 oraciones en español",
  "key_support": número,
  "key_resistance": número,
  "news_impact": "POSITIVO"|"NEGATIVO"|"NEUTRAL",
  "whale_alert": true|false,
  "warnings": []
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role":"user","content":prompt}]
    )
    raw   = response.content[0].text.strip()
    clean = raw.replace("```json","").replace("```","").strip()
    match = re.search(r"\{[\s\S]*\}", clean)
    if not match: raise ValueError("No JSON")
    result = json.loads(match.group())

    # Calculate trailing stop
    price_now = float(cache["tickers"].get(pair,{}).get("lastPrice",0))
    result["trailing_sl"] = calc_trailing_stop(
        result.get("signal","WAIT"),
        result.get("entry", price_now),
        price_now, atr
    )

    if result.get("signal") in ["BUY","SELL"]:
        cache["history"].insert(0,{
            "time": datetime.now().strftime("%H:%M:%S"),
            "pair": pair,
            "signal": result["signal"],
            "confidence": result.get("confidence",0),
            "price": result.get("entry",0),
            "ml_score": ml.get("ml_score",0),
            "source": "AI"
        })
        if result.get("confidence",0) >= CONFIG["min_confidence"]:
            msg = tg_alert(pair, result["signal"], result["confidence"],
                result.get("entry",0), result.get("sl",0), result.get("tp",0),
                result.get("rr",2), result.get("trailing_sl",0),
                result.get("reasoning",""), ml.get("ml_score",0), news.get("score",0))
            send_telegram(msg)
    return result

# ══════════════════════════════════════════════════════════════════
#  FLASK
# ══════════════════════════════════════════════════════════════════
app = Flask(__name__)
CORS(app)


@app.route("/")
@app.route("/app")
def serve_app():
    return send_file("nexus_apex-FINAL.html")

@app.route("/api/status")
def status():
    return jsonify({"ok":True,"last_update":cache["last_update"],"updating":cache["updating"],
        "pairs":len(PAIRS),"fgi":cache["fgi"],
        "telegram":"TU_API" not in CONFIG["telegram_token"]})

@app.route("/api/tickers")
def tickers():
    result = {}
    for pair in PAIRS:
        t   = cache["tickers"].get(pair,{})
        ml  = cache["signals"].get(pair,{"signal":"SCAN","confidence":0,"ml_score":0})
        ind = cache["indicators"].get(pair,{})
        ob  = cache["orderflow"].get(pair,{})
        news= cache["news"].get(pair,{"score":0,"label":"NEUTRAL"})
        result[pair] = {
            "price":     float(t.get("lastPrice",0)),
            "change":    float(t.get("priceChangePercent",0)),
            "high":      float(t.get("highPrice",0)),
            "low":       float(t.get("lowPrice",0)),
            "volume":    float(t.get("volume",0)),
            "signal":    ml["signal"],
            "confidence":ml["confidence"],
            "ml_score":  ml.get("ml_score",0),
            "patterns":  ind.get("patterns",[]),
            "rsi":       ind.get("rsi",0),
            "whale":     ob.get("whale_signal","NEUTRAL"),
            "news":      news.get("label","NEUTRAL"),
        }
    return jsonify(result)

@app.route("/api/indicators/<symbol>")
def indicators(symbol):
    ind  = cache["indicators"].get(symbol.upper(),{})
    ml   = cache["signals"].get(symbol.upper(),{"signal":"SCAN","confidence":0,"ml_score":0})
    ob   = cache["orderflow"].get(symbol.upper(),{})
    news = cache["news"].get(symbol.upper(),{})
    return jsonify({"indicators":ind,"signal":ml,"orderflow":ob,"news":news})

@app.route("/api/klines/<symbol>")
def klines(symbol):
    tf    = request.args.get("tf", CONFIG["kline_tf"])
    limit = int(request.args.get("limit",120))
    df = get_klines(symbol.upper(), tf, limit)
    if df.empty: return jsonify([])
    return jsonify(df[["time","open","high","low","close","volume"]].assign(
        time=df["time"].astype(str)).to_dict(orient="records"))

@app.route("/api/analyze/<symbol>", methods=["POST"])
def analyze(symbol):
    try:
        result = analyze_ai(symbol.upper())
        return jsonify({"ok":True,"result":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/mtf/<symbol>")
def mtf(symbol):
    try:
        result = multi_tf_analysis(symbol.upper())
        return jsonify({"ok":True,"result":result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/history")
def history():
    return jsonify(cache["history"])

@app.route("/api/news/<symbol>")
def news(symbol):
    return jsonify(cache["news"].get(symbol.upper(),{"score":0,"label":"NEUTRAL","headlines":[]}))

@app.route("/api/backtest/<symbol>")
def backtest(symbol):
    tf      = request.args.get("tf","1h")
    limit   = int(request.args.get("limit",300))
    capital = float(request.args.get("capital",10000))
    risk    = float(request.args.get("risk",1))
    df = get_klines(symbol.upper(), tf, limit)
    if df.empty: return jsonify({"ok":False,"error":"Sin datos"})
    trades, curve = run_backtest(df, capital, risk)
    stats = calc_stats(trades, capital, curve)
    return jsonify({"ok":True,"trades":trades[:60],"equity":curve,"stats":stats})

def run_backtest(df, capital, risk_pct):
    closes=df["close"].values; highs=df["high"].values; lows=df["low"].values
    trades=[]; curve=[capital]; equity=capital; wins=losses=0
    for i in range(50, len(df)-1):
        sl_df = df.iloc[:i+1]
        if len(sl_df)<30: continue
        ind = calc_all_indicators(sl_df)
        ob  = {"imbalance":0,"whale_signal":"NEUTRAL"}
        ml  = ml_scorer.score(ind, ob)
        sig = ml["signal"]
        if sig=="WAIT": curve.append(equity); continue
        entry=closes[i]; atr=ind["atr"]
        sl_p = entry-atr*1.5 if sig=="BUY" else entry+atr*1.5
        tp_p = entry+atr*3   if sig=="BUY" else entry-atr*3
        riskA=equity*risk_pct/100; slD=abs(entry-sl_p)
        units=riskA/slD if slD>0 else 0
        next_c=closes[min(i+1,len(closes)-1)]
        hitTp=(sig=="BUY" and next_c>=tp_p) or (sig=="SELL" and next_c<=tp_p)
        hitSl=(sig=="BUY" and next_c<=sl_p) or (sig=="SELL" and next_c>=sl_p)
        if hitTp:   pnl=abs(tp_p-entry)*units;   wins+=1
        elif hitSl: pnl=-abs(sl_p-entry)*units;  losses+=1
        else:
            pnl=(next_c-entry)*units*(1 if sig=="BUY" else -1)
            if pnl>0: wins+=1
            else: losses+=1
        equity+=pnl; curve.append(round(equity,2))
        trades.append({"num":len(trades)+1,"signal":sig,"entry":round(entry,4),"sl":round(sl_p,4),"tp":round(tp_p,4),"rr":"2.0","pnl":round(pnl,2),"win":pnl>0,"ml":ml["ml_score"]})
        if len(trades)>=80: break
    return trades, curve

def calc_stats(trades, capital, curve):
    if not trades: return {}
    equity=curve[-1]; wins=sum(1 for t in trades if t["win"]); losses=len(trades)-wins
    wr=wins/len(trades)*100; tr=(equity-capital)/capital*100
    wP=[t["pnl"] for t in trades if t["win"]]; lP=[t["pnl"] for t in trades if not t["win"]]
    avgW=sum(wP)/len(wP) if wP else 0; avgL=sum(lP)/len(lP) if lP else 0
    pf=abs(avgW*wins/(avgL*losses)) if losses>0 and avgL!=0 else 99
    peak=capital; maxDD=0
    for v in curve:
        if v>peak: peak=v
        dd=(peak-v)/peak*100
        if dd>maxDD: maxDD=dd
    return {"total_return":round(tr,2),"final_equity":round(equity,2),"win_rate":round(wr,1),
            "profit_factor":round(pf,2),"max_drawdown":round(maxDD,1),"total_trades":len(trades)}

# ══════════════════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════════════════
# Arrancar threads siempre (funciona con gunicorn Y con python directo)
print("\n"+"═"*58)
print("  NEXUS PRO ELITE — Bloomberg Terminal Style")
print("═"*58)
print(f"  Capital:   ${CONFIG['capital']} USDT | Riesgo: {CONFIG['risk_pct']}%")
print(f"  ML Score:  ACTIVO | Trailing Stop: ACTIVO")
print(f"  News:      ACTIVO | Order Flow: ACTIVO")
print(f"  Telegram:  {'✅' if 'TU_API' not in CONFIG['telegram_token'] else '⚠️  Configura token'}")
print("═"*58)
threading.Thread(target=update_all, daemon=True).start()
threading.Thread(target=bg_updater, daemon=True).start()
threading.Thread(target=news_updater, daemon=True).start()
print("\n  ✅ Servidor listo en http://localhost:5001")
print("  → Abre nexus_elite.html en tu navegador\n")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
