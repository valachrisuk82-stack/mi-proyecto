import pandas as pd
import numpy as np

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]

def load(pair):
    df = pd.read_csv(f"historical_data/{pair}_1h.csv")
    df.columns = [c.lower() for c in df.columns]
    return df.dropna().reset_index(drop=True)

def detect_smc(candles, i, swing=5):
    """Detecta OB, FVG y BOS en la ventana hasta índice i"""
    obs, fvgs, bos_list = [], [], []
    n = min(i+1, len(candles))
    window = candles.iloc[max(0,n-60):n]
    w = window.reset_index(drop=True)
    wn = len(w)

    # Order Blocks
    for j in range(2, wn-1):
        p, c, nx = w.iloc[j-1], w.iloc[j], w.iloc[j+1]
        if p.close < p.open and c.close > c.open and nx.close > nx.open and nx.close > p.high:
            obs.append({'type':'bull','high':p.high,'low':p.low,'idx':j-1})
        if p.close > p.open and c.close < c.open and nx.close < nx.open and nx.close < p.low:
            obs.append({'type':'bear','high':p.high,'low':p.low,'idx':j-1})

    # Fair Value Gaps
    for j in range(1, wn-1):
        a, c_bar = w.iloc[j-1], w.iloc[j+1]
        if c_bar.low > a.high:
            fvgs.append({'type':'bull','top':c_bar.low,'bot':a.high})
        if c_bar.high < a.low:
            fvgs.append({'type':'bear','top':a.low,'bot':c_bar.high})

    # BOS
    for j in range(swing, wn-swing):
        hi, lo = w.iloc[j].high, w.iloc[j].low
        is_sh = all(w.iloc[j-k-1].high < hi and w.iloc[j+k+1].high < hi for k in range(swing))
        is_sl = all(w.iloc[j-k-1].low > lo and w.iloc[j+k+1].low > lo for k in range(swing))
        if is_sh:
            for k in range(j+1, wn):
                if w.iloc[k].close > hi:
                    bos_list.append({'dir':'bull','price':hi})
                    break
        if is_sl:
            for k in range(j+1, wn):
                if w.iloc[k].close < lo:
                    bos_list.append({'dir':'bear','price':lo})
                    break

    return obs[-6:], fvgs[-6:], bos_list[-4:]

def check_signal(price, obs, fvgs, bos_list, atr):
    """Evalúa confluencia SMC y retorna señal"""
    # LONG
    conf = 0
    bull_ob  = next((o for o in obs  if o['type']=='bull' and o['low']<=price<=o['high']*1.003), None)
    bull_fvg = next((f for f in fvgs if f['type']=='bull' and f['bot']<=price<=f['top']*1.005), None)
    bull_bos = next((b for b in bos_list if b['dir']=='bull'), None)
    if bull_ob:  conf += 40
    if bull_fvg: conf += 30
    if bull_bos: conf += 30
    if conf >= 70 and bull_ob:
        sl = bull_ob['low'] - atr * 0.5
        tp = price + (price - sl) * 3
        return 'LONG', conf, price, sl, tp

    # SHORT
    conf = 0
    bear_ob  = next((o for o in obs  if o['type']=='bear' and o['low']*0.997<=price<=o['high']), None)
    bear_fvg = next((f for f in fvgs if f['type']=='bear' and f['bot']*0.995<=price<=f['top']), None)
    bear_bos = next((b for b in bos_list if b['dir']=='bear'), None)
    if bear_ob:  conf += 40
    if bear_fvg: conf += 30
    if bear_bos: conf += 30
    if conf >= 70 and bear_ob:
        sl = bear_ob['high'] + atr * 0.5
        tp = price - (sl - price) * 3
        return 'SHORT', conf, price, sl, tp

    return None, 0, 0, 0, 0

def backtest_smc(pair, max_bars=25):
    df = load(pair)
    atr_roll = (df['high'] - df['low']).rolling(14).mean()
    results = []
    in_trade = False
    cooldown = 0

    for i in range(60, len(df)-max_bars):
        if cooldown > 0:
            cooldown -= 1
            continue

        price = df.iloc[i]['close']
        atr = atr_roll.iloc[i]
        obs, fvgs, bos_list = detect_smc(df, i)
        sig, conf, entry, sl, tp = check_signal(price, obs, fvgs, bos_list, atr)

        if not sig:
            continue

        # Simular trade en las siguientes velas
        outcome = 'LOSS'
        pnl_r = -1.0
        for j in range(i+1, min(i+max_bars+1, len(df))):
            h, l = df.iloc[j]['high'], df.iloc[j]['low']
            if sig == 'LONG':
                if l <= sl: break
                if h >= tp: outcome = 'WIN'; pnl_r = 3.0; break
            else:
                if h >= sl: break
                if l <= tp: outcome = 'WIN'; pnl_r = 3.0; break

        results.append({
            'pair': pair, 'signal': sig, 'conf': conf,
            'entry': round(entry,4), 'sl': round(sl,4), 'tp': round(tp,4),
            'outcome': outcome, 'pnl_r': pnl_r,
            'time': df.iloc[i].get('time', i)
        })
        cooldown = 10  # evitar señales consecutivas

    return results

# ── MAIN ──
all_results = []
print("\n🔍 NEXUS APEX — SMC Backtest 1H")
print("="*60)

for pair in PAIRS:
    try:
        res = backtest_smc(pair)
        all_results.extend(res)
        if not res:
            print(f"  {pair}: sin señales")
            continue
        wins   = sum(1 for r in res if r['outcome']=='WIN')
        losses = sum(1 for r in res if r['outcome']=='LOSS')
        total  = wins + losses
        wr     = wins/total*100 if total else 0
        pf     = (wins*3) / losses if losses else float('inf')
        longs  = [r for r in res if r['signal']=='LONG']
        shorts = [r for r in res if r['signal']=='SHORT']
        print(f"  {pair:12} | Señales: {total:4} | WR: {wr:5.1f}% | PF: {pf:.2f} | L:{len(longs)} S:{len(shorts)}")
    except Exception as e:
        print(f"  {pair}: ERROR — {e}")

# Resumen global
print("\n" + "="*60)
wins_t   = sum(1 for r in all_results if r['outcome']=='WIN')
losses_t = sum(1 for r in all_results if r['outcome']=='LOSS')
total_t  = wins_t + losses_t
wr_t     = wins_t/total_t*100 if total_t else 0
pf_t     = (wins_t*3)/losses_t if losses_t else float('inf')
net_r    = sum(r['pnl_r'] for r in all_results)

print(f"  TOTAL señales : {total_t}")
print(f"  Win Rate      : {wr_t:.1f}%")
print(f"  Profit Factor : {pf_t:.2f}")
print(f"  Net R         : {net_r:+.1f}R")
print(f"  Longs WR      : {sum(1 for r in all_results if r['signal']=='LONG' and r['outcome']=='WIN') / max(1,sum(1 for r in all_results if r['signal']=='LONG'))*100:.1f}%")
print(f"  Shorts WR     : {sum(1 for r in all_results if r['signal']=='SHORT' and r['outcome']=='WIN') / max(1,sum(1 for r in all_results if r['signal']=='SHORT'))*100:.1f}%")
print("="*60)

# Guardar CSV
df_out = pd.DataFrame(all_results)
df_out.to_csv("backtest_smc_results.csv", index=False)
print(f"\n✅ Resultados guardados en backtest_smc_results.csv")
