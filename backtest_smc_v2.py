import pandas as pd
import numpy as np

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]

def load(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df.columns = [c.lower() for c in df.columns]
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    delta = df['close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain/loss))
    df['atr']    = (df['high'] - df['low']).rolling(14).mean()
    df['vol_ma'] = df['volume'].rolling(20).mean()
    return df.dropna().reset_index(drop=True)

def detect_smc_fast(df, i, window=40):
    """Versión vectorizada — solo mira las últimas `window` velas"""
    start = max(0, i - window)
    w = df.iloc[start:i+1]
    o, h, l, c = w['open'].values, w['high'].values, w['low'].values, w['close'].values
    n = len(w)

    obs, fvgs, bos_dirs = [], [], set()

    # Order Blocks (últimas 40 velas)
    for j in range(1, n-1):
        if c[j-1]<o[j-1] and c[j]>o[j] and c[j+1]>h[j-1]:
            obs.append({'type':'bull','high':h[j-1],'low':l[j-1]})
        if c[j-1]>o[j-1] and c[j]<o[j] and c[j+1]<l[j-1]:
            obs.append({'type':'bear','high':h[j-1],'low':l[j-1]})

    # FVG (últimas 40 velas)
    atr_val = df.iloc[i]['atr']
    for j in range(1, n-1):
        if l[j+1] > h[j-1] and (l[j+1]-h[j-1]) > atr_val*0.3:
            fvgs.append({'type':'bull','top':l[j+1],'bot':h[j-1]})
        if h[j+1] < l[j-1] and (l[j-1]-h[j+1]) > atr_val*0.3:
            fvgs.append({'type':'bear','top':l[j-1],'bot':h[j+1]})

    # BOS — simplificado: swing high/low de las últimas 20 velas
    s = max(0, n-20)
    swing_high = h[s:].max()
    swing_low  = l[s:].min()
    cur_close  = c[-1]
    if cur_close > swing_high * 0.998: bos_dirs.add('bull')
    if cur_close < swing_low  * 1.002: bos_dirs.add('bear')

    return obs[-6:], fvgs[-6:], bos_dirs

def check_signal(price, obs, fvgs, bos_dirs, row):
    atr    = row['atr']
    ema21  = row['ema21']
    ema50  = row['ema50']
    rsi    = row['rsi']
    vol_ok = row['volume'] > row['vol_ma'] * 1.2

    if not vol_ok: return None,0,0,0,0

    # LONG — tendencia alcista + RSI neutro
    if price > ema21 > ema50 and 35 < rsi < 65:
        bull_ob  = next((o for o in obs  if o['type']=='bull' and o['low']<=price<=o['high']*1.003), None)
        bull_fvg = next((f for f in fvgs if f['type']=='bull' and f['bot']<=price<=f['top']*1.004), None)
        if bull_ob and bull_fvg and 'bull' in bos_dirs:
            sl = bull_ob['low'] - atr*0.3
            tp = price + (price-sl)*3
            return 'LONG', 100, price, sl, tp

    # SHORT — tendencia bajista + RSI neutro
    if price < ema21 < ema50 and 35 < rsi < 65:
        bear_ob  = next((o for o in obs  if o['type']=='bear' and o['low']*0.997<=price<=o['high']), None)
        bear_fvg = next((f for f in fvgs if f['type']=='bear' and f['bot']*0.996<=price<=f['top']), None)
        if bear_ob and bear_fvg and 'bear' in bos_dirs:
            sl = bear_ob['high'] + atr*0.3
            tp = price - (sl-price)*3
            return 'SHORT', 100, price, sl, tp

    return None,0,0,0,0

def backtest_smc(pair, max_bars=30):
    df = load(pair)
    results, cooldown = [], 0
    for i in range(60, len(df)-max_bars):
        if cooldown > 0: cooldown -= 1; continue
        row   = df.iloc[i]
        price = row['close']
        obs, fvgs, bos_dirs = detect_smc_fast(df, i)
        sig, conf, entry, sl, tp = check_signal(price, obs, fvgs, bos_dirs, row)
        if not sig: continue
        outcome, pnl_r = 'LOSS', -1.0
        for j in range(i+1, min(i+max_bars+1, len(df))):
            h2, l2 = df.iloc[j]['high'], df.iloc[j]['low']
            if sig=='LONG':
                if l2<=sl: break
                if h2>=tp: outcome='WIN'; pnl_r=3.0; break
            else:
                if h2>=sl: break
                if l2<=tp: outcome='WIN'; pnl_r=3.0; break
        results.append({'pair':pair,'signal':sig,'entry':round(entry,4),
                        'sl':round(sl,4),'tp':round(tp,4),'outcome':outcome,'pnl_r':pnl_r})
        cooldown = 20
    return results

# ── MAIN ──
all_results = []
print("\n🔍 NEXUS APEX — SMC Backtest v2 (Filtros Estrictos)")
print("="*65)
for pair in PAIRS:
    try:
        res = backtest_smc(pair)
        all_results.extend(res)
        if not res: print(f"  {pair}: sin señales"); continue
        wins   = sum(1 for r in res if r['outcome']=='WIN')
        losses = sum(1 for r in res if r['outcome']=='LOSS')
        total  = wins+losses
        wr     = wins/total*100 if total else 0
        pf     = (wins*3)/losses if losses else float('inf')
        longs  = [r for r in res if r['signal']=='LONG']
        shorts = [r for r in res if r['signal']=='SHORT']
        print(f"  {pair:12} | N:{total:4} | WR:{wr:5.1f}% | PF:{pf:.2f} | L:{len(longs)} S:{len(shorts)}")
    except Exception as e:
        print(f"  {pair}: ERROR — {e}")

print("\n"+"="*65)
wins_t   = sum(1 for r in all_results if r['outcome']=='WIN')
losses_t = sum(1 for r in all_results if r['outcome']=='LOSS')
total_t  = wins_t+losses_t
wr_t     = wins_t/total_t*100 if total_t else 0
pf_t     = (wins_t*3)/losses_t if losses_t else float('inf')
net_r    = sum(r['pnl_r'] for r in all_results)
l_wr = sum(1 for r in all_results if r['signal']=='LONG'  and r['outcome']=='WIN')/max(1,sum(1 for r in all_results if r['signal']=='LONG'))*100
s_wr = sum(1 for r in all_results if r['signal']=='SHORT' and r['outcome']=='WIN')/max(1,sum(1 for r in all_results if r['signal']=='SHORT'))*100
print(f"  TOTAL señales : {total_t}")
print(f"  Win Rate      : {wr_t:.1f}%")
print(f"  Profit Factor : {pf_t:.2f}")
print(f"  Net R         : {net_r:+.1f}R")
print(f"  Longs WR      : {l_wr:.1f}%")
print(f"  Shorts WR     : {s_wr:.1f}%")
print("="*65)
pd.DataFrame(all_results).to_csv("backtest_smc_v2_results.csv", index=False)
print("\n✅ Guardado en backtest_smc_v2_results.csv")
