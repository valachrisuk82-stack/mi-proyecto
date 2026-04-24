import pandas as pd
import numpy as np
from itertools import product

def calc_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_ema(closes, period):
    return closes.ewm(span=period, adjust=False).mean()

def calc_macd(closes):
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    line = ema12 - ema26
    return line - calc_ema(line, 9)

def prepare(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df["rsi"] = calc_rsi(df["close"])
    df["macd_hist"] = calc_macd(df["close"])
    df["ema9"] = calc_ema(df["close"], 9)
    df["ema21"] = calc_ema(df["close"], 21)
    df["ema50"] = calc_ema(df["close"], 50)
    return df.dropna().reset_index(drop=True)

def test_weights(df, w_rsi, w_macd, w_ema, sl_pct=0.015, tp_pct=0.03):
    wins = 0; total = 0
    for i in range(len(df)-14):
        row = df.iloc[i]
        rsi = row["rsi"]
        if rsi < 30:   s_rsi = 90
        elif rsi < 40: s_rsi = 70
        elif rsi < 45: s_rsi = 55
        elif rsi > 70: s_rsi = 10
        elif rsi > 60: s_rsi = 30
        elif rsi > 55: s_rsi = 45
        else:          s_rsi = 50
        s_macd = 70 if row["macd_hist"] > 0 else 30
        if row["ema9"]>row["ema21"]>row["ema50"]:   s_ema=85
        elif row["ema9"]>row["ema21"]:               s_ema=65
        elif row["ema9"]<row["ema21"]<row["ema50"]: s_ema=15
        else:                                         s_ema=35
        score = s_rsi*w_rsi + s_macd*w_macd + s_ema*w_ema
        if score >= 60:   sig = "BUY"
        elif score <= 40: sig = "SELL"
        else: continue
        entry = df.iloc[i+1]["open"]
        sl = entry*(1-sl_pct) if sig=="BUY" else entry*(1+sl_pct)
        tp = entry*(1+tp_pct) if sig=="BUY" else entry*(1-tp_pct)
        for j in range(i+2, i+14):
            h, l = df.iloc[j]["high"], df.iloc[j]["low"]
            if sig=="BUY":
                if l<=sl: break
                if h>=tp: wins+=1; break
            else:
                if h>=sl: break
                if l<=tp: wins+=1; break
        total += 1
    return wins/total if total > 0 else 0

print("Cargando datos...")
dfs = {p: prepare(p) for p in ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]}

print("Optimizando pesos...")
best_wr = 0; best_weights = None
steps = [0.2, 0.3, 0.4, 0.5]
for w_rsi in steps:
    for w_macd in steps:
        w_ema = round(1 - w_rsi - w_macd, 1)
        if w_ema < 0.1 or w_ema > 0.6: continue
        total_wr = sum(test_weights(df, w_rsi, w_macd, w_ema) for df in dfs.values()) / len(dfs)
        if total_wr > best_wr:
            best_wr = total_wr
            best_weights = (w_rsi, w_macd, w_ema)
        print(f"  RSI:{w_rsi} MACD:{w_macd} EMA:{w_ema} → WR:{round(total_wr*100,1)}%")

print(f"\n✅ MEJORES PESOS:")
print(f"  RSI:  {best_weights[0]}")
print(f"  MACD: {best_weights[1]}")
print(f"  EMA:  {best_weights[2]}")
print(f"  WR:   {round(best_wr*100,1)}%")
