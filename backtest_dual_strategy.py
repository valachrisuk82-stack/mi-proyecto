"""
Backtest de la estrategia DUAL M1+H1 — ultra selectiva para copy trading
Solo entra cuando:
  - H1 confirma tendencia (EMA9 > EMA21 o <)
  - M1 tiene cruce reciente de EMA9/EMA21 en la misma dirección
  - Volumen M1 > 1.2x el promedio
  - RSI M1 no está en zona extrema opuesta (evita comprar sobrecomprado / vender sobrevendido)
"""
import pandas as pd
import numpy as np

ASSETS = {
    "BTCUSDT": {"m1": "BTCUSDT_1m.csv", "h1": "BTCUSDT_1h.csv", "sl_mult": 1.5, "tp_mult": 2.25},
    "XAUUSD":  {"m1": "XAUUSD_1m.csv",  "h1": "XAUUSD_1h.csv",  "sl_mult": 2.0, "tp_mult": 3.0},
    "EURUSD":  {"m1": "EURUSD_1m.csv",  "h1": "EURUSD_1h.csv",  "sl_mult": 1.5, "tp_mult": 2.25},
    "GBPUSD":  {"m1": "GBPUSD_1m.csv",  "h1": "GBPUSD_1h.csv",  "sl_mult": 1.5, "tp_mult": 2.25},
}

def load(path):
    df = pd.read_csv(f"historical_data/{path}")
    df.columns = [c.lower() for c in df.columns]
    df['time'] = pd.to_datetime(df['time'])
    df['ema9']  = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain/loss))
    df['atr'] = (df['high'] - df['low']).rolling(14).mean()
    df['vol_ma'] = df['volume'].rolling(20).mean()
    return df.dropna().reset_index(drop=True)

def get_h1_trend_at(h1_df, ts, min_sep_pct=0.08):
    """Busca la última vela H1 cerrada antes de ts. Solo confirma tendencia
    si la separación entre EMA9/EMA21 es significativa (filtra mercado lateral)"""
    past = h1_df[h1_df['time'] <= ts]
    if past.empty: return None
    row = past.iloc[-1]
    sep_pct = abs(row['ema9'] - row['ema21']) / row['close'] * 100
    if sep_pct < min_sep_pct:
        return None  # EMAs muy pegadas = mercado lateral, no confiar en tendencia
    if row['ema9'] > row['ema21']: return "bull"
    if row['ema9'] < row['ema21']: return "bear"
    return None

def backtest_asset(name, cfg):
    m1 = load(cfg["m1"])
    h1 = load(cfg["h1"])
    trades = []
    in_trade = False
    direction, entry, sl, tp, entry_idx = None, None, None, None, None

    for i in range(22, len(m1)):
        row = m1.iloc[i]
        prev = m1.iloc[i-1]

        if in_trade:
            # Revisar si toca SL o TP en esta vela
            if direction == "long":
                if row['low'] <= sl:
                    trades.append({"pair": name, "dir": "LONG", "entry": entry, "sl": sl, "tp": tp,
                                   "outcome": "LOSS", "pnl_r": -1.0, "time": m1.iloc[entry_idx]['time']})
                    in_trade = False
                elif row['high'] >= tp:
                    trades.append({"pair": name, "dir": "LONG", "entry": entry, "sl": sl, "tp": tp,
                                   "outcome": "WIN", "pnl_r": cfg["tp_mult"]/cfg["sl_mult"], "time": m1.iloc[entry_idx]['time']})
                    in_trade = False
            else:
                if row['high'] >= sl:
                    trades.append({"pair": name, "dir": "SHORT", "entry": entry, "sl": sl, "tp": tp,
                                   "outcome": "LOSS", "pnl_r": -1.0, "time": m1.iloc[entry_idx]['time']})
                    in_trade = False
                elif row['low'] <= tp:
                    trades.append({"pair": name, "dir": "SHORT", "entry": entry, "sl": sl, "tp": tp,
                                   "outcome": "WIN", "pnl_r": cfg["tp_mult"]/cfg["sl_mult"], "time": m1.iloc[entry_idx]['time']})
                    in_trade = False
            continue

        # No estamos en trade — buscar señal de entrada
        ema9, ema21 = row['ema9'], row['ema21']
        prev9, prev21 = prev['ema9'], prev['ema21']
        rsi = row['rsi']
        atr = row['atr']
        if atr <= 0:
            continue
        # Forex (Yahoo) no tiene datos de volumen reales — solo exigir volumen en crypto
        is_forex = name in ("EURUSD", "GBPUSD", "XAUUSD")
        vol_ok = True if is_forex else row['volume'] > row['vol_ma'] * 1.2
        if not vol_ok:
            continue

        h1_trend = get_h1_trend_at(h1, row['time'])
        if h1_trend is None:
            continue

        # Confirmación de cruce 1-2 velas atrás (evita entrar en el ruido exacto del cruce)
        prev2 = m1.iloc[i-2]
        cross_bull_confirmed = (prev2['ema9'] <= prev2['ema21'] and prev9 > prev21 and ema9 > ema21)
        cross_bear_confirmed = (prev2['ema9'] >= prev2['ema21'] and prev9 < prev21 and ema9 < ema21)
        m1_sep_pct = abs(ema9 - ema21) / row['close'] * 100

        # LONG: H1 alcista fuerte + cruce M1 confirmado + separación M1 mínima + RSI sano
        if h1_trend == "bull" and cross_bull_confirmed and m1_sep_pct > 0.015 and 40 < rsi < 62:
            direction = "long"
            entry = row['close']
            sl = entry - atr * cfg["sl_mult"]
            tp = entry + atr * cfg["tp_mult"]
            entry_idx = i
            in_trade = True
        # SHORT: H1 bajista fuerte + cruce M1 confirmado + separación M1 mínima + RSI sano
        elif h1_trend == "bear" and cross_bear_confirmed and m1_sep_pct > 0.015 and 38 < rsi < 60:
            direction = "short"
            entry = row['close']
            sl = entry + atr * cfg["sl_mult"]
            tp = entry - atr * cfg["tp_mult"]
            entry_idx = i
            in_trade = True

    return trades

all_trades = []
for name, cfg in ASSETS.items():
    print(f"Backtesting {name}...")
    trades = backtest_asset(name, cfg)
    print(f"  {len(trades)} trades generados")
    all_trades.extend(trades)

df = pd.DataFrame(all_trades)
if df.empty:
    print("\n⚠️ No se generaron trades — la estrategia es demasiado selectiva para este período de datos")
else:
    df.to_csv("backtest_dual_strategy_results.csv", index=False)
    total = len(df)
    wins = (df['outcome'] == 'WIN').sum()
    win_rate = wins/total*100
    net_r = df['pnl_r'].sum()
    print(f"\n{'='*50}")
    print(f"RESULTADOS GLOBALES")
    print(f"{'='*50}")
    print(f"Total trades: {total}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Net R: {net_r:+.1f}R")
    print(f"\nPor activo:")
    for pair in df['pair'].unique():
        sub = df[df['pair']==pair]
        wr = (sub['outcome']=='WIN').sum()/len(sub)*100
        print(f"  {pair}: {len(sub)} trades, {wr:.1f}% win rate, {sub['pnl_r'].sum():+.1f}R")
