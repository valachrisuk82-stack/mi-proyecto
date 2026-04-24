import requests, pandas as pd, time, os
from datetime import datetime

PAIRS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT"]
OUTPUT_DIR = "historical_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def download_pair(pair, interval="1h", years=5):
    print(f"Descargando {pair}...")
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = end_ms - (years * 365 * 24 * 3600 * 1000)
    all_data = []
    url = "https://api.binance.com/api/v3/klines"
    while start_ms < end_ms:
        r = requests.get(url, params={"symbol":pair,"interval":interval,"startTime":start_ms,"limit":1000}, timeout=10)
        data = r.json()
        if not data: break
        all_data.extend(data)
        start_ms = data[-1][0] + 1
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=["time","open","high","low","close","volume","ct","qav","nt","tbbav","tbqav","ignore"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    df = df[["time","open","high","low","close","volume"]]
    df.to_csv(f"{OUTPUT_DIR}/{pair}_{interval}.csv", index=False)
    print(f"  ✅ {pair}: {len(df)} velas guardadas")
    return df

for pair in PAIRS:
    download_pair(pair)
print("\n✅ Descarga completa!")
