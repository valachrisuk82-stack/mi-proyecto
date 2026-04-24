import pandas as pd
import numpy as np

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
    signal = calc_ema(line, 9)
    hist = line - signal
    return hist

def ml_score(row):
    scores = {}
    rsi = row["rsi"]
    if rsi < 30:   scores["rsi"] = 90
    elif rsi < 40: scores["rsi"] = 70
    elif rsi < 45: scores["rsi"] = 55
    elif rsi > 70: scores["rsi"] = 10
    elif rsi > 60: scores["rsi"] = 30
    elif rsi > 55: scores["rsi"] = 45
    else:          scores["rsi"] = 50

    hist = row["macd_hist"]
    if hist > 0:   scores["macd"] = 70
    else:          scores["macd"] = 30

    if row["ema9"] > row["ema21"] > row["ema50"]: scores["ema"] = 85
    elif row["ema9"] > row["ema21"]:              scores["ema"] = 65
    elif row["ema9"] < row["ema21"] < row["ema50"]: scores["ema"] = 15
    else:                                          scores["ema"] = 35

    weights = {"rsi":0.35, "macd":0.35, "ema":0.30}
    total = sum(scores[k]*weights[k] for k in scores)
    if total >= 60:   return "BUY", total
    elif total <= 40: return "SELL", total
    else:             return "WAIT", total

def backtest(pair, sl_pct=0.015, tp_pct=0.03):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df["rsi"] = calc_rsi(df["close"])
    df["macd_hist"] = calc_macd(df["close"])
    df["ema9"] = calc_ema(df["close"], 9)
    df["ema21"] = calc_ema(df["close"], 21)
    df["ema50"] = calc_ema(df["close"], 50)
    df = df.dropna()

    trades = []
    for i in range(len(df)-1):
        row = df.iloc[i]
        sig, score = ml_score(row)
        if sig == "WAIT": continue
        entry = df.iloc[i+1]["open"]
        sl = entry*(1-sl_pct) if sig=="BUY" else entry*(1+sl_pct)
        tp = entry*(1+tp_pct) if sig=="BUY" else entry*(1-tp_pct)
        # Simular resultado en las siguientes 12 velas
        result = "OPEN"
        for j in range(i+2, min(i+14, len(df))):
            high = df.iloc[j]["high"]
            low  = df.iloc[j]["low"]
            if sig == "BUY":
                if low <= sl:  result = "LOSS"; break
                if high >= tp: result = "WIN";  break
            else:
                if high >= sl: result = "LOSS"; break
                if low <= tp:  result = "WIN";  break
        if result == "OPEN": result = "LOSS"
        trades.append({"signal":sig,"score":score,"result":result})

    df_t = pd.DataFrame(trades)
    total = len(df_t)
    wins  = len(df_t[df_t["result"]=="WIN"])
    wr    = round(wins/total*100, 1) if total else 0
    buy_wr  = round(len(df_t[(df_t["signal"]=="BUY")&(df_t["result"]=="WIN")])/max(len(df_t[df_t["signal"]=="BUY"]),1)*100,1)
    sell_wr = round(len(df_t[(df_t["signal"]=="SELL")&(df_t["result"]=="WIN")])/max(len(df_t[df_t["signal"]=="SELL"]),1)*100,1)
    print(f"{pair}: {total} trades | WR: {wr}% | BUY: {buy_wr}% | SELL: {sell_wr}%")
    return wr, buy_wr, sell_wr

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]
print("=" * 55)
print("NEXUS APEX — BACKTESTING 5 AÑOS")
print("=" * 55)
results = []
for pair in PAIRS:
    wr, bwr, swr = backtest(pair)
    results.append(wr)
print("=" * 55)
print(f"WR PROMEDIO: {round(sum(results)/len(results),1)}%")
print("=" * 55)
