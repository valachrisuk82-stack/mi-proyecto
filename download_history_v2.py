"""
Descarga datos históricos para backtest de la estrategia EMA dual M1+H1
- BTC: Binance API (M1 últimos 6 meses + H1 ya existe)
- ORO, EUR/USD, GBP/USD: Yahoo Finance (M1 últimos 7 días + H1 últimos 2 años)
"""
import requests, pandas as pd, time, os
from datetime import datetime

OUTPUT_DIR = "historical_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def download_binance(pair, interval, days):
    print(f"Descargando {pair} {interval} ({days} días)...")
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = end_ms - (days * 24 * 3600 * 1000)
    all_data = []
    url = "https://api.binance.com/api/v3/klines"
    while start_ms < end_ms:
        r = requests.get(url, params={"symbol": pair, "interval": interval,
                                       "startTime": start_ms, "limit": 1000}, timeout=10)
        data = r.json()
        if not data: break
        all_data.extend(data)
        start_ms = data[-1][0] + 1
        time.sleep(0.1)
        if len(all_data) % 5000 == 0:
            print(f"  ... {len(all_data)} velas")
    df = pd.DataFrame(all_data, columns=["time","open","high","low","close","volume",
                                          "ct","qav","nt","tbbav","tbqav","ignore"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    df = df[["time","open","high","low","close","volume"]]
    df.to_csv(f"{OUTPUT_DIR}/{pair}_{interval}.csv", index=False)
    print(f"  ✅ {pair} {interval}: {len(df)} velas guardadas")
    return df

def download_yahoo(symbol, yf_ticker, tf, rng):
    """tf: 1m, 1h | rng: 7d, 2y etc — limitado por Yahoo según intervalo"""
    print(f"Descargando {symbol} ({yf_ticker}) {tf}...")
    tf_map = {"1m": "1m", "1h": "60m"}
    yf_tf = tf_map.get(tf, tf)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
    r = requests.get(url, params={"interval": yf_tf, "range": rng, "includePrePost": False},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    data = r.json()
    result = data["chart"]["result"][0]
    quotes = result["indicators"]["quote"][0]
    timestamps = result.get("timestamp", [])
    rows = []
    for t, o, h, l, c, v in zip(timestamps, quotes.get("open", []), quotes.get("high", []),
                                  quotes.get("low", []), quotes.get("close", []), quotes.get("volume", [])):
        if c is None or o is None: continue
        rows.append({"time": pd.to_datetime(t, unit="s"), "open": float(o),
                     "high": float(h) if h else float(c), "low": float(l) if l else float(c),
                     "close": float(c), "volume": float(v) if v else 0.0})
    if not rows:
        print(f"  ⚠️ Sin datos para {symbol} {tf}")
        return None
    df = pd.DataFrame(rows)
    fname = "1h" if tf == "1h" else "1m"
    df.to_csv(f"{OUTPUT_DIR}/{symbol}_{fname}.csv", index=False)
    print(f"  ✅ {symbol} {tf}: {len(df)} velas guardadas")
    return df

# ── BTC en M1 (últimos 25 días — límite práctico de Binance para no saturar) ──
download_binance("BTCUSDT", "1m", days=25)

# ── ORO, EUR/USD, GBP/USD ──
FX_SYMBOLS = [
    ("XAUUSD", "GC=F"),
    ("EURUSD", "EURUSD=X"),
    ("GBPUSD", "GBPUSD=X"),
]
for sym, ticker in FX_SYMBOLS:
    download_yahoo(sym, ticker, "1h", "2y")
    time.sleep(1)
    download_yahoo(sym, ticker, "1m", "7d")
    time.sleep(1)

print("\n✅ Descarga completa!")
