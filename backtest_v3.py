import pandas as pd
import numpy as np

def calc_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    return 100 - (100 / (1 + gain/loss))

def calc_ema(closes, period):
    return closes.ewm(span=period, adjust=False).mean()

def calc_macd(closes):
    e12 = calc_ema(closes, 12); e26 = calc_ema(closes, 26)
    line = e12 - e26
    return line - calc_ema(line, 9), line

def calc_stoch(high, low, close, period=14):
    ll = low.rolling(period).min()
    hh = high.rolling(period).max()
    return (close - ll) / (hh - ll) * 100

def prepare(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df["rsi"]   = calc_rsi(df["close"])
    df["macd_hist"], df["macd_line"] = calc_macd(df["close"])
    df["ema9"]  = calc_ema(df["close"], 9)
    df["ema21"] = calc_ema(df["close"], 21)
    df["ema50"] = calc_ema(df["close"], 50)
    df["stoch"] = calc_stoch(df["high"], df["low"], df["close"])
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
    return df.dropna().reset_index(drop=True)

def get_signal(row):
    rsi = row["rsi"]
    macd_bull = row["macd_hist"] > 0 and row["macd_line"] > 0
    macd_bear = row["macd_hist"] < 0 and row["macd_line"] < 0
    ema_bull = row["ema9"] > row["ema21"] > row["ema50"]
    ema_bear = row["ema9"] < row["ema21"] < row["ema50"]
    stoch_bull = row["stoch"] < 25
    stoch_bear = row["stoch"] > 75
    vol_ok = row["vol_ratio"] > 1.1

    # BUY: RSI oversold + MACD bull + EMA bull + volumen
    buy_score = sum([rsi < 40, macd_bull, ema_bull, stoch_bull, vol_ok])
    # SELL: RSI overbought + MACD bear + EMA bear + volumen  
    sell_score = sum([rsi > 60, macd_bear, ema_bear, stoch_bear, vol_ok])

    if buy_score >= 3:   return "BUY", buy_score
    if sell_score >= 3:  return "SELL", sell_score
    return "WAIT", 0

def backtest(pair, sl_pct=0.025, tp_pct=0.05):
    df = prepare(pair)
    wins = losses = buy_w = buy_l = sell_w = sell_l = 0
    for i in range(len(df)-25):
        sig, score = get_signal(df.iloc[i])
        if sig == "WAIT": continue
        entry = df.iloc[i+1]["open"]
        sl = entry*(1-sl_pct) if sig=="BUY" else entry*(1+sl_pct)
        tp = entry*(1+tp_pct) if sig=="BUY" else entry*(1-tp_pct)
        result = "LOSS"
        for j in range(i+2, i+25):
            h, l = df.iloc[j]["high"], df.iloc[j]["low"]
            if sig=="BUY":
                if l<=sl: break
                if h>=tp: result="WIN"; break
            else:
                if h>=sl: break
                if l<=tp: result="WIN"; break
        if result=="WIN":
            wins+=1
            if sig=="BUY": buy_w+=1
            else: sell_w+=1
        else:
            losses+=1
            if sig=="BUY": buy_l+=1
            else: sell_l+=1

    total = wins+losses
    wr = round(wins/total*100,1) if total else 0
    bwr = round(buy_w/max(buy_w+buy_l,1)*100,1)
    swr = round(sell_w/max(sell_w+sell_l,1)*100,1)
    print(f"{pair}: {total} señales | WR:{wr}% | BUY:{bwr}% | SELL:{swr}%")
    return wr

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]
print("="*60)
print("NEXUS APEX — BACKTESTING V3 (confluencia 3+ indicadores)")
print("="*60)
results = [backtest(p) for p in PAIRS]
print("="*60)
print(f"WR PROMEDIO: {round(sum(results)/len(results),1)}%")
print("="*60)
