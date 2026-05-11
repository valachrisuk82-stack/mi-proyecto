"""
╔══════════════════════════════════════════════════════════════════╗
║   NEXUS PRO ELITE — Servidor con ML + Trailing Stop             ║
║   Order Flow + Noticias Crypto + Bloomberg Style                ║
╚══════════════════════════════════════════════════════════════════╝
"""

from flask import Flask, jsonify, request, send_file, send_from_directory
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
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
from collections import deque
from functools import wraps

# ══════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════
CONFIG = {
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    "telegram_token":    "8683659808:AAGqxOiZUBnzhNWnk-ET5Cz7ZQKGPBUrHH0",
    "telegram_chat_id":  "8204656882",
    "capital":           1000.0,
    "risk_pct":          1.0,
    "kline_tf":          "5m",
    "kline_limit":       200,
    "refresh_sec":       30,
    "min_confidence":    60,
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




# ══════════════════════════════════════════════════════════════════
#  JOURNAL + RISK MANAGER
# ══════════════════════════════════════════════════════════════════
import sqlite3 as _sq

JOURNAL_DB = "nexus_journal.db"

def journal_init():
    con = _sq.connect(JOURNAL_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, pair TEXT, signal TEXT,
        entry REAL, sl REAL, tp REAL, rr REAL,
        confidence INTEGER, ml_score INTEGER, source TEXT,
        outcome TEXT DEFAULT 'OPEN',
        pnl_r REAL DEFAULT 0,
        closed_at TEXT DEFAULT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS daily_stats (
        date TEXT PRIMARY KEY,
        trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        net_r REAL DEFAULT 0,
        blocked INTEGER DEFAULT 0
    )""")
    con.commit(); con.close()

def journal_add(pair, signal, entry, sl, tp, rr, conf, ml_score, source="SMC"):
    today = datetime.now().strftime("%Y-%m-%d")
    # Risk manager — max 3 operaciones abiertas
    con = _sq.connect(JOURNAL_DB)
    open_trades = con.execute("SELECT COUNT(*) FROM trades WHERE outcome='OPEN'").fetchone()[0]
    blocked = con.execute("SELECT blocked FROM daily_stats WHERE date=?", (today,)).fetchone()
    blocked = blocked[0] if blocked else 0
    if blocked:
        con.close()
        return None, "BLOQUEADO: stop diario alcanzado"
    if open_trades >= 3:
        con.close()
        return None, f"BLOQUEADO: {open_trades} operaciones abiertas"
    # R:R mínimo 2:1
    if rr < 2.0:
        con.close()
        return None, f"BLOQUEADO: R:R {rr:.1f} < 2.0"
    cur = con.execute("""INSERT INTO trades
        (ts, pair, signal, entry, sl, tp, rr, confidence, ml_score, source)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pair, signal,
         entry, sl, tp, rr, conf, ml_score, source))
    trade_id = cur.lastrowid
    # Actualizar daily_stats
    con.execute("""INSERT INTO daily_stats(date,trades) VALUES(?,1)
        ON CONFLICT(date) DO UPDATE SET trades=trades+1""", (today,))
    con.commit(); con.close()
    return trade_id, "OK"

def journal_close_open_trades():
    """Verifica trades abiertos y cierra los que tocaron SL o TP"""
    con = _sq.connect(JOURNAL_DB)
    open_trades = con.execute(
        "SELECT id,pair,signal,entry,sl,tp FROM trades WHERE outcome='OPEN'").fetchall()
    for tid, pair, signal, entry, sl, tp in open_trades:
        try:
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price",
                           params={"symbol": pair}, timeout=3)
            price = float(r.json()["price"])
            outcome = None
            pnl_r   = 0
            if signal == "LONG":
                if price <= sl:   outcome, pnl_r = "LOSS", -1.0
                elif price >= tp: outcome, pnl_r = "WIN",   3.0
            else:
                if price >= sl:   outcome, pnl_r = "LOSS", -1.0
                elif price <= tp: outcome, pnl_r = "WIN",   3.0
            if outcome:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                con.execute("""UPDATE trades SET outcome=?, pnl_r=?, closed_at=? WHERE id=?""",
                           (outcome, pnl_r, now.strftime("%Y-%m-%d %H:%M:%S"), tid))
                col = "wins" if outcome=="WIN" else "losses"
                con.execute(f"""INSERT INTO daily_stats(date,{col},net_r) VALUES(?,1,?)
                    ON CONFLICT(date) DO UPDATE SET {col}={col}+1, net_r=net_r+?""",
                    (today, pnl_r, pnl_r))
                # Stop diario: si net_r <= -3R bloquear
                net = con.execute("SELECT net_r FROM daily_stats WHERE date=?", (today,)).fetchone()
                if net and net[0] <= -3.0:
                    con.execute("""INSERT INTO daily_stats(date,blocked) VALUES(?,1)
                        ON CONFLICT(date) DO UPDATE SET blocked=1""", (today,))
                    send_telegram(f"🛑 <b>NEXUS APEX — STOP DIARIO</b>\nPérdida de {net[0]:.1f}R alcanzada.\nSistema bloqueado hasta mañana.")
                con.commit()
        except: pass
    con.close()

def journal_stats():
    con = _sq.connect(JOURNAL_DB)
    rows = con.execute("""SELECT outcome, pnl_r FROM trades
        WHERE outcome != 'OPEN' ORDER BY id DESC LIMIT 100""").fetchall()
    today = datetime.now().strftime("%Y-%m-%d")
    day = con.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
    open_c = con.execute("SELECT COUNT(*) FROM trades WHERE outcome='OPEN'").fetchone()[0]
    con.close()
    wins   = sum(1 for r in rows if r[0]=="WIN")
    losses = sum(1 for r in rows if r[0]=="LOSS")
    total  = wins+losses
    wr     = round(wins/total*100, 1) if total else 0
    pf     = round(wins*3/losses, 2) if losses else 0
    net_r  = round(sum(r[1] for r in rows), 1)
    blocked= bool(day[5]) if day else False
    return {
        "wins": wins, "losses": losses, "total": total,
        "wr": wr, "pf": pf, "net_r": net_r,
        "open_trades": open_c, "blocked": blocked,
        "today_net": round(day[4],1) if day else 0
    }

def journal_recent(limit=20):
    con = _sq.connect(JOURNAL_DB)
    rows = con.execute("""SELECT ts,pair,signal,entry,sl,tp,rr,confidence,ml_score,outcome,pnl_r,source
        FROM trades ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    con.close()
    keys = ["ts","pair","signal","entry","sl","tp","rr","confidence","ml_score","outcome","pnl_r","source"]
    return [dict(zip(keys,r)) for r in rows]

journal_init()


def verify_token(token):
    """Verifica un JWT token y retorna el user_id si es válido"""
    try:
        import base64, hashlib, json
        parts = token.split(".")
        if len(parts) != 2: return None
        p64, sig = parts
        expected = hashlib.sha256(f"{p64}nexus_secret".encode()).hexdigest()[:16]
        if sig != expected: return None
        payload = json.loads(base64.b64decode(p64).decode())
        # Verificar expiración
        from datetime import datetime
        exp = datetime.fromisoformat(payload.get("exp","2000-01-01"))
        if datetime.now() > exp: return None
        return payload
    except: return None

def get_user_plan(token):
    """Retorna el plan del usuario desde el token"""
    payload = verify_token(token)
    if not payload: return None
    return payload.get("plan","free")

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
        top_bids = sorted(bids, key=lambda x: x[1], reverse=True)[:5]
        top_asks = sorted(asks, key=lambda x: x[1], reverse=True)[:5]
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
            "top_bids": top_bids,
            "top_asks": top_asks,
            "bid_vol": round(bid_vol,2),
            "ask_vol": round(ask_vol,2),
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
    """Obtiene noticias via RSS gratuito"""
    import re
    coin_lower = symbol.replace("USDT","").lower()
    score = 0
    headlines = []
    sources = [
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://cryptonews.com/news/feed/",
    ]
    for url in sources:
        try:
            r = requests.get(url, timeout=5, headers={"User-Agent":"Mozilla/5.0"})
            titles = re.findall(r"<title><![CDATA[(.*?)]]></title>", r.text)
            if not titles:
                titles = re.findall(r"<title>(.*?)</title>", r.text)
            for title in titles[1:8]:
                tc = title.strip()[:100]
                tl = tc.lower()
                if coin_lower in tl or "bitcoin" in tl or "crypto" in tl:
                    headlines.append(tc)
                    for w in POSITIVE_WORDS:
                        if w in tl: score += 1
                    for w in NEGATIVE_WORDS:
                        if w in tl: score -= 1
        except: pass
    normalized = max(-10, min(10, score))
    label = "MUY POSITIVO" if normalized>5 else "POSITIVO" if normalized>1 else "MUY NEGATIVO" if normalized<-5 else "NEGATIVO" if normalized<-1 else "NEUTRAL"
    return {"score":normalized,"label":label,"headlines":list(dict.fromkeys(headlines))[:5]}
# ══════════════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
#  SMART MONEY CONCEPTS
# ══════════════════════════════════════════════════════════════════
def detect_smc(df):
    """Detecta Order Blocks, FVG, BOS/ChoCH"""
    try:
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        n = len(df)
        
        order_blocks = []
        fvg = []
        bos = []
        
        # Order Blocks — última vela bajista antes de movimiento alcista fuerte
        for i in range(2, min(n-1, 50)):
            idx = n - 1 - i  # Desde el final
            
            # Bullish Order Block: vela bajista seguida de movimiento alcista
            if opens[idx] > closes[idx]:  # Vela bajista
                # Verificar que siguiente movimiento sea alcista fuerte
                future_high = max(highs[idx+1:min(idx+5, n)])
                if future_high > highs[idx] * 1.002:
                    order_blocks.append({
                        "type": "bull",
                        "high": round(float(highs[idx]), 4),
                        "low": round(float(lows[idx]), 4),
                        "index": int(idx)
                    })
            
            # Bearish Order Block: vela alcista seguida de movimiento bajista
            elif closes[idx] > opens[idx]:  # Vela alcista
                future_low = min(lows[idx+1:min(idx+5, n)])
                if future_low < lows[idx] * 0.998:
                    order_blocks.append({
                        "type": "bear",
                        "high": round(float(highs[idx]), 4),
                        "low": round(float(lows[idx]), 4),
                        "index": int(idx)
                    })
        
        # Fair Value Gaps (FVG) — gaps entre velas
        for i in range(1, min(n-1, 30)):
            idx = n - 1 - i
            # Bullish FVG: low[i+1] > high[i-1]
            if lows[idx+1] > highs[idx-1]:
                fvg.append({
                    "type": "bull",
                    "high": round(float(lows[idx+1]), 4),
                    "low": round(float(highs[idx-1]), 4),
                    "index": int(idx)
                })
            # Bearish FVG: high[i+1] < low[i-1]
            elif highs[idx+1] < lows[idx-1]:
                fvg.append({
                    "type": "bear",
                    "high": round(float(lows[idx-1]), 4),
                    "low": round(float(highs[idx+1]), 4),
                    "index": int(idx)
                })
        
        # BOS/ChoCH — Break of Structure
        recent_high = max(highs[-20:])
        recent_low = min(lows[-20:])
        current = closes[-1]
        
        if current > recent_high * 0.999:
            bos.append({"type": "BOS_BULL", "level": round(float(recent_high), 4)})
        elif current < recent_low * 1.001:
            bos.append({"type": "BOS_BEAR", "level": round(float(recent_low), 4)})
        
        return {
            "order_blocks": order_blocks[:5],
            "fvg": fvg[:5],
            "bos": bos,
            "premium_zone": round(float(recent_high), 4),
            "discount_zone": round(float(recent_low), 4),
            "equilibrium": round(float((recent_high + recent_low) / 2), 4)
        }
    except Exception as e:
        return {"order_blocks": [], "fvg": [], "bos": [], "error": str(e)}

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
        # Confluencia validada con backtesting 5 años (WR 29.7%, R:R 1:3)
        rsi_v = ind.get("rsi", 50)
        macd_v = ind.get("macd", {})
        ema9_v = ind.get("ema9", 0); ema21_v = ind.get("ema21", 0); ema50_v = ind.get("ema50", 0)
        stoch_v = ind.get("stoch", {}).get("k", 50)
        vol_v = ind.get("vol", {}).get("ratio", 1)
        buy_conf = int(rsi_v<35) + int(macd_v.get("hist",0)>0) + int(ema9_v>ema21_v and ema21_v>ema50_v) + int(stoch_v<25) + int(vol_v>1.2)
        sell_conf = int(rsi_v>65) + int(macd_v.get("hist",0)<0) + int(ema9_v<ema21_v and ema21_v<ema50_v) + int(stoch_v>75) + int(vol_v>1.2)
        if buy_conf>=3 or total>=62:   signal, conf = "BUY",  round(min(95, max(total, 60+buy_conf*5)))
        elif sell_conf>=3 or total<=38: signal, conf = "SELL", round(min(95, max(100-total, 60+sell_conf*5)))
        else:                           signal, conf = "WAIT", round(50)

        return {
            "signal":     signal,
            "confidence": conf,
            "ml_score":   round(total),
            "breakdown":  scores,
        }

# ═══════════════════════════════════════════════════════
#  NEUROPSYCHOLOGY ENGINE — 6 Neuronas del Mercado
# ═══════════════════════════════════════════════════════
def calc_neuro_psychology(ind, klines=None):
    """
    6 Neuronas Psicológicas que detectan el estado emocional del mercado.
    Retorna boost al ML score y diagnóstico para Telegram.
    """
    rsi      = ind.get("rsi", 50)
    volume   = ind.get("volume_ratio", 1.0)
    macd     = ind.get("macd", 0)
    macd_sig = ind.get("macd_signal", 0)
    bb_pos   = ind.get("bb_position", 0.5)  # 0=banda baja, 1=banda alta
    stoch    = ind.get("stoch_k", 50)
    close    = ind.get("close", 0)
    ema21    = ind.get("ema21", close)
    ema50    = ind.get("ema50", close)

    neurons = {}
    boosts  = []
    alerts  = []

    # ── NEURONA 1: MIEDO COLECTIVO ─────────────────────────────────────────
    # Pánico de la masa = oportunidad contraria
    panic = rsi < 30 and volume > 2.0
    fear_level = max(0, (35 - rsi) / 35)
    if panic: fear_level = min(1.0, fear_level * 1.5)
    neurons["miedo"] = round(fear_level, 2)
    if panic:
        boosts.append(+12)
        alerts.append("😨 MIEDO COLECTIVO — La masa vende en pánico. Históricamente el mejor momento de compra.")
    elif rsi < 25:
        boosts.append(+18)
        alerts.append("😱 PÁNICO EXTREMO — Capitulación detectada. Rebote inminente con alta probabilidad.")

    # ── NEURONA 2: CODICIA / EUFORIA ───────────────────────────────────────
    # Euforia = trampa para compradores tardíos
    euphoria = rsi > 72 and volume > 1.8 and close > ema21
    greed_level = max(0, (rsi - 60) / 40)
    neurons["codicia"] = round(greed_level, 2)
    if euphoria:
        boosts.append(-10)
        alerts.append("🤑 EUFORIA DETECTADA — Los latecomers están comprando el techo. Precaución máxima.")
    elif rsi > 80:
        boosts.append(-15)
        alerts.append("🚨 SOBRECOMPRA EXTREMA — Mercado irracional alcista. Alta probabilidad de corrección.")

    # ── NEURONA 3: CAPITULACIÓN (DOLOR MÁXIMO) ─────────────────────────────
    # Dolor máximo del mercado = suelo potencial
    bb_extreme_low = bb_pos < 0.08
    cap_signal = bb_extreme_low and rsi < 35 and macd < macd_sig
    cap_level = max(0, (0.2 - bb_pos) / 0.2) if bb_pos < 0.2 else 0
    neurons["dolor"] = round(min(1.0, cap_level), 2)
    if cap_signal:
        boosts.append(+20)
        alerts.append("💔 CAPITULACIÓN — Precio en banda inferior + RSI bajo. Suelo técnico y psicológico.")

    # ── NEURONA 4: TRAMPA DEL MERCADO ──────────────────────────────────────
    # Señal demasiado obvia = posible trampa
    all_bull = (macd > macd_sig and rsi > 60 and close > ema21 and close > ema50 and volume > 1.5)
    all_bear = (macd < macd_sig and rsi < 40 and close < ema21 and close < ema50 and volume > 1.5)
    trap_level = 0.8 if (all_bull or all_bear) else 0.1
    neurons["trampa"] = round(trap_level, 2)
    if all_bull:
        boosts.append(-8)
        alerts.append("🪤 TRAMPA ALCISTA — Señal demasiado obvia. El mercado suele sorprender a la mayoría.")
    elif all_bear:
        boosts.append(-8)
        alerts.append("🪤 TRAMPA BAJISTA — Todos ven la caída. Cuidado con el short squeeze.")

    # ── NEURONA 5: MEMORIA DE PRECIO ───────────────────────────────────────
    # Niveles psicológicos redondos actúan como soporte/resistencia
    psych_levels = [1000,5000,10000,20000,25000,30000,40000,50000,
                    60000,65000,70000,75000,80000,100000,
                    1000,1500,2000,2500,3000,3500,4000,
                    0.5,1.0,1.5,2.0,5.0,10.0,
                    1800,1900,2000,2100,2200,2300,2400,2500,
                    4000,4500,5000,5500,6000,
                    1.0,1.05,1.1,1.15,1.2]
    near_level = any(abs(close - l) / (close or 1) < 0.008 for l in psych_levels if l > 0)
    neurons["memoria"] = 0.85 if near_level else 0.1
    if near_level:
        boosts.append(+5)
        alerts.append("🧠 NIVEL PSICOLÓGICO — Precio en zona de memoria colectiva. Alta reactividad esperada.")

    # ── NEURONA 6: ANTI-CONSENSO ───────────────────────────────────────────
    # Sentimiento extremo en una dirección = oportunidad contraria
    extreme_fear   = rsi < 20 or stoch < 10
    extreme_greed  = rsi > 80 or stoch > 90
    consensus_lvl  = abs(rsi - 50) / 50
    neurons["consenso"] = round(consensus_lvl, 2)
    if extreme_fear:
        boosts.append(+15)
        alerts.append("⚡ SENTIMIENTO EXTREMO BAJISTA — Cuando todos huyen, los smart money compran.")
    elif extreme_greed:
        boosts.append(-12)
        alerts.append("⚡ SENTIMIENTO EXTREMO ALCISTA — Cuando todos compran, los smart money venden.")

    # ── RESULTADO FINAL ────────────────────────────────────────────────────
    total_boost = sum(boosts)
    total_boost = max(-25, min(+25, total_boost))  # cap ±25 puntos

    # Diagnóstico principal (la neurona más activa)
    top_alert = alerts[0] if alerts else "🧠 Mercado en estado psicológico neutro."

    return {
        "neuro_boost":   total_boost,
        "neuro_alert":   top_alert,
        "neuro_alerts":  alerts,
        "neurons":       neurons,
        "neuro_summary": _neuro_summary(neurons, total_boost)
    }

def _neuro_summary(neurons, boost):
    """Genera resumen compacto para Telegram."""
    icons = {"miedo":"😨","codicia":"🤑","dolor":"💔","trampa":"🪤","memoria":"🧠","consenso":"⚡"}
    active = [(k,v) for k,v in neurons.items() if v > 0.5]
    if not active:
        return "🧠 Psicología: NEUTRAL"
    top = sorted(active, key=lambda x: x[1], reverse=True)[:2]
    parts = [f"{icons.get(k,'•')} {k.upper()} {int(v*100)}%" for k,v in top]
    direction = "ALCISTA 🟢" if boost > 5 else "BAJISTA 🔴" if boost < -5 else "NEUTRAL ⚪"
    return f"🧠 Neuro: {' | '.join(parts)} → {direction}"


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
_mtf_cache = {}
_mtf_cache_ttl = 30  # segundos

def multi_tf_analysis(symbol):
    import time
    now = time.time()
    if symbol in _mtf_cache and now - _mtf_cache[symbol]["ts"] < _mtf_cache_ttl:
        return _mtf_cache[symbol]["data"]
    result = {}
    def fetch_tf(tf):
        df = get_klines(symbol, tf, 100)
        if not df.empty and len(df)>=30:
            ind = calc_all_indicators(df)
            ob = get_orderbook_deep(symbol, 20)
            ml = ml_scorer.score(ind, ob)
            return tf, {
                "rsi":  ind.get("rsi",0),
                "trend": "ALCISTA" if ind.get("ema9",0)>ind.get("ema21",0) else "BAJISTA",
                "signal": ml["signal"],
                "ml_score":ml["ml_score"],
            }
        return tf, None
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fetch_tf, tf) for tf in ["1m","5m","15m","1h"]]
        for future in futures:
            tf, data = future.result()
            if data:
                result[tf] = data
    bull = sum(1 for v in result.values() if v["signal"]=="BUY")
    bear = sum(1 for v in result.values() if v["signal"]=="SELL")
    if bull>=3:   conf = "ALCISTA FUERTE"
    elif bull>=2: conf = "ALCISTA"
    elif bear>=3: conf = "BAJISTA FUERTE"
    elif bear>=2: conf = "BAJISTA"
    else:         conf = "NEUTRAL"
    _result = {"timeframes": result, "confluence": conf, "bull": bull, "bear": bear}
    _mtf_cache[symbol] = {"ts": now, "data": _result}
    return _result

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
    "last_alerts":{},"sr_alerts":{},"smc_history":[],"smc_last_scan":{},
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

    # ── Alertas de precio en S/R ──
    for pair in PAIRS:
        try:
            ind  = cache["indicators"].get(pair, {})
            sr   = ind.get("sr", {})
            t    = cache["tickers"].get(pair, {})
            price = float(t.get("lastPrice", 0))
            if not price: continue
            atr  = ind.get("atr", price*0.01)
            tol  = atr * 0.3  # tolerancia = 30% del ATR
            prev_alerts = cache.get("sr_alerts", {})
            for sup in sr.get("support", []):
                key = f"{pair}_S_{round(sup,2)}"
                if abs(price - sup) <= tol and key not in prev_alerts:
                    msg = f"🟢 <b>SOPORTE TOCADO</b>\n{pair} @ <b>${price:,.4f}</b>\nSoporte: ${sup:,.4f}\nATR: {atr:.4f}"
                    send_telegram(msg)
                    prev_alerts[key] = datetime.now()
            for res in sr.get("resistance", []):
                key = f"{pair}_R_{round(res,2)}"
                if abs(price - res) <= tol and key not in prev_alerts:
                    msg = f"🔴 <b>RESISTENCIA TOCADA</b>\n{pair} @ <b>${price:,.4f}</b>\nResistencia: ${res:,.4f}\nATR: {atr:.4f}"
                    send_telegram(msg)
                    prev_alerts[key] = datetime.now()
            # Limpiar alertas viejas (>2h)
            cache["sr_alerts"] = {k:v for k,v in prev_alerts.items() if (datetime.now()-v).seconds < 7200}
        except: pass
    cache["last_update"] = datetime.now().strftime("%H:%M:%S")
    cache["updating"] = False
    print(f"[OK] Elite update complete — {cache['last_update']}")


def smc_scanner():
    """Thread que escanea SMC cada 5 minutos"""
    import time as _time
    _time.sleep(30)  # esperar que arranque el servidor
    while True:
        try:
            scan_smc_all_pairs()
        except Exception as e:
            pass
        _time.sleep(300)  # cada 5 minutos

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
_analyze_cache = {}
_analyze_cache_time = {}
ANALYZE_CACHE_TTL = 300  # 5 minutos

_analyze_cache = {}
_analyze_cache_time = {}
ANALYZE_CACHE_TTL = 300  # 5 minutos

def analyze_local(pair):
    """Análisis local sin IA cuando no hay créditos"""
    ticker = cache["tickers"].get(pair, {})
    ind    = cache["indicators"].get(pair, {})
    ml     = cache["signals"].get(pair, {"ml_score":50, "signal":"WAIT", "confidence":50})
    # Precio para crypto
    price = float(ticker.get("lastPrice", 0))
    # Fallback para activos externos (FOREX, COMD, IDX, STK)
    if not price and pair in ALL_EXTERNAL:
        try:
            pd_data = get_yahoo_price_simple(ALL_EXTERNAL[pair]["ticker"])
            price = pd_data.get("price", 0)
        except:
            price = 0
    atr    = ind.get("atr", price*0.012)
    sig    = ml.get("signal","WAIT")
    conf   = ml.get("confidence",50)
    sl     = price - atr*1.5 if sig=="BUY" else price + atr*1.5
    tp     = price + atr*3   if sig=="BUY" else price - atr*3
    trail  = calc_trailing_stop(sig, price, price, atr)
    capital= CONFIG["capital"]; risk=CONFIG["risk_pct"]
    lot    = (capital*risk/100)/max(abs(price-sl),0.0001)
    rsi    = ind.get("rsi",50)
    trend  = "ALCISTA" if ind.get("ema9",0)>ind.get("ema21",0) else "BAJISTA"
    return {
        "signal": sig, "confidence": conf, "entry": price,
        "sl": sl, "tp": tp, "trailing_sl": trail, "rr": 3.0, "lot": lot,
        "trend": trend, "strength": "MODERADO",
        "reasoning": (
            f"{'🟢 SEÑAL ALCISTA' if sig=='BUY' else '🔴 SEÑAL BAJISTA' if sig=='SELL' else '⏳ MERCADO LATERAL'}. "
            f"RSI {rsi:.0f} {'(sobrevendido)' if rsi<35 else '(sobrecomprado)' if rsi>65 else '(neutral)'}. "
            f"EMA {'alcista 9>21>50' if ind.get('ema9',0)>ind.get('ema21',0)>ind.get('ema50',0) else 'bajista 9<21<50' if ind.get('ema9',0)<ind.get('ema21',0)<ind.get('ema50',0) else 'mixta'}. "
            f"MACD {'positivo' if ind.get('macd',{}).get('hist',0)>0 else 'negativo'}. "
            f"ML Score {ml.get('ml_score',50)}/100 — {'Alta' if ml.get('ml_score',50)>=65 else 'Media' if ml.get('ml_score',50)>=45 else 'Baja'} convicción."
        ),
        "key_support": sl, "key_resistance": tp,
        "news_impact": "NEUTRAL", "whale_alert": False, "warnings": []
    }

def analyze_ai(pair):
    now = time.time()
    if pair in _analyze_cache and now - _analyze_cache_time.get(pair, 0) < ANALYZE_CACHE_TTL:
        return _analyze_cache[pair]
    now = time.time()
    if pair in _analyze_cache and now - _analyze_cache_time.get(pair, 0) < ANALYZE_CACHE_TTL:
        return _analyze_cache[pair]
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
  "reasoning": "1 oración en español",
  "key_support": número,
  "key_resistance": número,
  "news_impact": "POSITIVO"|"NEGATIVO"|"NEUTRAL",
  "whale_alert": true|false,
  "warnings": []
}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
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
    _analyze_cache[pair] = result
    _analyze_cache_time[pair] = time.time()
    return result

# ══════════════════════════════════════════════════════════════════
#  FLASK
# ══════════════════════════════════════════════════════════════════









app = Flask(__name__)
CORS(app)


@app.route('/')
def index():
    with open('nexus_apex.html', 'r') as file:
        return file.read()

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
    sym = symbol.upper()
    ind  = cache["indicators"].get(sym,{})
    ml   = cache["signals"].get(sym,{"signal":"SCAN","confidence":0,"ml_score":0})
    ob   = cache["orderflow"].get(sym,{})
    news = cache["news"].get(sym,{})
    if not ind and sym in ALL_EXTERNAL:
        try:
            df = get_yahoo_klines_simple(ALL_EXTERNAL[sym]["ticker"], "5m")
            if not df.empty and len(df) >= 30:
                df["volume"] = df["volume"].replace(0,1).fillna(1)
                ind = calc_all_indicators(df)
                ml  = ml_scorer.score(ind, {})
                cache["indicators"][sym] = ind
                cache["signals"][sym] = ml
        except Exception as e:
            print(f"[ind ext] {sym}: {e}")
    return jsonify({"indicators":ind,"signal":ml,"orderflow":ob,"news":news})

@app.route("/api/klines/<symbol>")
def klines(symbol):
    sym   = symbol.upper()
    tf    = request.args.get("tf", CONFIG["kline_tf"])
    limit = int(request.args.get("limit",120))
    if sym in ALL_EXTERNAL:
        yf_ticker = ALL_EXTERNAL[sym]["ticker"]
        # Yahoo no tiene M1 real para activos externos — forzar mínimo 5m
        ext_tf = tf if tf not in ["1m"] else "5m"
        df = get_yahoo_klines_simple(yf_ticker, ext_tf)
    else:
        df = get_klines(sym, tf, limit)
    if df.empty: return jsonify([])
    df = df.tail(limit)
    return jsonify(df[["time","open","high","low","close","volume"]].assign(
        time=df["time"].astype(str)).to_dict(orient="records"))

@app.route("/api/analyze/<symbol>", methods=["POST"])
def analyze(symbol):
    try:
        result = analyze_ai(symbol.upper())
        return jsonify({"ok":True,"result":result})
    except Exception as e:
        print(f"[AI fallback] {symbol}: {e}")
        result = analyze_local(symbol.upper())
        return jsonify({"ok":True,"result":result,"fallback":True})

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
        trades.append({"num":len(trades)+1,"signal":sig,"entry":round(entry,4),"sl":round(sl_p,4),"tp":round(tp_p,4),"rr":"3.0","pnl":round(pnl,2),"win":pnl>0,"ml":ml["ml_score"]})
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


@app.route("/app")
def serve_app():
    import time
    with open("nexus_apex.html", "r") as f:
        html = f.read()
    html = html.replace("</head>", f'<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta name="version" content="{int(time.time())}"></head>', 1)
    from flask import Response
    return Response(html, mimetype="text/html", headers={"Cache-Control":"no-cache, no-store, must-revalidate","Pragma":"no-cache","Expires":"0"})

@app.route("/login")
def serve_login():
    return send_file("nexus_login.html")

@app.route("/auth/register", methods=["POST"])
def register():
    try:
        data = request.get_json()
        username = data.get("username","").strip().lower()
        email = data.get("email","").strip().lower()
        password = data.get("password","")
        if len(username)<3: return jsonify({"ok":False,"error":"Usuario muy corto"}),400
        if "@" not in email: return jsonify({"ok":False,"error":"Email invalido"}),400
        if len(password)<6: return jsonify({"ok":False,"error":"Password minimo 6 caracteres"}),400
        conn = sqlite3.connect("nexus_users.db")
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT UNIQUE, password_hash TEXT, plan TEXT DEFAULT 'free', phone TEXT, country TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("SELECT id FROM users WHERE username=? OR email=?", (username, email))
        if c.fetchone():
            conn.close()
            return jsonify({"ok":False,"error":"Usuario o email ya existe"}),400
        import hashlib, json, base64
        from datetime import timedelta
        pw_hash = hashlib.sha256(f"nexus_salt_{password}".encode()).hexdigest()
        phone   = data.get("phone","").strip()
        country = data.get("country","").strip()
        c.execute("INSERT INTO users (username,email,password_hash,plan,phone,country) VALUES (?,?,?,?,?,?)",(username,email,pw_hash,"free",phone,country))
        uid = c.lastrowid
        conn.commit(); conn.close()
        payload = json.dumps({"user_id":uid,"plan":"free","exp":(datetime.now()+timedelta(days=30)).isoformat()})
        p64 = base64.b64encode(payload.encode()).decode()
        sig = hashlib.sha256(f"{p64}nexus_secret".encode()).hexdigest()[:16]
        return jsonify({"ok":True,"token":f"{p64}.{sig}","user":{"id":uid,"username":username,"email":email,"plan":"free"},"message":"Bienvenido!"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.route("/auth/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        username = data.get("username","").strip().lower()
        password = data.get("password","")
        import hashlib, json, base64
        from datetime import timedelta
        conn = sqlite3.connect("nexus_users.db")
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT UNIQUE, password_hash TEXT, plan TEXT DEFAULT 'free', phone TEXT, country TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        pw_hash = hashlib.sha256(f"nexus_salt_{password}".encode()).hexdigest()
        c.execute("SELECT id,username,email,plan FROM users WHERE (username=? OR email=?) AND password_hash=?",(username,username,pw_hash))
        user = c.fetchone(); conn.close()
        if not user: return jsonify({"ok":False,"error":"Credenciales incorrectas"}),401
        payload = json.dumps({"user_id":user[0],"plan":user[3],"exp":(datetime.now()+timedelta(days=30)).isoformat()})
        p64 = base64.b64encode(payload.encode()).decode()
        sig = hashlib.sha256(f"{p64}nexus_secret".encode()).hexdigest()[:16]
        return jsonify({"ok":True,"token":f"{p64}.{sig}","user":{"id":user[0],"username":user[1],"email":user[2],"plan":user[3]}})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


# ══ ACTIVOS EXTERNOS (Yahoo Finance) ══
ALL_EXTERNAL = {
    "EURUSD":{"ticker":"EURUSD=X","cat":"FOREX"},
    "GBPUSD":{"ticker":"GBPUSD=X","cat":"FOREX"},
    "USDJPY":{"ticker":"USDJPY=X","cat":"FOREX"},
    "AUDUSD":{"ticker":"AUDUSD=X","cat":"FOREX"},
    "USDCHF":{"ticker":"USDCHF=X","cat":"FOREX"},
    "USDCAD":{"ticker":"USDCAD=X","cat":"FOREX"},
    "NZDUSD":{"ticker":"NZDUSD=X","cat":"FOREX"},
    "EURGBP":{"ticker":"EURGBP=X","cat":"FOREX"},
    "XAUUSD":{"ticker":"GC=F","cat":"COMMODITIES"},
    "XAGUSD":{"ticker":"SI=F","cat":"COMMODITIES"},
    "USOIL": {"ticker":"CL=F","cat":"COMMODITIES"},
    "UKOIL": {"ticker":"BZ=F","cat":"COMMODITIES"},
    "NATGAS":{"ticker":"NG=F","cat":"COMMODITIES"},
    "COPPER":{"ticker":"HG=F","cat":"COMMODITIES"},
    "WHEAT": {"ticker":"ZW=F","cat":"COMMODITIES"},
    "CORN":  {"ticker":"ZC=F","cat":"COMMODITIES"},
    "SPX500":{"ticker":"^GSPC","cat":"INDICES"},
    "NAS100":{"ticker":"^IXIC","cat":"INDICES"},
    "DOW30": {"ticker":"^DJI","cat":"INDICES"},
    "DAX40": {"ticker":"^GDAXI","cat":"INDICES"},
    "FTSE100":{"ticker":"^FTSE","cat":"INDICES"},
    "NIK225":{"ticker":"^N225","cat":"INDICES"},
    "VIX":   {"ticker":"^VIX","cat":"INDICES"},
    "AAPL":  {"ticker":"AAPL","cat":"STOCKS"},
    "TSLA":  {"ticker":"TSLA","cat":"STOCKS"},
    "NVDA":  {"ticker":"NVDA","cat":"STOCKS"},
    "AMZN":  {"ticker":"AMZN","cat":"STOCKS"},
    "MSFT":  {"ticker":"MSFT","cat":"STOCKS"},
    "GOOGL": {"ticker":"GOOGL","cat":"STOCKS"},
    "META":  {"ticker":"META","cat":"STOCKS"},
    "NFLX":  {"ticker":"NFLX","cat":"STOCKS"},
}

def get_yahoo_price_simple(yf_ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        r = requests.get(url, params={"interval":"1m","range":"1d"}, headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        data = r.json()
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev  = meta.get("previousClose", price)
        change = round((price - prev) / prev * 100, 2) if prev else 0
        high  = meta.get("regularMarketDayHigh", price)
        low   = meta.get("regularMarketDayLow", price)
        return {"price": round(price,4), "change": change, "high": round(high,4), "low": round(low,4)}
    except:
        return {"price": 0, "change": 0, "high": 0, "low": 0}

def get_yahoo_klines_simple(yf_ticker, tf="5m"):
    try:
        tf_map  = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"60m","4h":"1h","1d":"1d","1w":"1wk"}
        per_map = {"1m":"1d","5m":"5d","15m":"1mo","30m":"1mo","60m":"3mo","1h":"3mo","1d":"2y","1wk":"5y"}
        yf_tf  = tf_map.get(tf,"5m")
        period = per_map.get(yf_tf,"5d")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        r = requests.get(url, params={"interval":yf_tf,"range":period,"includePrePost":False}, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        data  = r.json()
        chart = data["chart"]["result"][0]
        ts = chart.get("timestamp") or []
        if not ts: return pd.DataFrame()
        q  = chart["indicators"]["quote"][0]
        df = pd.DataFrame({
            "time":  pd.to_datetime(ts, unit="s"),
            "open":  q.get("open",  [0]*len(ts)),
            "high":  q.get("high",  [0]*len(ts)),
            "low":   q.get("low",   [0]*len(ts)),
            "close": q.get("close", [0]*len(ts)),
            "volume":[x or 0 for x in q.get("volume",[0]*len(ts))],
        }).dropna()
        df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
        return df
    except Exception as e:
        print(f"[ERROR] Yahoo klines {yf_ticker}: {e}")
        return pd.DataFrame()

_ext_cache = {}
_ext_cache_time = {}
EXT_CACHE_TTL = 60

@app.route("/api/ext_tickers")
def ext_tickers():
    result = {}
    now = time.time()
    for sym, info in ALL_EXTERNAL.items():
        cache_key = f"ext_{sym}"
        if cache_key in _ext_cache and now - _ext_cache_time.get(cache_key,0) < EXT_CACHE_TTL:
            result[sym] = _ext_cache[cache_key]
            continue
        try:
            pd_data = get_yahoo_price_simple(info["ticker"])
            ml  = cache["signals"].get(sym, {"signal":"WAIT","confidence":50,"ml_score":50})
            entry = {
                "price":      pd_data["price"],
                "change":     pd_data["change"],
                "high":       pd_data["high"],
                "low":        pd_data["low"],
                "category":   info["cat"],
                "signal":     ml.get("signal","WAIT"),
                "confidence": ml.get("confidence",50),
                "ml_score":   ml.get("ml_score",50),
                "whale":      "NEUTRAL",
                "news":       "NEUTRAL",
            }
            _ext_cache[cache_key] = entry
            _ext_cache_time[cache_key] = now
            result[sym] = entry
        except:
            result[sym] = {"price":0,"change":0,"category":info["cat"],"signal":"WAIT","confidence":50,"ml_score":50}
    return jsonify(result)



@app.route("/api/smc/<symbol>")
def smc_analysis(symbol):
    try:
        df = get_klines(symbol, "1h", 100)
        smc = detect_smc(df)
        return jsonify(smc)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/orderbook/<symbol>")
def orderbook(symbol):
    try:
        data = get_orderbook_deep(symbol)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/volume_profile/<symbol>")
def volume_profile(symbol):
    try:
        df = get_klines(symbol, "1h", 200)
        vp = calc_volume_profile(df)
        return jsonify(vp)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/alert_price", methods=["POST"])
def alert_price():
    try:
        d = request.json
        pair = d.get("pair","")
        price = d.get("price",0)
        current = d.get("current",0)
        msg = f"🔔 <b>ALERTA DE PRECIO</b>\n{pair}\nNivel: <b>${price:,.4f}</b>\nPrecio actual: ${current:,.4f}"
        send_telegram(msg)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


@app.route("/api/predict/<symbol>")
def predict_price(symbol):
    try:
        ind = cache["indicators"].get(symbol, {})
        ticker = cache["tickers"].get(symbol, {})
        price = float(ticker.get("lastPrice", 0))
        # Buscar en activos externos si no hay precio crypto
        if not price:
            ext_prices = cache.get("ext_prices", {})
            # Buscar por símbolo directo
            if symbol in ext_prices:
                price = float(ext_prices[symbol].get("price", 0))
            # Buscar en ALL_EXTERNAL por nombre
            if not price:
                for key, val in ALL_EXTERNAL.items():
                    if key == symbol or val.get("ticker","").replace("=X","").replace("-USD","") == symbol:
                        if key in ext_prices:
                            price = float(ext_prices[key].get("price", 0))
                            if price:
                                ind = cache["indicators"].get(key, ind)
                                break
        if not price:
            return jsonify({"error": "no price for " + symbol})
        
        rsi = ind.get("rsi", 50)
        macd = ind.get("macd", {})
        macd_hist = macd.get("hist", 0) if isinstance(macd, dict) else 0
        ema9 = ind.get("ema9", price)
        ema21 = ind.get("ema21", price)
        atr = ind.get("atr", price * 0.01)
        ml = cache["signals"].get(symbol, {})
        ml_score = ml.get("ml_score", 50)
        signal = ml.get("signal", "WAIT")

        # Predicción simple basada en indicadores
        bias = 0
        if rsi < 35: bias += 1.5
        elif rsi > 65: bias -= 1.5
        if macd_hist > 0: bias += 1
        elif macd_hist < 0: bias -= 1
        if ema9 > ema21: bias += 1
        elif ema9 < ema21: bias -= 1
        if ml_score > 65: bias += 1.5
        elif ml_score < 35: bias -= 1.5

        # Calcular niveles predichos
        factor = bias / 10
        p1h = round(price * (1 + factor * 0.003), 4)
        p4h = round(price * (1 + factor * 0.008), 4)
        p24h = round(price * (1 + factor * 0.02), 4)
        
        direction = "ALCISTA" if bias > 0 else "BAJISTA" if bias < 0 else "LATERAL"
        confidence = min(95, max(30, 50 + abs(bias) * 8))

        return jsonify({
            "symbol": symbol,
            "current": price,
            "direction": direction,
            "confidence": round(confidence),
            "bias": round(bias, 2),
            "predictions": {
                "1h": p1h,
                "4h": p4h,
                "24h": p24h
            },
            "atr": round(atr, 4),
            "support": round(price - atr * 1.5, 4),
            "resistance": round(price + atr * 1.5, 4)
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/smc_signal", methods=["POST"])
def smc_signal():
    try:
        data = request.get_json()
        pair     = data.get("pair", "BTCUSDT")
        signal   = data.get("signal")        # "LONG" o "SHORT"
        entry    = float(data.get("entry", 0))
        sl       = float(data.get("sl", 0))
        tp       = float(data.get("tp", 0))
        ob_type  = data.get("ob_type", "")
        fvg      = data.get("fvg", False)
        bos      = data.get("bos", "")
        conf     = int(data.get("confidence", 0))

        if not signal or entry == 0:
            return jsonify({"ok": False, "error": "Datos incompletos"})

        rr = round(abs(tp - entry) / abs(entry - sl), 2) if abs(entry - sl) > 0 else 0
        emoji = "🟢" if signal == "LONG" else "🔴"
        bars = "█" * (conf // 10) + "░" * (10 - conf // 10)

        msg = (
            f"{emoji} <b>SMC SIGNAL — {signal} {pair}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Order Block: <b>{ob_type}</b>\n"
            f"📊 FVG: {'✅' if fvg else '❌'}  |  BOS: <b>{bos}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Entrada:  <b>${entry:,.4f}</b>\n"
            f"🛑 Stop Loss: <b>${sl:,.4f}</b>\n"
            f"🎯 Take Profit: <b>${tp:,.4f}</b>\n"
            f"📐 R:R  <b>1:{rr}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 Confluencia SMC: {bars} {conf}%\n"
            f"⚡ NEXUS APEX — Smart Money"
        )

        # Filtro noticias macro
        blackout, blackout_reason = is_news_blackout(pair)
        if blackout:
            return jsonify({"ok": False, "blocked": True, "reason": f"🗞️ BLACKOUT: {blackout_reason}"})

        # Confirmación triple: ML Score
        ml_score = int(data.get("ml_score", 0))
        mtf_ok   = bool(data.get("mtf_ok", False))
        if signal == "LONG"  and ml_score < 60:
            return jsonify({"ok": False, "blocked": True, "reason": f"ML Score {ml_score} < 60 para LONG"})
        if signal == "SHORT" and ml_score > 40:
            return jsonify({"ok": False, "blocked": True, "reason": f"ML Score {ml_score} > 40 para SHORT"})
        if not mtf_ok:
            return jsonify({"ok": False, "blocked": True, "reason": "MTF no confirma la dirección"})

        # Risk manager — añadir al journal
        trade_id, status = journal_add(pair, signal, entry, sl, tp, rr, conf, ml_score, "SMC")
        if trade_id is None:
            return jsonify({"ok": False, "blocked": True, "reason": status})

        send_telegram(msg)
        cache["history"].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "pair": pair, "signal": signal,
            "confidence": conf, "price": entry,
            "ml_score": ml_score, "source": "SMC"
        })
        return jsonify({"ok": True, "rr": rr, "trade_id": trade_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/smc_scan")
def smc_scan():
    """Escanea todos los pares y retorna los mejores setups SMC"""
    return jsonify({
        "history": cache.get("smc_history", []),
        "last_scan": cache.get("smc_last_scan", {})
    })

def detect_ob_fvg_bos(klines):
    """Detecta SMC en lista de klines [{open,high,low,close,volume}]"""
    if len(klines) < 20: return [], [], set()
    o = [k['open']   for k in klines]
    h = [k['high']   for k in klines]
    l = [k['low']    for k in klines]
    c = [k['close']  for k in klines]
    n = len(klines)
    atr = sum(h[i]-l[i] for i in range(n-14,n)) / 14

    obs, fvgs, bos_dirs = [], [], set()

    for j in range(1, n-1):
        if c[j-1]<o[j-1] and c[j]>o[j] and c[j+1]>h[j-1]:
            obs.append({'type':'bull','high':h[j-1],'low':l[j-1]})
        if c[j-1]>o[j-1] and c[j]<o[j] and c[j+1]<l[j-1]:
            obs.append({'type':'bear','high':h[j-1],'low':l[j-1]})

    for j in range(1, n-1):
        if l[j+1]>h[j-1] and (l[j+1]-h[j-1])>atr*0.25:
            fvgs.append({'type':'bull','top':l[j+1],'bot':h[j-1]})
        if h[j+1]<l[j-1] and (l[j-1]-h[j+1])>atr*0.25:
            fvgs.append({'type':'bear','top':l[j-1],'bot':h[j+1]})

    swing_h = max(h[-20:-1])
    swing_l = min(l[-20:-1])
    if c[-1] > swing_h: bos_dirs.add('bull')
    if c[-1] < swing_l: bos_dirs.add('bear')

    return obs[-8:], fvgs[-8:], bos_dirs

def scan_smc_all_pairs():
    """Escanea todos los pares cada 5 min buscando confluencia SMC"""
    import requests as req
    results = []
    for pair in PAIRS:
        try:
            # Obtener klines 1H
            r = req.get(f"https://api.binance.com/api/v3/klines",
                       params={"symbol":pair,"interval":"1h","limit":60}, timeout=5)
            raw = r.json()
            klines = [{"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),
                       "close":float(k[4]),"volume":float(k[5])} for k in raw]
            if len(klines) < 30: continue

            obs, fvgs, bos_dirs = detect_ob_fvg_bos(klines)
            price = klines[-1]['close']
            atr   = sum(k['high']-k['low'] for k in klines[-14:]) / 14

            # Calcular EMAs simples
            closes = [k['close'] for k in klines]
            ema9  = closes[-1]
            ema21 = closes[-1]
            for i in range(len(closes)-1, max(0,len(closes)-50), -1):
                ema9  = closes[i]*0.2   + ema9*0.8
                ema21 = closes[i]*0.095 + ema21*0.905

            conf = 0
            direction = None

            # LONG setup
            if ema9 > ema21:
                bull_ob  = next((o for o in obs  if o['type']=='bull' and o['low']<=price<=o['high']*1.004), None)
                bull_fvg = next((f for f in fvgs if f['type']=='bull' and f['bot']<=price<=f['top']*1.005), None)
                if bull_ob:  conf += 40
                if bull_fvg: conf += 30
                if 'bull' in bos_dirs: conf += 30
                if conf >= 70: direction = 'LONG'

            # SHORT setup
            if not direction and ema9 < ema21:
                conf = 0
                bear_ob  = next((o for o in obs  if o['type']=='bear' and o['low']*0.996<=price<=o['high']), None)
                bear_fvg = next((f for f in fvgs if f['type']=='bear' and f['bot']*0.995<=price<=f['top']), None)
                if bear_ob:  conf += 40
                if bear_fvg: conf += 30
                if 'bear' in bos_dirs: conf += 30
                if conf >= 70: direction = 'SHORT'

            if direction:
                results.append({
                    "pair": pair,
                    "signal": direction,
                    "confidence": conf,
                    "price": round(price, 4),
                    "atr": round(atr, 4),
                    "time": datetime.now().strftime("%H:%M:%S")
                })
        except Exception as e:
            pass

    # Ordenar por confianza
    results.sort(key=lambda x: x['confidence'], reverse=True)
    cache['smc_last_scan'] = {"time": datetime.now().strftime("%H:%M:%S"), "pairs_scanned": len(PAIRS), "setups": len(results)}

    # Guardar en historial y alertar el mejor
    for r in results[:3]:
        key = r['pair'] + r['signal']
        last = cache.get('smc_scan_alerts', {}).get(key)
        now_ts = datetime.now().timestamp()
        if not last or now_ts - last > 300:
            if not hasattr(cache, 'smc_scan_alerts'): cache['smc_scan_alerts'] = {}
            cache.setdefault('smc_scan_alerts', {})[key] = now_ts
            cache['smc_history'].insert(0, r)
            if len(cache['smc_history']) > 50: cache['smc_history'] = cache['smc_history'][:50]
            emoji = "🟢" if r['signal']=='LONG' else "🔴"
            bars = "█"*(r['confidence']//10) + "░"*(10-r['confidence']//10)
            msg = (f"{emoji} <b>SMC SCAN — {r['signal']} {r['pair']}</b>\n"
                   f"💰 Precio: <b>${r['price']:,.4f}</b>\n"
                   f"🧠 Confluencia: {bars} {r['confidence']}%\n"
                   f"⚡ NEXUS APEX Multi-Scanner")
            send_telegram(msg)
    return results


@app.route("/api/journal/stats")
def journal_stats_api():
    return jsonify(journal_stats())

@app.route("/api/journal/trades")
def journal_trades_api():
    return jsonify(journal_recent(30))

@app.route("/api/journal/close_trade", methods=["POST"])
def close_trade_manual():
    try:
        data = request.get_json()
        tid  = data.get("trade_id")
        outcome = data.get("outcome")  # WIN o LOSS
        if not tid or outcome not in ("WIN","LOSS"):
            return jsonify({"ok": False})
        pnl_r = 3.0 if outcome=="WIN" else -1.0
        today = datetime.now().strftime("%Y-%m-%d")
        con = __import__("sqlite3").connect(JOURNAL_DB)
        con.execute("UPDATE trades SET outcome=?, pnl_r=?, closed_at=? WHERE id=?",
                   (outcome, pnl_r, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tid))
        col = "wins" if outcome=="WIN" else "losses"
        con.execute(f"""INSERT INTO daily_stats(date,{col},net_r) VALUES(?,1,?)
            ON CONFLICT(date) DO UPDATE SET {col}={col}+1, net_r=net_r+?""",
            (today, pnl_r, pnl_r))
        con.commit(); con.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})




# ══ WAITLIST ══
_waitlist = []

@app.route("/api/waitlist", methods=["GET","POST"])
def waitlist():
    global _waitlist
    if request.method == "POST":
        email = request.get_json().get("email","").strip()
        if email and "@" in email and email not in _waitlist:
            _waitlist.append(email)
            # Guardar en archivo
            try:
                with open("waitlist.txt","a") as f:
                    f.write(email+"\n")
            except: pass
            send_telegram(f"📧 Nueva lista de espera: {email} ({len(_waitlist)} total)")
        return jsonify({"ok":True,"count":len(_waitlist)})
    return jsonify({"count":len(_waitlist)})

@app.route("/landing")
def landing_page():
    try:
        with open("nexus_landing.html","r") as f:
            return f.read(), 200, {"Content-Type":"text/html"}
    except Exception as e:
        return f"Error: {e}", 500

@app.route("/track")
def track_page():
    try:
        with open("nexus_track.html","r") as f:
            return f.read(), 200, {"Content-Type":"text/html"}
    except Exception as e:
        return f"Error: {e}", 500

@app.route("/stats")
def stats_page():
    try:
        with open("nexus_stats.html", "r") as f:
            return f.read(), 200, {"Content-Type": "text/html"}
    except Exception as e:
        return f"Error: {e}", 500

# ══════════════════════════════════════════════════════════════════
#  MACRO CALENDAR + NEWS FILTER
# ══════════════════════════════════════════════════════════════════
_macro_events = []
_macro_last_fetch = 0

def fetch_macro_calendar():
    """Obtiene eventos macro de ForexFactory RSS"""
    global _macro_events, _macro_last_fetch
    now = datetime.now().timestamp()
    if now - _macro_last_fetch < 3600: return _macro_events
    try:
        import xml.etree.ElementTree as ET
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=5)
        events = r.json()
        _macro_events = []
        for e in events:
            if e.get("impact") in ("High","Medium"):
                try:
                    dt_str = e.get("date","") + " " + e.get("time","")
                    dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d %I:%M%p")
                    _macro_events.append({
                        "title": e.get("title",""),
                        "impact": e.get("impact",""),
                        "currency": e.get("country",""),
                        "dt": dt
                    })
                except: pass
        _macro_last_fetch = now
    except:
        _macro_events = []
    return _macro_events

def is_news_blackout(pair="BTCUSDT", window_min=30):
    """Retorna True si hay evento macro de alto impacto en los próximos/últimos 30 min"""
    try:
        events = fetch_macro_calendar()
        now = datetime.now()
        for e in events:
            diff = abs((e["dt"] - now).total_seconds() / 60)
            if diff <= window_min:
                if e["impact"] == "High":
                    return True, f"{e['title']} ({e['impact']}) en {int(diff)}min"
        return False, ""
    except:
        return False, ""

@app.route("/api/macro_events")
def macro_events():
    try:
        events = fetch_macro_calendar()
        now = datetime.now()
        result = []
        for e in events:
            diff = (e["dt"] - now).total_seconds() / 60
            if -60 <= diff <= 240:
                result.append({
                    "title": e["title"],
                    "impact": e["impact"],
                    "currency": e["currency"],
                    "minutes_away": round(diff),
                    "time": e["dt"].strftime("%H:%M")
                })
        result.sort(key=lambda x: x["minutes_away"])
        blackout, reason = is_news_blackout()
        return jsonify({"events": result[:8], "blackout": blackout, "reason": reason})
    except Exception as ex:
        return jsonify({"events": [], "blackout": False, "reason": str(ex)})




# ══════════════════════════════════════════════════════════════════
#  TELEGRAM BOT INTERACTIVO (INTEGRADO)
# ══════════════════════════════════════════════════════════════════
_bot_offset = 0
_bot_users  = {}

def bot_send(chat_id, text, keyboard=None):
    token = CONFIG["telegram_token"]
    data  = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        import json as _json
        data["reply_markup"] = _json.dumps({"inline_keyboard": keyboard})
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                     data=data, timeout=5)
    except: pass

def bot_answer_cb(cb_id):
    token = CONFIG["telegram_token"]
    try:
        requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                     data={"callback_query_id": cb_id}, timeout=3)
    except: pass

def bot_get_signal(pair):
    pair = pair.upper().replace("/","")
    if not pair.endswith("USDT"):
        pair += "USDT"
    try:
        r     = requests.get("https://api.binance.com/api/v3/ticker/price",
                            params={"symbol":pair}, timeout=5)
        price = float(r.json()["price"])
        ind   = cache["indicators"].get(pair, {})
        ml    = cache["signals"].get(pair, {"signal":"WAIT","confidence":50,"ml_score":50})
        sig   = ml.get("signal","WAIT")
        conf  = ml.get("confidence",50)
        ml_s  = ml.get("ml_score",50)
        rsi   = ind.get("rsi",50) if ind else 50
        atr   = ind.get("atr", price*0.015) if ind else price*0.015
        emoji = "🟢" if sig=="BUY" else "🔴" if sig=="SELL" else "🟡"
        sl    = round(price-atr*1.5,4) if sig=="BUY" else round(price+atr*1.5,4)
        tp    = round(price+atr*3,4)   if sig=="BUY" else round(price-atr*3,4)
        rr    = round(abs(tp-price)/max(0.0001,abs(price-sl)),1)
        bars  = "█"*(ml_s//10) + "░"*(10-ml_s//10)
        lines = [
            f"{emoji} <b>NEXUS APEX — {pair}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Señal: <b>{sig}</b> ({conf}% confianza)",
            f"💰 Precio: <b>${price:,.4f}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 Entry:  <b>${price:,.4f}</b>",
            f"🛑 SL:     <b>${sl:,.4f}</b>",
            f"✅ TP:     <b>${tp:,.4f}</b>",
            f"📐 R:R     <b>1:{rr}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🧠 ML Score: {bars} {ml_s}/100",
            f"📈 RSI: {rsi:.1f}",
            "⚡ NEXUS APEX",
        ]
        kbd = [
            [{"text":"📊 Otro par","callback_data":"menu_signal"},
             {"text":"🔍 Scan","callback_data":"menu_scan"}],
            [{"text":"📈 Stats","callback_data":"menu_stats"},
             {"text":"🏠 Menú","callback_data":"menu_start"}],
        ]
        return "\n".join(lines), kbd
    except Exception as e:
        return f"❌ Error analizando {pair}: {e}", None

def bot_handle_message(msg):
    global _bot_users
    chat_id = msg["chat"]["id"]
    name    = msg["chat"].get("first_name","Trader")
    text    = msg.get("text","").strip()
    if chat_id not in _bot_users:
        _bot_users[chat_id] = {"name":name,"plan":"free"}
    if text.startswith("/start"):
        kbd = [
            [{"text":"📊 Señal en vivo","callback_data":"menu_signal"},
             {"text":"🔍 Escanear","callback_data":"menu_scan"}],
            [{"text":"📈 Track Record","callback_data":"menu_stats"},
             {"text":"💎 Planes","callback_data":"menu_plans"}],
        ]
        lines = [
            f"⚡ <b>Bienvenido a NEXUS APEX, {name}!</b>",
            "",
            "El sistema de trading más transparente del mercado.",
            "",
            "<b>Comandos:</b>",
            "• /signal BTC — Analizar un par",
            "• /scan — Mejores setups ahora",
            "• /stats — Track record",
            "• /pairs — Pares disponibles",
            "",
            "¿Qué quieres hacer?",
        ]
        bot_send(chat_id, "\n".join(lines), kbd)
    elif text.startswith("/signal") or text.startswith("/s "):
        parts = text.split()
        pair  = parts[1] if len(parts)>1 else "BTC"
        txt, kbd = bot_get_signal(pair)
        bot_send(chat_id, txt, kbd)
    elif text.startswith("/scan"):
        history = cache.get("smc_history",[])[:5]
        if not history:
            bot_send(chat_id, "🔍 Sin setups activos ahora. El scanner corre cada 5 min.")
        else:
            lines = ["🔍 <b>MEJORES SETUPS ACTIVOS</b>","━━━━━━━━━━━━━━━━━━━━"]
            for s in history:
                e = "🟢" if s["signal"]=="LONG" else "🔴"
                lines.append(f"{e} <b>{s['signal']} {s['pair'].replace('USDT','')}</b> — ${s['price']:,.2f} — {s['confidence']}%")
            kbd = [[{"text":"📊 Analizar par","callback_data":"menu_signal"},
                    {"text":"🏠 Menú","callback_data":"menu_start"}]]
            bot_send(chat_id, "\n".join(lines), kbd)
    elif text.startswith("/stats"):
        try:
            stats = paper_stats()
            wr    = stats["wr"]
            bars  = "█"*(wr//10) + "░"*(10-wr//10)
            lines = [
                "📈 <b>NEXUS APEX — TRACK RECORD</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                f"✅ Win Rate:      {bars} <b>{wr}%</b>",
                f"💰 Profit Factor: <b>{stats['pf']}</b>",
                f"📊 Net R:         <b>{'+' if stats['net_r']>=0 else ''}{stats['net_r']}R</b>",
                f"📉 Max DD:        <b>{stats['max_dd']}R</b>",
                f"🔢 Total trades:  <b>{stats['total']}</b>",
                f"📅 Días corriendo: <b>{stats['days_running']}</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                "⚡ Paper Trading verificado",
            ]
            bot_send(chat_id, "\n".join(lines))
        except Exception as e:
            bot_send(chat_id, f"❌ Error: {e}")
    elif text.startswith("/track"):
        bot_send(chat_id, "📊 <b>Track Record Público</b>\n\nhttps://mi-proyecto-production-29a8.up.railway.app/track")
    elif text.startswith("/pairs"):
        bot_send(chat_id, "💱 <b>Pares disponibles</b>\n\nBTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, LINK, UNI, ATOM, LTC, MATIC\n\nUsa: /signal BTC")
    else:
        pair = text.upper().replace("/","").replace(" ","")
        if len(pair) >= 3:
            txt, kbd = bot_get_signal(pair)
            bot_send(chat_id, txt, kbd)

def bot_handle_callback(cb):
    chat_id = cb["message"]["chat"]["id"]
    name    = cb["message"]["chat"].get("first_name","Trader")
    data    = cb.get("data","")
    bot_answer_cb(cb["id"])
    if data == "menu_start":
        bot_handle_message({"chat":{"id":chat_id,"first_name":name},"text":"/start"})
    elif data == "menu_signal":
        kbd = [
            [{"text":"BTC","callback_data":"signal_BTC"},
             {"text":"ETH","callback_data":"signal_ETH"},
             {"text":"BNB","callback_data":"signal_BNB"}],
            [{"text":"SOL","callback_data":"signal_SOL"},
             {"text":"XRP","callback_data":"signal_XRP"},
             {"text":"ADA","callback_data":"signal_ADA"}],
            [{"text":"DOGE","callback_data":"signal_DOGE"},
             {"text":"LINK","callback_data":"signal_LINK"},
             {"text":"AVAX","callback_data":"signal_AVAX"}],
            [{"text":"🔙 Volver","callback_data":"menu_start"}],
        ]
        bot_send(chat_id, "📊 <b>Selecciona un par:</b>", kbd)
    elif data.startswith("signal_"):
        txt, kbd = bot_get_signal(data.replace("signal_",""))
        bot_send(chat_id, txt, kbd)
    elif data == "menu_scan":
        bot_handle_message({"chat":{"id":chat_id,"first_name":name},"text":"/scan"})
    elif data == "menu_stats":
        bot_handle_message({"chat":{"id":chat_id,"first_name":name},"text":"/stats"})
    elif data == "menu_plans":
        lines = [
            "💎 <b>Planes NEXUS APEX</b>","",
            "🆓 <b>FREE</b> — Gratis","• Track record público","• Señales básicas","",
            "⭐ <b>PRO</b> — $49/mes","• Dashboard completo","• Señales SMC en tiempo real",
            "• Alertas personalizadas","• Risk Manager","",
            "💎 <b>ELITE</b> — $99/mes","• Todo lo de Pro","• API access","• Soporte prioritario",
        ]
        kbd = [[{"text":"🌐 Ver landing","url":"https://mi-proyecto-production-29a8.up.railway.app/landing"}]]
        bot_send(chat_id, "\n".join(lines), kbd)

def bot_polling():
    global _bot_offset
    import time as _t
    token = CONFIG["telegram_token"]
    try:
        requests.post(f"https://api.telegram.org/bot{token}/setMyCommands",
            json={"commands":[
                {"command":"start",  "description":"Menu principal"},
                {"command":"signal", "description":"Señal de un par (ej: /signal BTC)"},
                {"command":"scan",   "description":"Mejores setups ahora"},
                {"command":"stats",  "description":"Track record y estadisticas"},
                {"command":"track",  "description":"Link al track record publico"},
                {"command":"pairs",  "description":"Pares disponibles"},
            ]}, timeout=5)
    except: pass
    print("  🤖 Bot Telegram activo")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset":_bot_offset,"timeout":30,
                        "allowed_updates":["message","callback_query"]},
                timeout=35)
            for u in r.json().get("result",[]):
                _bot_offset = u["update_id"] + 1
                if "message" in u:
                    bot_handle_message(u["message"])
                elif "callback_query" in u:
                    bot_handle_callback(u["callback_query"])
        except: _t.sleep(5)
        _t.sleep(0.5)

# ══════════════════════════════════════════════════════════════════
#  PAPER TRADING ENGINE
# ══════════════════════════════════════════════════════════════════
PAPER_MODE = True  # Cambiar a False cuando tengas track record validado

def paper_add_trade(pair, signal, entry, sl, tp, rr, conf, ml_score):
    """Registra trade en paper trading y track record público"""
    if not PAPER_MODE: return None
    con = _sq.connect(JOURNAL_DB)
    # Verificar límites paper (max 5 simultáneos en paper)
    open_c = con.execute(
        "SELECT COUNT(*) FROM trades WHERE outcome='OPEN' AND mode='PAPER'").fetchone()[0]
    if open_c >= 5:
        con.close()
        return None
    cur = con.execute("""INSERT INTO trades
        (ts,pair,signal,entry,sl,tp,rr,confidence,ml_score,source,mode)
        VALUES(?,?,?,?,?,?,?,?,?,'PAPER','PAPER')""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         pair,signal,entry,sl,tp,rr,conf,ml_score))
    tid = cur.lastrowid
    # Registrar en track record público
    con.execute("""INSERT INTO track_record
        (ts,pair,signal,entry,sl,tp,rr,confidence,ml_score,mode)
        VALUES(?,?,?,?,?,?,?,?,?,'PAPER')""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         pair,signal,entry,sl,tp,rr,conf,ml_score))
    con.commit(); con.close()
    return tid

def paper_close_trades():
    """Cierra paper trades cuando tocan SL o TP"""
    con = _sq.connect(JOURNAL_DB)
    open_trades = con.execute(
        "SELECT id,pair,signal,entry,sl,tp FROM trades WHERE outcome='OPEN' AND mode='PAPER'"
    ).fetchall()
    closed = 0
    for tid, pair, signal, entry, sl, tp in open_trades:
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price",
                           params={"symbol": pair}, timeout=3)
            price = float(r.json()["price"])
            outcome, pnl_r = None, 0
            if signal == "LONG":
                if price <= sl:   outcome, pnl_r = "LOSS", -1.0
                elif price >= tp: outcome, pnl_r = "WIN",   3.0
            else:
                if price >= sl:   outcome, pnl_r = "LOSS", -1.0
                elif price <= tp: outcome, pnl_r = "WIN",   3.0
            if outcome:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                con.execute("""UPDATE trades SET outcome=?,pnl_r=?,closed_at=?
                    WHERE id=?""", (outcome,pnl_r,now_str,tid))
                # Actualizar track record
                con.execute("""UPDATE track_record SET outcome=?,pnl_r=?,closed_at=?
                    WHERE pair=? AND signal=? AND entry=? AND outcome='OPEN'""",
                    (outcome,pnl_r,now_str,pair,signal,entry))
                today = datetime.now().strftime("%Y-%m-%d")
                col = "wins" if outcome=="WIN" else "losses"
                con.execute(f"""INSERT INTO daily_stats(date,{col},net_r,mode)
                    VALUES(?,1,?,'PAPER')
                    ON CONFLICT(date) DO UPDATE SET {col}={col}+1,net_r=net_r+?""",
                    (today,pnl_r,pnl_r))
                closed += 1
        except: pass
    if closed: con.commit()
    con.close()
    return closed

def paper_stats():
    """Stats del paper trading para track record"""
    con = _sq.connect(JOURNAL_DB)
    rows = con.execute("""SELECT outcome,pnl_r,pair,signal,ts,entry,sl,tp,rr,ml_score
        FROM track_record ORDER BY id DESC""").fetchall()
    con.close()
    closed = [r for r in rows if r[0] not in ('OPEN',None)]
    wins   = sum(1 for r in closed if r[0]=='WIN')
    losses = sum(1 for r in closed if r[0]=='LOSS')
    total  = wins+losses
    wr     = round(wins/total*100,1) if total else 0
    pf     = round(wins*3/losses,2) if losses else 0
    net_r  = round(sum(r[1] for r in closed),1)
    # Drawdown máximo
    cumr, peak, dd = 0, 0, 0
    for r in reversed(closed):
        cumr += r[1]
        peak = max(peak, cumr)
        dd   = min(dd, cumr - peak)
    open_c = sum(1 for r in rows if r[0]=='OPEN')
    days   = len(set(r[4][:10] for r in rows)) if rows else 0
    return {
        "wins":wins,"losses":losses,"total":total,"open":open_c,
        "wr":wr,"pf":pf,"net_r":net_r,"max_dd":round(dd,1),
        "days_running":days,
        "trades": [{"outcome":r[0],"pnl_r":r[1],"pair":r[2],"signal":r[3],
                    "ts":r[4],"entry":r[5],"sl":r[6],"tp":r[7],"rr":r[8],"ml":r[9]}
                   for r in rows[:50]]
    }

@app.route("/api/paper/stats")
def paper_stats_api():
    return jsonify(paper_stats())

@app.route("/api/paper/add", methods=["POST"])
def paper_add_manual():
    """Añadir trade manualmente al paper trading"""
    try:
        d = request.get_json()
        tid = paper_add_trade(
            d["pair"],d["signal"],float(d["entry"]),
            float(d["sl"]),float(d["tp"]),float(d.get("rr",3)),
            int(d.get("confidence",70)),int(d.get("ml_score",50))
        )
        return jsonify({"ok": tid is not None, "trade_id": tid})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

def paper_trading_thread():
    """Thread que verifica paper trades cada 5 minutos"""
    import time as _t
    _t.sleep(60)
    while True:
        try:
            closed = paper_close_trades()
            if closed:
                stats = paper_stats()
                print(f"  📊 Paper: {closed} trades cerrados | WR:{stats['wr']}% Net:{stats['net_r']}R")
        except Exception as e:
            pass
        _t.sleep(300)



@app.route("/admin")
def admin_page():
    try:
        with open("nexus_admin.html","r") as f:
            return f.read(), 200, {"Content-Type":"text/html"}
    except Exception as e:
        return f"Error: {e}", 500

@app.route("/api/admin/upgrade", methods=["POST"])
def admin_upgrade():
    try:
        d    = request.get_json()
        uid  = d.get("user_id")
        plan = d.get("plan")
        if plan not in ("free","pro","elite"):
            return jsonify({"ok":False,"error":"Plan inválido"})
        conn = sqlite3.connect("nexus_users.db")
        conn.execute("UPDATE users SET plan=? WHERE id=?", (plan, uid))
        conn.commit()
        # Notificar por Telegram
        user = conn.execute("SELECT username,email FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
        if user:
            send_telegram(f"💎 <b>UPGRADE</b>\nUsuario: {user[0]}\nEmail: {user[1]}\nNuevo plan: <b>{plan.upper()}</b>")
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/admin/users")
def admin_users():
    try:
        conn = sqlite3.connect("nexus_users.db")
        c = conn.cursor()
        c.execute("SELECT id,username,email,plan,phone,country,created_at FROM users ORDER BY id DESC")
        users = [{"id":r[0],"username":r[1],"email":r[2],"plan":r[3],
                  "phone":r[4],"country":r[5],"created_at":r[6]} for r in c.fetchall()]
        conn.close()
        return jsonify({"total":len(users),"users":users})
    except Exception as e:
        return jsonify({"error":str(e)})


@app.route("/api/auth/verify", methods=["POST"])
def verify_token_api():
    """Verifica si un token es válido"""
    try:
        data  = request.get_json()
        token = data.get("token","")
        payload = verify_token(token)
        if not payload:
            return jsonify({"ok":False,"error":"Token inválido o expirado"}), 401
        return jsonify({"ok":True,"user":payload})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    """Renueva un token válido"""
    try:
        import base64, hashlib, json
        from datetime import timedelta
        data  = request.get_json()
        token = data.get("token","")
        payload = verify_token(token)
        if not payload:
            return jsonify({"ok":False,"error":"Token inválido"}), 401
        # Generar nuevo token con 30 días más
        new_payload = json.dumps({
            "user_id": payload["user_id"],
            "plan": payload["plan"],
            "exp": (datetime.now()+timedelta(days=30)).isoformat()
        })
        p64 = base64.b64encode(new_payload.encode()).decode()
        sig  = hashlib.sha256(f"{p64}nexus_secret".encode()).hexdigest()[:16]
        return jsonify({"ok":True,"token":f"{p64}.{sig}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500


@app.route("/api/payment/notify", methods=["POST"])
def payment_notify():
    """Recibe notificación de pago pendiente y avisa por Telegram"""
    try:
        d     = request.get_json()
        email = d.get("email","")
        plan  = d.get("plan","pro")
        ref   = d.get("ref","sin referencia")
        amount= "£29" if plan=="pro" else "£79"

        # Guardar en archivo de pagos pendientes
        with open("pending_payments.txt","a") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | {email} | {plan.upper()} | {amount} | ref:{ref}\n")

        # Alerta Telegram
        msg = (
            f"💰 <b>PAGO PENDIENTE — NEXUS APEX</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📧 Email: <b>{email}</b>\n"
            f"💎 Plan:  <b>{plan.upper()}</b>\n"
            f"💵 Monto: <b>{amount}/mes</b>\n"
            f"🔖 Ref:   <b>{ref}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Verifica en Wise y activa en /admin"
        )
        send_telegram(msg)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/categories")
def categories():
    return jsonify({"CRYPTO": list(cache["tickers"].keys()), "FOREX": [k for k,v in ALL_EXTERNAL.items() if v["cat"]=="FOREX"], "COMMODITIES": [k for k,v in ALL_EXTERNAL.items() if v["cat"]=="COMMODITIES"], "INDICES": [k for k,v in ALL_EXTERNAL.items() if v["cat"]=="INDICES"], "STOCKS": [k for k,v in ALL_EXTERNAL.items() if v["cat"]=="STOCKS"]})

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
threading.Thread(target=smc_scanner, daemon=True).start()
threading.Thread(target=paper_trading_thread, daemon=True).start()
threading.Thread(target=bot_polling, daemon=True).start()

threading.Thread(target=lambda: [
    __import__("time").sleep(1800) or journal_close_open_trades()
    for _ in iter(int, 1)
], daemon=True).start()
print("\n  ✅ Servidor listo en http://localhost:5001")
print("  → Abre nexus_elite.html en tu navegador\n")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
