import os
import time
import requests
import threading
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import feedparser
import hashlib
import logging
import random

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "2"))
NEWS_INTERVAL = int(os.getenv("NEWS_INTERVAL", "300"))  # 5 minutes default (300 seconds)
MAX_RETRIES = 3

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# English messages
WELCOME = (
    "👋 Welcome! You're now subscribed to AI News Bot.\n\n"
    "🤖 You'll receive the latest AI news updates regularly.\n"
    "📱 Commands: /stop to unsubscribe, /status to check subscription, /news for latest update"
)
GOODBYE = "👋 You've unsubscribed. To return: /start"
STATUS = "📬 You're subscribed! You'll receive AI news updates here."

# Enhanced AI News RSS feeds with more sources for 24/7 coverage
AI_NEWS_FEEDS = [
    {"url": "https://feeds.feedburner.com/venturebeat/SZYF", "name": "VentureBeat AI"},
    {"url": "https://www.artificialintelligence-news.com/feed/", "name": "AI News"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "name": "TechCrunch AI"},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "name": "The Verge AI"},
    {"url": "https://blog.google/technology/ai/rss/", "name": "Google AI Blog"},
    {"url": "https://openai.com/blog/rss.xml", "name": "OpenAI Blog"},
    {"url": "https://blogs.microsoft.com/ai/feed/", "name": "Microsoft AI Blog"},
    {"url": "https://aws.amazon.com/blogs/machine-learning/feed/", "name": "AWS ML Blog"},
    {"url": "https://research.facebook.com/feed/", "name": "Meta Research"},
    {"url": "https://deepmind.com/blog/rss.xml", "name": "DeepMind"},
    {"url": "https://blogs.nvidia.com/feed/", "name": "NVIDIA Blog"},
    {"url": "https://www.wired.com/feed/category/business/artificial-intelligence/latest/rss", "name": "WIRED AI"},
    {"url": "https://spectrum.ieee.org/rss/topic/artificial-intelligence", "name": "IEEE Spectrum AI"},
    {"url": "https://www.zdnet.com/topic/artificial-intelligence/rss.xml", "name": "ZDNet AI"},
]

def safe_request(func, *args, max_retries=MAX_RETRIES, **kwargs):
    """Wrapper for safe API requests with retry logic"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                logger.error(f"Request failed after {max_retries} attempts: {e}")
                return None
            time.sleep(2 ** attempt)  # Exponential backoff
    return None

def send(method, **params):
    def _send():
        r = requests.post(f"{TG_API}/{method}", json=params, timeout=30)
        r.raise_for_status()
        return r.json()
    
    return safe_request(_send)

def upsert_subscriber(msg):
    chat = msg.get("chat", {})
    user = msg.get("from", {})
    payload = {
        "telegram_user_id": chat["id"],
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "language_code": user.get("language_code"),
        "subscribed_at": datetime.now(timezone.utc).isoformat(),
    }
    
    def _upsert():
        return supabase.table("subscribers").upsert(payload).execute()
    
    result = safe_request(_upsert)
    if result:
        logger.info(f"Subscriber added/updated: {chat['id']}")
    return result

def get_subscribers():
    def _get_subscribers():
        response = supabase.table("subscribers").select("telegram_user_id").execute()
        return [row["telegram_user_id"] for row in response.data]
    
    result = safe_request(_get_subscribers)
    return result if result else []

def generate_news_hash(title, link):
    """Generate a hash for news item to avoid duplicates"""
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()

def is_news_sent(news_hash):
    """Check if news item was already sent"""
    def _check():
        response = supabase.table("sent_news").select("id").eq("news_hash", news_hash).execute()
        return len(response.data) > 0
    
    result = safe_request(_check)
    return result if result is not None else False

def mark_news_as_sent(news_hash, title, source, link):
    """Mark news item as sent"""
    payload = {
        "news_hash": news_hash,
        "title": title,
        "source": source,
        "link": link,
        "sent_at": datetime.now(timezone.utc).isoformat()
    }
    
    def _mark():
        return supabase.table("sent_news").insert(payload).execute()
    
    result = safe_request(_mark)
    if result:
        logger.info(f"News marked as sent: {title[:50]}...")
    return result

def fetch_ai_news():
    """Fetch latest AI news from multiple sources with better filtering"""
    news_items = []
    feeds_processed = 0
    
    # Shuffle feeds to vary the order and get different news first
    shuffled_feeds = AI_NEWS_FEEDS.copy()
    random.shuffle(shuffled_feeds)
    
    for feed_info in shuffled_feeds:
        feed_url = feed_info["url"]
        feed_name = feed_info["name"]
        
        try:
            logger.info(f"Fetching from: {feed_name}")
            
            def _parse_feed():
                return feedparser.parse(feed_url)
            
            feed = safe_request(_parse_feed)
            if not feed:
                continue
                
            feeds_processed += 1
            
            for entry in feed.entries[:5]:  # Top 5 from each feed
                try:
                    # More flexible time filtering - last 48 hours for more content
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_time = datetime(*entry.published_parsed[:6])
                        if datetime.now() - pub_time > timedelta(hours=48):
                            continue
                    
                    # Clean title and check if it's AI-related
                    title = entry.title.strip()
                    title_lower = title.lower()
                    
                    # AI-related keywords filter
                    ai_keywords = [
                        'ai', 'artificial intelligence', 'machine learning', 'deep learning',
                        'neural network', 'chatgpt', 'openai', 'claude', 'gemini', 'llm',
                        'language model', 'generative', 'automation', 'robot', 'algorithm',
                        'computer vision', 'nlp', 'natural language'
                    ]
                    
                    if not any(keyword in title_lower for keyword in ai_keywords):
                        continue
                    
                    news_hash = generate_news_hash(title, entry.link)
                    
                    # Skip if already sent
                    if is_news_sent(news_hash):
                        continue
                    
                    # Get summary with better fallback
                    summary = ""
                    if hasattr(entry, 'summary'):
                        summary = entry.summary
                    elif hasattr(entry, 'description'):
                        summary = entry.description
                    
                    # Clean and truncate summary
                    if summary:
                        summary = summary.strip()[:300] + "..." if len(summary) > 300 else summary
                    
                    news_item = {
                        "title": title,
                        "link": entry.link,
                        "summary": summary,
                        "source": feed_name,
                        "hash": news_hash,
                        "published": getattr(entry, 'published', 'Recent')
                    }
                    news_items.append(news_item)
                    
                except Exception as e:
                    logger.warning(f"Error processing entry from {feed_name}: {e}")
                    continue
                    
        except Exception as e:
            logger.warning(f"Error fetching from {feed_name}: {e}")
            continue
    
    logger.info(f"Processed {feeds_processed} feeds, found {len(news_items)} new AI news items")
    return news_items[:3]  # Return top 3 latest news

def format_news_message(news_item):
    """Format news item for Telegram with emojis and better formatting"""
    message = f"🤖 **AI News Alert**\n\n"
    message += f"**{news_item['title']}**\n\n"
    
    if news_item['summary']:
        message += f"📝 {news_item['summary']}\n\n"
    
    message += f"🔗 [Read Full Article]({news_item['link']})\n"
    message += f"📰 Source: {news_item['source']}\n"
    
    if news_item.get('published'):
        message += f"⏰ {news_item['published']}"
    
    return message

def broadcast_news():
    """Fetch and broadcast AI news to all subscribers with error handling"""
    try:
        logger.info("Starting news broadcast cycle...")
        news_items = fetch_ai_news()
        
        if not news_items:
            logger.info("No new AI news found in this cycle")
            return
        
        subscribers = get_subscribers()
        if not subscribers:
            logger.warning("No subscribers found")
            return
            
        logger.info(f"Broadcasting {len(news_items)} news items to {len(subscribers)} subscribers")
        
        successful_sends = 0
        failed_sends = 0
        
        for news_item in news_items:
            message = format_news_message(news_item)
            item_successful = 0
            item_failed = 0
            
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
                    item_successful += 1
                    successful_sends += 1
                else:
                    item_failed += 1
                    failed_sends += 1
                    logger.warning(f"Failed to send news to subscriber {chat_id}")
                
                # Small delay to avoid rate limiting
                time.sleep(0.05)
            
            # Mark as sent regardless of delivery success (to avoid resending)
            mark_news_as_sent(
                news_item['hash'], 
                news_item['title'], 
                news_item['source'],
                news_item['link']
            )
            
            logger.info(f"News '{news_item['title'][:50]}...' sent to {item_successful} users, failed for {item_failed}")
            
            # Delay between different news items
            time.sleep(1)
        
        logger.info(f"Broadcast complete: {successful_sends} successful, {failed_sends} failed")
        
    except Exception as e:
        logger.error(f"Critical error in broadcast_news: {e}")

def cleanup_old_news():
    """Clean up old sent news to keep database lean"""
    try:
        def _cleanup():
            # Keep only last 7 days of sent news
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            return supabase.table("sent_news").delete().lt("sent_at", cutoff_date).execute()
        
        result = safe_request(_cleanup)
        if result:
            logger.info("Old news records cleaned up")
    except Exception as e:
        logger.warning(f"Error cleaning up old news: {e}")

def news_worker():
    """Background worker for news broadcasting - runs forever"""
    logger.info(f"News worker started - broadcasting every {NEWS_INTERVAL} seconds")
    cleanup_counter = 0
    
    while True:
        try:
            broadcast_news()
            
            # Clean up old news every 24 hours (288 cycles at 5min intervals)
            cleanup_counter += 1
            if cleanup_counter >= 288:
                cleanup_old_news()
                cleanup_counter = 0
                
        except Exception as e:
            logger.error(f"Unexpected error in news worker: {e}")
        
        logger.info(f"News cycle complete. Waiting {NEWS_INTERVAL} seconds...")
        time.sleep(NEWS_INTERVAL)

def handle_update(u):
    """Handle Telegram updates with error handling"""
    try:
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            return
        
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip().lower()
        username = msg.get("from", {}).get("username", "Unknown")
        
        logger.info(f"Received command '{text}' from user {username} ({chat_id})")
        
        if text.startswith("/start"):
            if upsert_subscriber(msg):
                send("sendMessage", chat_id=chat_id, text=WELCOME, disable_web_page_preview=True)
                logger.info(f"New subscriber: {username} ({chat_id})")
        
        elif text.startswith("/stop"):
            def _unsubscribe():
                return supabase.table("subscribers").delete().eq("telegram_user_id", chat_id).execute()
            
            if safe_request(_unsubscribe):
                send("sendMessage", chat_id=chat_id, text=GOODBYE, disable_web_page_preview=True)
                logger.info(f"Unsubscribed: {username} ({chat_id})")
        
        elif text.startswith("/status"):
            send("sendMessage", chat_id=chat_id, text=STATUS, disable_web_page_preview=True)
        
        elif text.startswith("/news"):
            logger.info(f"Manual news request from {username} ({chat_id})")
            news_items = fetch_ai_news()
            if news_items:
                for news_item in news_items[:1]:  # Send just the latest
                    message = format_news_message(news_item)
                    send("sendMessage", chat_id=chat_id, text=message, parse_mode="Markdown")
            else:
                send("sendMessage", chat_id=chat_id, text="🤖 No new AI news available right now. Check back soon!")
        
    except Exception as e:
        logger.error(f"Error handling update: {e}")

def bot_polling():
    """Main bot polling loop - runs forever"""
    offset = None
    error_count = 0
    max_errors = 10
    
    logger.info("Bot polling started...")
    
    while True:
        try:
            def _get_updates():
                return requests.get(
                    f"{TG_API}/getUpdates",
                    params={"timeout": 25, "offset": offset},
                    timeout=30
                ).json()
            
            resp = safe_request(_get_updates)
            if not resp:
                time.sleep(5)
                continue
            
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
            
            error_count = 0  # Reset error count on success
            
        except Exception as e:
            error_count += 1
            logger.error(f"Error in bot polling loop (count: {error_count}): {e}")
            
            if error_count >= max_errors:
                logger.critical(f"Too many errors ({max_errors}), sleeping for 60 seconds")
                time.sleep(60)
                error_count = 0
            else:
                time.sleep(2)
        
        time.sleep(POLL_INTERVAL)

def main():
    """Main function - starts both threads and keeps them running"""
    logger.info("🤖 Starting AI News Telegram Bot...")
    
    # Start news worker in background thread
    news_thread = threading.Thread(target=news_worker, daemon=False, name="NewsWorker")
    news_thread.start()
    logger.info("✅ News worker thread started")
    
    # Start bot polling in background thread  
    bot_thread = threading.Thread(target=bot_polling, daemon=False, name="BotPoller")
    bot_thread.start()
    logger.info("✅ Bot polling thread started")
    
    # Keep main thread alive and monitor other threads
    try:
        while True:
            if not news_thread.is_alive():
                logger.error("❌ News thread died, restarting...")
                news_thread = threading.Thread(target=news_worker, daemon=False, name="NewsWorker")
                news_thread.start()
            
            if not bot_thread.is_alive():
                logger.error("❌ Bot thread died, restarting...")
                bot_thread = threading.Thread(target=bot_polling, daemon=False, name="BotPoller")
                bot_thread.start()
            
            time.sleep(30)  # Check every 30 seconds
            
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down bot...")
    except Exception as e:
        logger.critical(f"💥 Critical error in main: {e}")

if __name__ == "__main__":
    required_vars = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TELEGRAM_TOKEN")
    for var in required_vars:
        if not os.environ.get(var):
            raise SystemExit(f"❌ Missing env var: {var}")
    
    logger.info("🚀 All environment variables found, starting bot...")
    main()