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

def calc_bb(closes, period=20):
    ma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    return (closes - (ma - 2*std)) / (4*std) * 100

def calc_stoch(high, low, close, period=14):
    ll = low.rolling(period).min()
    hh = high.rolling(period).max()
    return (close - ll) / (hh - ll) * 100

def prepare(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df["rsi"]  = calc_rsi(df["close"])
    df["macd_hist"], df["macd_line"] = calc_macd(df["close"])
    df["ema9"]  = calc_ema(df["close"], 9)
    df["ema21"] = calc_ema(df["close"], 21)
    df["ema50"] = calc_ema(df["close"], 50)
    df["bb"]    = calc_bb(df["close"])
    df["stoch"] = calc_stoch(df["high"], df["low"], df["close"])
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
    return df.dropna().reset_index(drop=True)

def score_row(row):
    rsi = row["rsi"]
    if rsi < 30:   s_rsi = 90
    elif rsi < 40: s_rsi = 70
    elif rsi < 45: s_rsi = 55
    elif rsi > 70: s_rsi = 10
    elif rsi > 60: s_rsi = 30
    elif rsi > 55: s_rsi = 45
    else:          s_rsi = 50

    s_macd = 80 if row["macd_hist"]>0 and row["macd_line"]>0 else 65 if row["macd_hist"]>0 else 20 if row["macd_hist"]<0 and row["macd_line"]<0 else 35

    if row["ema9"]>row["ema21"]>row["ema50"]:   s_ema=85
    elif row["ema9"]>row["ema21"]:               s_ema=65
    elif row["ema9"]<row["ema21"]<row["ema50"]: s_ema=15
    else:                                         s_ema=35

    bb = row["bb"]
    s_bb = 85 if bb<20 else 65 if bb<35 else 15 if bb>80 else 35 if bb>65 else 50

    stk = row["stoch"]
    s_stoch = 85 if stk<20 else 65 if stk<30 else 15 if stk>80 else 35 if stk>70 else 50

    vol = row["vol_ratio"]
    s_vol = 80 if vol>1.5 else 65 if vol>1.2 else 35 if vol<0.7 else 50

    # Pesos optimizados
    total = s_rsi*0.20 + s_macd*0.20 + s_ema*0.20 + s_bb*0.15 + s_stoch*0.15 + s_vol*0.10
    return total

def backtest(pair, sl_pct=0.02, tp_pct=0.04, threshold=62):
    df = prepare(pair)
    wins = 0; losses = 0
    buy_w = buy_l = sell_w = sell_l = 0
    for i in range(len(df)-20):
        score = score_row(df.iloc[i])
        if score >= threshold:   sig = "BUY"
        elif score <= (100-threshold): sig = "SELL"
        else: continue
        entry = df.iloc[i+1]["open"]
        sl = entry*(1-sl_pct) if sig=="BUY" else entry*(1+sl_pct)
        tp = entry*(1+tp_pct) if sig=="BUY" else entry*(1-tp_pct)
        result = "LOSS"
        for j in range(i+2, i+20):
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
    print(f"{pair}: {total} trades | WR:{wr}% | BUY:{bwr}% | SELL:{swr}% | SL:{sl_pct*100}% TP:{tp_pct*100}%")
    return wr

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]
print("="*60)
print("NEXUS APEX — BACKTESTING V2 (6 indicadores, 5 años)")
print("="*60)
results = [backtest(p) for p in PAIRS]
print("="*60)
print(f"WR PROMEDIO: {round(sum(results)/len(results),1)}%")
print("="*60)
