import os
import time
import requests
import threading
from datetime import datetime, timedelta
from supabase import create_client, Client
import feedparser
import hashlib

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "2"))
NEWS_INTERVAL = int(os.getenv("NEWS_INTERVAL", "60"))  # News every 60 seconds

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# English messages
WELCOME = (
    "👋 Welcome! You're now subscribed.\n\n"
    "You'll receive notifications when we publish AI news updates.\n"
    "Commands: /stop to unsubscribe, /status to check subscription"
)
GOODBYE = "👋 You've unsubscribed. To return: /start"
STATUS = "📬 You're subscribed. You'll receive AI news updates here."

# AI News RSS feeds (free sources)
AI_NEWS_FEEDS = [
    "https://feeds.feedburner.com/venturebeat/SZYF",  # VentureBeat AI
    "https://www.artificialintelligence-news.com/feed/",  # AI News
    "https://techcrunch.com/category/artificial-intelligence/feed/",  # TechCrunch AI
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",  # The Verge AI
    "https://blog.google/technology/ai/rss/",  # Google AI Blog
    "https://openai.com/blog/rss.xml",  # OpenAI Blog
]

def send(method, **params):
    try:
        r = requests.post(f"{TG_API}/{method}", json=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Error sending message: {e}")
        return None

def upsert_subscriber(msg):
    chat = msg.get("chat", {})
    user = msg.get("from", {})
    payload = {
        "telegram_user_id": chat["id"],
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "language_code": user.get("language_code"),
        "subscribed_at": datetime.utcnow().isoformat(),
    }
    try:
        supabase.table("subscribers").upsert(payload).execute()
        print(f"Subscriber added/updated: {chat['id']}")
    except Exception as e:
        print(f"Error upserting subscriber: {e}")

def get_subscribers():
    try:
        response = supabase.table("subscribers").select("telegram_user_id").execute()
        return [row["telegram_user_id"] for row in response.data]
    except Exception as e:
        print(f"Error getting subscribers: {e}")
        return []

def generate_news_hash(title, link):
    """Generate a hash for news item to avoid duplicates"""
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()

def is_news_sent(news_hash):
    """Check if news item was already sent"""
    try:
        response = supabase.table("sent_news").select("id").eq("news_hash", news_hash).execute()
        return len(response.data) > 0
    except Exception:
        return False

def mark_news_as_sent(news_hash, title):
    """Mark news item as sent"""
    try:
        payload = {
            "news_hash": news_hash,
            "title": title,
            "sent_at": datetime.utcnow().isoformat()
        }
        supabase.table("sent_news").insert(payload).execute()
    except Exception as e:
        print(f"Error marking news as sent: {e}")

def fetch_ai_news():
    """Fetch latest AI news from multiple sources"""
    news_items = []
    
    for feed_url in AI_NEWS_FEEDS:
        try:
            print(f"Fetching from: {feed_url}")
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:3]:  # Get top 3 from each feed
                # Filter for recent news (last 24 hours)
                if hasattr(entry, 'published_parsed'):
                    pub_time = datetime(*entry.published_parsed[:6])
                    if datetime.now() - pub_time > timedelta(hours=24):
                        continue
                
                news_hash = generate_news_hash(entry.title, entry.link)
                
                # Skip if already sent
                if is_news_sent(news_hash):
                    continue
                
                news_item = {
                    "title": entry.title,
                    "link": entry.link,
                    "summary": getattr(entry, 'summary', '')[:200] + "..." if len(getattr(entry, 'summary', '')) > 200 else getattr(entry, 'summary', ''),
                    "source": feed.feed.title if hasattr(feed, 'feed') else "AI News",
                    "hash": news_hash
                }
                news_items.append(news_item)
                
        except Exception as e:
            print(f"Error fetching from {feed_url}: {e}")
            continue
    
    return news_items[:5]  # Return top 5 latest news

def format_news_message(news_item):
    """Format news item for Telegram"""
    message = f"🤖 **AI News Update**\n\n"
    message += f"**{news_item['title']}**\n\n"
    if news_item['summary']:
        message += f"{news_item['summary']}\n\n"
    message += f"🔗 [Read more]({news_item['link']})\n"
    message += f"📰 Source: {news_item['source']}"
    
    return message

def broadcast_news():
    """Fetch and broadcast AI news to all subscribers"""
    print("Fetching AI news...")
    news_items = fetch_ai_news()
    
    if not news_items:
        print("No new AI news found")
        return
    
    subscribers = get_subscribers()
    print(f"Broadcasting to {len(subscribers)} subscribers")
    
    for news_item in news_items:
        message = format_news_message(news_item)
        
        # Send to all subscribers
        for chat_id in subscribers:
            result = send(
                "sendMessage",
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=False
            )
            
            if result:
                print(f"News sent to {chat_id}")
            else:
                print(f"Failed to send news to {chat_id}")
            
            time.sleep(0.1)  # Small delay to avoid rate limiting
        
        # Mark as sent
        mark_news_as_sent(news_item['hash'], news_item['title'])
        print(f"News marked as sent: {news_item['title'][:50]}...")

def news_worker():
    """Background worker for news broadcasting"""
    print(f"News worker started - will send news every {NEWS_INTERVAL} seconds")
    while True:
        try:
            broadcast_news()
        except Exception as e:
            print(f"Error in news worker: {e}")
        
        print(f"Waiting {NEWS_INTERVAL} seconds for next news cycle...")
        time.sleep(NEWS_INTERVAL)

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
        try:
            supabase.table("subscribers").delete().eq("telegram_user_id", chat_id).execute()
            send("sendMessage", chat_id=chat_id, text=GOODBYE, disable_web_page_preview=True)
            print(f"Subscriber removed: {chat_id}")
        except Exception as e:
            print(f"Error removing subscriber: {e}")
    
    elif text.startswith("/status"):
        send("sendMessage", chat_id=chat_id, text=STATUS, disable_web_page_preview=True)
    
    elif text.startswith("/news"):
        # Manual news request
        print(f"Manual news request from {chat_id}")
        news_items = fetch_ai_news()
        if news_items:
            for news_item in news_items[:1]:  # Send just the latest
                message = format_news_message(news_item)
                send("sendMessage", chat_id=chat_id, text=message, parse_mode="Markdown")
        else:
            send("sendMessage", chat_id=chat_id, text="No recent AI news available right now.")

def create_tables():
    """Create necessary database tables"""
    try:
        # This would need to be run once to create the sent_news table
        # You can run this SQL in your Supabase dashboard:
        print("Make sure to create the 'sent_news' table in Supabase with columns:")
        print("- id (uuid, primary key)")
        print("- news_hash (text, unique)")
        print("- title (text)")
        print("- sent_at (timestamp)")
    except Exception as e:
        print(f"Note: {e}")

def main():
    print("Starting AI News Telegram Bot...")
    
    # Start news worker in background thread
    news_thread = threading.Thread(target=news_worker, daemon=True)
    news_thread.start()
    
    # Main bot loop
    offset = None
    print("Bot polling started...")
    
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
                
        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(2)
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    required_vars = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TELEGRAM_TOKEN")
    for var in required_vars:
        if not os.environ.get(var):
            raise SystemExit(f"Missing env var: {var}")
    
    # Install required packages
    print("Required packages: pip install supabase requests feedparser")
    
    create_tables()
    main()