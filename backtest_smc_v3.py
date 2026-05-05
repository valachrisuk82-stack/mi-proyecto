import pandas as pd
import numpy as np

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]

def load(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df.columns = [c.lower() for c in df.columns]
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema9']  = df['close'].ewm(span=9,  adjust=False).mean()
    delta = df['close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi']    = 100 - (100 / (1 + gain/loss))
    df['atr']    = (df['high'] - df['low']).rolling(14).mean()
    df['vol_ma'] = df['volume'].rolling(20).mean()
    return df.dropna().reset_index(drop=True)

def detect_smc_fast(df, i, window=50):
    start = max(0, i-window)
    w = df.iloc[start:i+1]
    o,h,l,c = w['open'].values,w['high'].values,w['low'].values,w['close'].values
    n = len(w)
    atr_val = df.iloc[i]['atr']
    obs, fvgs, bos_dirs = [], [], set()

    for j in range(1, n-1):
        if c[j-1]<o[j-1] and c[j]>o[j] and c[j+1]>h[j-1]:
            obs.append({'type':'bull','high':h[j-1],'low':l[j-1]})
        if c[j-1]>o[j-1] and c[j]<o[j] and c[j+1]<l[j-1]:
            obs.append({'type':'bear','high':h[j-1],'low':l[j-1]})

    for j in range(1, n-1):
        if l[j+1]>h[j-1] and (l[j+1]-h[j-1])>atr_val*0.2:
            fvgs.append({'type':'bull','top':l[j+1],'bot':h[j-1]})
        if h[j+1]<l[j-1] and (l[j-1]-h[j+1])>atr_val*0.2:
            fvgs.append({'type':'bear','top':l[j-1],'bot':h[j+1]})

    # BOS — precio rompe swing de últimas 15 velas
    s = max(0, n-15)
    if c[-1] > h[s:-1].max(): bos_dirs.add('bull')
    if c[-1] < l[s:-1].min(): bos_dirs.add('bear')

    return obs[-8:], fvgs[-8:], bos_dirs

def check_signal(price, obs, fvgs, bos_dirs, row):
    atr    = row['atr']
    ema9   = row['ema9']
    ema21  = row['ema21']
    ema50  = row['ema50']
    rsi    = row['rsi']
    vol_ok = row['volume'] > row['vol_ma'] * 1.1  # ligeramente más permisivo

    if not vol_ok: return None,0,0,0,0

    # LONG — necesita OB + (FVG o BOS) + tendencia
    if ema9 > ema21 and price > ema50 and rsi < 60:
        bull_ob  = next((o for o in obs  if o['type']=='bull' and o['low']<=price<=o['high']*1.004), None)
        bull_fvg = next((f for f in fvgs if f['type']=='bull' and f['bot']<=price<=f['top']*1.005), None)
        bull_bos = 'bull' in bos_dirs
        if bull_ob and (bull_fvg or bull_bos):
            conf = 70 + (30 if bull_fvg and bull_bos else 0)
            sl = bull_ob['low'] - atr*0.4
            tp = price + (price-sl)*3
            return 'LONG', conf, price, sl, tp

    # SHORT — necesita OB + (FVG o BOS) + tendencia
    if ema9 < ema21 and price < ema50 and rsi > 40:
        bear_ob  = next((o for o in obs  if o['type']=='bear' and o['low']*0.996<=price<=o['high']), None)
        bear_fvg = next((f for f in fvgs if f['type']=='bear' and f['bot']*0.995<=price<=f['top']), None)
        bear_bos = 'bear' in bos_dirs
        if bear_ob and (bear_fvg or bear_bos):
            conf = 70 + (30 if bear_fvg and bear_bos else 0)
            sl = bear_ob['high'] + atr*0.4
            tp = price - (sl-price)*3
            return 'SHORT', conf, price, sl, tp

    return None,0,0,0,0

def backtest_smc(pair, max_bars=30):
    df = load(pair)
    results, cooldown = [], 0
    for i in range(60, len(df)-max_bars):
        if cooldown > 0: cooldown -= 1; continue
        row = df.iloc[i]
        obs, fvgs, bos_dirs = detect_smc_fast(df, i)
        sig, conf, entry, sl, tp = check_signal(row['close'], obs, fvgs, bos_dirs, row)
        if not sig: continue
        outcome, pnl_r = 'LOSS', -1.0
        for j in range(i+1, min(i+max_bars+1, len(df))):
            h2,l2 = df.iloc[j]['high'], df.iloc[j]['low']
            if sig=='LONG':
                if l2<=sl: break
                if h2>=tp: outcome='WIN'; pnl_r=3.0; break
            else:
                if h2>=sl: break
                if l2<=tp: outcome='WIN'; pnl_r=3.0; break
        results.append({'pair':pair,'signal':sig,'conf':conf,'entry':round(entry,4),
                        'sl':round(sl,4),'tp':round(tp,4),'outcome':outcome,'pnl_r':pnl_r})
        cooldown = 15
    return results

all_results = []
print("\n🔍 NEXUS APEX — SMC Backtest v3 (OB + FVG o BOS)")
print("="*65)
for pair in PAIRS:
    try:
        res = backtest_smc(pair)
        all_results.extend(res)
        if not res: print(f"  {pair}: sin señales"); continue
        wins  = sum(1 for r in res if r['outcome']=='WIN')
        total = len(res)
        wr    = wins/total*100
        pf    = (wins*3)/(total-wins) if total>wins else float('inf')
        longs = sum(1 for r in res if r['signal']=='LONG')
        shorts= sum(1 for r in res if r['signal']=='SHORT')
        print(f"  {pair:12} | N:{total:4} | WR:{wr:5.1f}% | PF:{pf:.2f} | L:{longs} S:{shorts}")
    except Exception as e:
        print(f"  {pair}: ERROR — {e}")

print("\n"+"="*65)
wins_t  = sum(1 for r in all_results if r['outcome']=='WIN')
total_t = len(all_results)
losses_t= total_t - wins_t
wr_t    = wins_t/total_t*100 if total_t else 0
pf_t    = (wins_t*3)/losses_t if losses_t else float('inf')
net_r   = sum(r['pnl_r'] for r in all_results)
l_wr    = sum(1 for r in all_results if r['signal']=='LONG'  and r['outcome']=='WIN')/max(1,sum(1 for r in all_results if r['signal']=='LONG'))*100
s_wr    = sum(1 for r in all_results if r['signal']=='SHORT' and r['outcome']=='WIN')/max(1,sum(1 for r in all_results if r['signal']=='SHORT'))*100
print(f"  TOTAL señales : {total_t}")
print(f"  Win Rate      : {wr_t:.1f}%")
print(f"  Profit Factor : {pf_t:.2f}")
print(f"  Net R         : {net_r:+.1f}R")
print(f"  Longs WR      : {l_wr:.1f}%")
print(f"  Shorts WR     : {s_wr:.1f}%")
print("="*65)
pd.DataFrame(all_results).to_csv("backtest_smc_v3_results.csv", index=False)
print("\n✅ Guardado en backtest_smc_v3_results.csv")
