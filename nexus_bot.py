import requests
import threading
import time
from datetime import datetime

# Importar desde el servidor principal
import sys
sys.path.insert(0, '.')

TOKEN   = "8683659808:AAGqxOiZUBnzhNWnk-ET5Cz7ZQKGPBUrHH0"
BASE    = f"https://api.telegram.org/bot{TOKEN}"
API_URL = "http://localhost:5001/api"

_offset = 0
_users  = {}

def send(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        import json
        data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    try:
        requests.post(f"{BASE}/sendMessage", data=data, timeout=5)
    except:
        pass

def answer_cb(cb_id):
    try:
        requests.post(f"{BASE}/answerCallbackQuery",
                     data={"callback_query_id": cb_id}, timeout=3)
    except:
        pass

def get_signal(pair):
    pair = pair.upper().replace("/", "")
    if not pair.endswith("USDT"):
        pair += "USDT"
    try:
        r = requests.get(f"https://api.binance.com/api/v3/ticker/price",
                        params={"symbol": pair}, timeout=5)
        price = float(r.json()["price"])

        ind_r = requests.get(f"{API_URL}/indicators/{pair}", timeout=5).json()
        ind   = ind_r.get("indicators", {})
        sig_d = ind_r.get("signal", {})

        sig  = sig_d.get("signal", "WAIT")
        conf = sig_d.get("confidence", 50)
        ml_s = sig_d.get("ml_score", 50)
        rsi  = ind.get("rsi", 50)
        atr  = ind.get("atr", price * 0.015)

        emoji = "🟢" if sig == "BUY" else "🔴" if sig == "SELL" else "🟡"

        if sig == "BUY":
            sl = round(price - atr * 1.5, 4)
            tp = round(price + atr * 3,   4)
        else:
            sl = round(price + atr * 1.5, 4)
            tp = round(price - atr * 3,   4)

        rr   = round(abs(tp - price) / max(0.0001, abs(price - sl)), 1)
        bars = "█" * (ml_s // 10) + "░" * (10 - ml_s // 10)

        lines = [
            f"{emoji} <b>NEXUS APEX — {pair}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Señal: <b>{sig}</b> ({conf}% confianza)",
            f"💰 Precio: <b>${price:,.4f}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 Entry:  <b>${price:,.4f}</b>",
            f"🛑 SL:     <b>${sl:,.4f}</b>",
            f"✅ TP:     <b>${tp:,.4f}</b>",
            f"📐 R:R     <b>1:{rr}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🧠 ML Score: {bars} {ml_s}/100",
            f"📈 RSI: {rsi:.1f}",
            "⚡ NEXUS APEX",
        ]
        msg = "\n".join(lines)

        kbd = [
            [{"text": "📊 Otro par", "callback_data": "menu_signal"},
             {"text": "🔍 Scan", "callback_data": "menu_scan"}],
            [{"text": "📈 Stats", "callback_data": "menu_stats"},
             {"text": "🏠 Menú", "callback_data": "menu_start"}],
        ]
        return msg, kbd
    except Exception as e:
        return f"❌ Error analizando {pair}: {e}", None

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    name    = msg["chat"].get("first_name", "Trader")
    text    = msg.get("text", "").strip()

    if chat_id not in _users:
        _users[chat_id] = {"name": name, "plan": "free"}

    if text.startswith("/start"):
        kbd = [
            [{"text": "📊 Señal en vivo",     "callback_data": "menu_signal"},
             {"text": "🔍 Escanear mercado",  "callback_data": "menu_scan"}],
            [{"text": "📈 Track Record",       "callback_data": "menu_stats"},
             {"text": "💎 Planes Premium",     "callback_data": "menu_plans"}],
        ]
        lines = [
            f"⚡ <b>Bienvenido a NEXUS APEX, {name}!</b>",
            "",
            "El sistema de trading más transparente del mercado.",
            "",
            "<b>Comandos:</b>",
            "• /signal BTC — Analizar un par",
            "• /scan — Mejores setups ahora",
            "• /stats — Track record",
            "• /pairs — Pares disponibles",
            "",
            "¿Qué quieres hacer?",
        ]
        send(chat_id, "\n".join(lines), kbd)

    elif text.startswith("/signal") or text.startswith("/s "):
        parts = text.split()
        pair  = parts[1] if len(parts) > 1 else "BTC"
        txt, kbd = get_signal(pair)
        send(chat_id, txt, kbd)

    elif text.startswith("/scan"):
        try:
            r = requests.get(f"{API_URL}/smc_scan", timeout=5).json()
            history = r.get("history", [])[:5]
            if not history:
                send(chat_id, "🔍 Sin setups activos ahora. El scanner corre cada 5 min.")
            else:
                lines = ["🔍 <b>MEJORES SETUPS ACTIVOS</b>", "━━━━━━━━━━━━━━━━━━━━"]
                for s in history:
                    e = "🟢" if s["signal"] == "LONG" else "🔴"
                    lines.append(f"{e} <b>{s['signal']} {s['pair'].replace('USDT','')}</b> — ${s['price']:,.2f} — {s['confidence']}%")
                kbd = [[{"text": "📊 Analizar par", "callback_data": "menu_signal"},
                        {"text": "🏠 Menú",         "callback_data": "menu_start"}]]
                send(chat_id, "\n".join(lines), kbd)
        except:
            send(chat_id, "❌ Error conectando al servidor.")

    elif text.startswith("/stats"):
        try:
            r = requests.get(f"{API_URL}/paper/stats", timeout=5).json()
            wr   = r.get("wr", 0)
            bars = "█" * (wr // 10) + "░" * (10 - wr // 10)
            lines = [
                "📈 <b>NEXUS APEX — TRACK RECORD</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                f"✅ Win Rate:     {bars} <b>{wr}%</b>",
                f"💰 Profit Factor: <b>{r.get('pf', '--')}</b>",
                f"📊 Net R:         <b>{'+' if r.get('net_r',0)>=0 else ''}{r.get('net_r',0)}R</b>",
                f"📉 Max DD:        <b>{r.get('max_dd',0)}R</b>",
                f"🔢 Total trades:  <b>{r.get('total',0)}</b>",
                f"📅 Días corriendo: <b>{r.get('days_running',0)}</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                "⚡ Paper Trading verificado",
            ]
            send(chat_id, "\n".join(lines))
        except:
            send(chat_id, "❌ Error obteniendo stats.")

    elif text.startswith("/track"):
        send(chat_id, "📊 <b>Track Record Público</b>\n\nhttps://mi-proyecto-production-29a8.up.railway.app/track")

    elif text.startswith("/pairs"):
        pairs = "BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, LINK, UNI, ATOM, LTC, MATIC"
        send(chat_id, f"💱 <b>Pares disponibles</b>\n\n{pairs}\n\nUsa: /signal BTC")

    else:
        pair = text.upper().replace("/", "").replace(" ", "")
        if len(pair) >= 3:
            txt, kbd = get_signal(pair)
            send(chat_id, txt, kbd)

def handle_callback(cb):
    chat_id = cb["message"]["chat"]["id"]
    name    = cb["message"]["chat"].get("first_name", "Trader")
    data    = cb.get("data", "")
    answer_cb(cb["id"])

    if data == "menu_start":
        handle_message({"chat": {"id": chat_id, "first_name": name}, "text": "/start"})
    elif data == "menu_signal":
        kbd = [
            [{"text": "BTC",  "callback_data": "signal_BTC"},
             {"text": "ETH",  "callback_data": "signal_ETH"},
             {"text": "BNB",  "callback_data": "signal_BNB"}],
            [{"text": "SOL",  "callback_data": "signal_SOL"},
             {"text": "XRP",  "callback_data": "signal_XRP"},
             {"text": "ADA",  "callback_data": "signal_ADA"}],
            [{"text": "DOGE", "callback_data": "signal_DOGE"},
             {"text": "LINK", "callback_data": "signal_LINK"},
             {"text": "AVAX", "callback_data": "signal_AVAX"}],
            [{"text": "🔙 Volver", "callback_data": "menu_start"}],
        ]
        send(chat_id, "📊 <b>Selecciona un par:</b>", kbd)
    elif data.startswith("signal_"):
        pair = data.replace("signal_", "")
        txt, kbd = get_signal(pair)
        send(chat_id, txt, kbd)
    elif data == "menu_scan":
        handle_message({"chat": {"id": chat_id, "first_name": name}, "text": "/scan"})
    elif data == "menu_stats":
        handle_message({"chat": {"id": chat_id, "first_name": name}, "text": "/stats"})
    elif data == "menu_plans":
        lines = [
            "💎 <b>Planes NEXUS APEX</b>",
            "",
            "🆓 <b>FREE</b> — Gratis",
            "• Track record público",
            "• Señales básicas",
            "",
            "⭐ <b>PRO</b> — $49/mes",
            "• Dashboard completo",
            "• Señales SMC en tiempo real",
            "• Alertas personalizadas",
            "• Risk Manager",
            "",
            "💎 <b>ELITE</b> — $99/mes",
            "• Todo lo de Pro",
            "• API access",
            "• Soporte prioritario",
        ]
        kbd = [[{"text": "🌐 Ver landing page",
                 "url": "https://mi-proyecto-production-29a8.up.railway.app/landing"}]]
        send(chat_id, "\n".join(lines), kbd)

def set_commands():
    try:
        requests.post(f"{BASE}/setMyCommands", json={"commands": [
            {"command": "start",  "description": "Menu principal"},
            {"command": "signal", "description": "Señal de un par (ej: /signal BTC)"},
            {"command": "scan",   "description": "Mejores setups ahora"},
            {"command": "stats",  "description": "Track record y estadisticas"},
            {"command": "track",  "description": "Link al track record publico"},
            {"command": "pairs",  "description": "Pares disponibles"},
        ]}, timeout=5)
        print("  ✅ Comandos del bot configurados")
    except:
        pass

def polling():
    global _offset
    set_commands()
    print("  🤖 Bot Telegram activo — escribe /start en @ProfitNexus_Bot")
    while True:
        try:
            r = requests.get(f"{BASE}/getUpdates",
                params={"offset": _offset, "timeout": 30,
                        "allowed_updates": ["message", "callback_query"]},
                timeout=35)
            for u in r.json().get("result", []):
                _offset = u["update_id"] + 1
                if "message" in u:
                    handle_message(u["message"])
                elif "callback_query" in u:
                    handle_callback(u["callback_query"])
        except Exception as e:
            time.sleep(5)
        time.sleep(0.5)

if __name__ == "__main__":
    polling()
