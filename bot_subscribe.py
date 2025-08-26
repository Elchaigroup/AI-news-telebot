import os, time, requests
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service role, server-only
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "2"))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

WELCOME = (
    "👋 Benvenuto! Sei iscritto.\n\n"
    "Da ora riceverai notifiche quando pubblichiamo aggiornamenti.\n"
    "Comandi: /stop per annullare"
)
GOODBYE = "👋 Hai annullato l’iscrizione. Per tornare: /start"
STATUS = "📬 Sei iscritto. Riceverai aggiornamenti qui."

def send(method, **params):
    r = requests.post(f"{TG_API}/{method}", json=params, timeout=30)
    r.raise_for_status()
    return r.json()

def upsert_subscriber(msg):
    chat = msg.get("chat", {})
    user = msg.get("from", {})
    payload = {
        "telegram_user_id": chat["id"],
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "language_code": user.get("language_code"),
    }
    supabase.table("subscribers").upsert(payload).execute()

def handle_update(u):
    msg = u.get("message") or u.get("edited_message")
    if not msg: 
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip().lower()

    if text.startswith("/start"):
        upsert_subscriber(msg)
        send("sendMessage", chat_id=chat_id, text=WELCOME, disable_web_page_preview=True)

    elif text.startswith("/stop"):
        supabase.table("subscribers").delete().eq("telegram_user_id", chat_id).execute()
        send("sendMessage", chat_id=chat_id, text=GOODBYE, disable_web_page_preview=True)

    elif text.startswith("/status"):
        send("sendMessage", chat_id=chat_id, text=STATUS, disable_web_page_preview=True)

def main():
    offset = None
    while True:
        try:
            resp = requests.get(
                f"{TG_API}/getUpdates",
                params={"timeout": 25, "offset": offset},
                timeout=30
            ).json()
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
        except Exception:
            time.sleep(2)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    for var in ("SUPABASE_URL","SUPABASE_SERVICE_KEY","TELEGRAM_TOKEN"):
        if not os.environ.get(var):
            raise SystemExit(f"Missing env var: {var}")
    main()
