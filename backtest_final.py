import pandas as pd
import numpy as np

def calc_rsi(c, p=14):
    d=c.diff(); g=d.clip(lower=0).rolling(p).mean(); l=(-d.clip(upper=0)).rolling(p).mean()
    return 100-(100/(1+g/l))

def calc_ema(c, p): return c.ewm(span=p, adjust=False).mean()

def prepare(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df["rsi"] = calc_rsi(df["close"])
    e12=calc_ema(df["close"],12); e26=calc_ema(df["close"],26)
    line=e12-e26; df["macd_hist"]=line-calc_ema(line,9)
    df["ema9"]=calc_ema(df["close"],9)
    df["ema21"]=calc_ema(df["close"],21)
    df["ema50"]=calc_ema(df["close"],50)
    ll=df["low"].rolling(14).min(); hh=df["high"].rolling(14).max()
    df["stoch"]=(df["close"]-ll)/(hh-ll)*100
    df["vol_ratio"]=df["volume"]/df["volume"].rolling(20).mean()
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift()).abs(),
                    (df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    return df.dropna().reset_index(drop=True)

def backtest(pair, rr=2.0, atr_mult_sl=1.5):
    df = prepare(pair)
    wins=losses=0
    for i in range(len(df)-30):
        row = df.iloc[i]
        rsi=row["rsi"]; stoch=row["stoch"]; vol=row["vol_ratio"]
        macd_bull=row["macd_hist"]>0
        macd_bear=row["macd_hist"]<0
        ema_bull=row["ema9"]>row["ema21"]>row["ema50"]
        ema_bear=row["ema9"]<row["ema21"]<row["ema50"]
        buy  = (rsi<35) + macd_bull + ema_bull + (stoch<25) + (vol>1.2)
        sell = (rsi>65) + macd_bear + ema_bear + (stoch>75) + (vol>1.2)
        if buy>=3:   sig="BUY"
        elif sell>=3: sig="SELL"
        else: continue
        entry = df.iloc[i+1]["open"]
        atr = row["atr"]
        sl_dist = atr * atr_mult_sl
        tp_dist = sl_dist * rr
        sl = entry-sl_dist if sig=="BUY" else entry+sl_dist
        tp = entry+tp_dist if sig=="BUY" else entry-tp_dist
        result="LOSS"
        for j in range(i+2, min(i+30, len(df))):
            h,l=df.iloc[j]["high"],df.iloc[j]["low"]
            if sig=="BUY":
                if l<=sl: break
                if h>=tp: result="WIN"; break
            else:
                if h>=sl: break
                if l<=tp: result="WIN"; break
        if result=="WIN": wins+=1
        else: losses+=1
    total=wins+losses
    wr=round(wins/total*100,1) if total else 0
    exp = round(wins*rr - losses, 1)
    print(f"{pair}: {total} señales | WR:{wr}% | R:R 1:{rr} | Expectativa:{exp}")
    return wr, total

PAIRS=["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]
print("="*62)
print("NEXUS APEX — BACKTEST FINAL (ATR dinámico, 3+ confluencias)")
print("="*62)
wrs=[]; tots=[]
for p in PAIRS:
    wr,tot=backtest(p); wrs.append(wr); tots.append(tot)
avg=round(sum(wrs)/len(wrs),1)
print("="*62)
print(f"WR PROMEDIO: {avg}% | Señales/par: {round(sum(tots)/len(tots))}")
min_wr=round(100/(1+2),1)
if avg>=min_wr: print("✅ SISTEMA RENTABLE")
else: print(f"⚠️  Necesitas WR>{min_wr}% para ser rentable con R:R 1:2")
print("="*62)
