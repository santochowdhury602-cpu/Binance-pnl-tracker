"""
Minimal long-polling bot: /start replies with a button that opens the
Mini App (your Render URL). Run this as a separate Render "Background
Worker" service — it's independent from the FastAPI web service.
"""
import os
import time
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]  # e.g. https://your-app.onrender.com
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_webapp_button(chat_id: int):
    requests.post(f"{API}/sendMessage", json={
        "chat_id": chat_id,
        "text": "Your PnL, live from Binance.",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "📊 Open PnL Tracker", "web_app": {"url": WEBAPP_URL}}
            ]]
        }
    })


def poll():
    offset = 0
    print("Bot polling started.")
    while True:
        try:
            resp = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35).json()
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if msg and msg.get("text", "").startswith("/start"):
                    send_webapp_button(msg["chat"]["id"])
        except Exception as e:
            print("poll error:", e)
            time.sleep(3)


if __name__ == "__main__":
    poll()
