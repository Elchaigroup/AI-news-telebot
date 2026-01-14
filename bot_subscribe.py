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
from openai import OpenAI

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
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "2"))
NEWS_INTERVAL = int(os.getenv("NEWS_INTERVAL", "60"))
MAX_RETRIES = 3

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Italian messages
WELCOME = (
    "👋 Benvenuto! Ora sei iscritto al Bot Notizie AI.\n\n"
    "🤖 Riceverai regolarmente gli ultimi aggiornamenti sulle notizie AI.\n"
    "📱 Comandi: /stop per annullare l'iscrizione, /status per controllare l'iscrizione, /news per l'ultimo aggiornamento"
)
GOODBYE = "👋 Ti sei disiscritto. Per tornare: /start"
STATUS = "📬 Sei iscritto! Riceverai qui gli aggiornamenti sulle notizie AI."

# =============================================================================
# RSS FEEDS BY CATEGORY
# =============================================================================

# AI News feeds (sent to Telegram + stored in sent_news)
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

# Politics feeds (stored in articles table only)
POLITICS_FEEDS = [
    {"url": "https://feeds.reuters.com/Reuters/PoliticsNews", "name": "Reuters Politics"},
    {"url": "https://feeds.bbci.co.uk/news/politics/rss.xml", "name": "BBC Politics"},
    {"url": "https://rss.politico.com/politics-news.xml", "name": "Politico"},
    {"url": "https://thehill.com/feed/", "name": "The Hill"},
    {"url": "https://www.theguardian.com/politics/rss", "name": "Guardian Politics"},
    {"url": "https://feeds.npr.org/1014/rss.xml", "name": "NPR Politics"},
]

# Economy feeds (stored in articles table only)
ECONOMY_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "name": "Reuters Business"},
    {"url": "https://feeds.bloomberg.com/markets/news.rss", "name": "Bloomberg Markets"},
    {"url": "https://www.cnbc.com/id/10001147/device/rss/rss.html", "name": "CNBC"},
    {"url": "https://feeds.bbci.co.uk/news/business/rss.xml", "name": "BBC Business"},
    {"url": "https://www.theguardian.com/business/rss", "name": "Guardian Business"},
    {"url": "https://www.economist.com/finance-and-economics/rss.xml", "name": "The Economist"},
]

# Culture feeds (stored in articles table only)
CULTURE_FEEDS = [
    {"url": "https://www.theguardian.com/culture/rss", "name": "Guardian Culture"},
    {"url": "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "name": "BBC Culture"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml", "name": "NYT Arts"},
    {"url": "https://www.rollingstone.com/feed/", "name": "Rolling Stone"},
    {"url": "https://variety.com/feed/", "name": "Variety"},
    {"url": "https://pitchfork.com/feed/feed-news/rss", "name": "Pitchfork"},
]

# Social Justice feeds (stored in articles table only)
SOCIAL_JUSTICE_FEEDS = [
    {"url": "https://www.theguardian.com/inequality/rss", "name": "Guardian Equality"},
    {"url": "https://www.huffpost.com/section/impact/feed", "name": "HuffPost Impact"},
    {"url": "https://civilrights.org/feed/", "name": "Civil Rights"},
    {"url": "https://www.aclu.org/feed/", "name": "ACLU"},
    {"url": "https://theconversation.com/us/topics/social-justice-702/articles.atom", "name": "The Conversation"},
]

# Community feeds (stored in articles table only)
COMMUNITY_FEEDS = [
    {"url": "https://feeds.npr.org/1001/rss.xml", "name": "NPR News"},
    {"url": "https://apnews.com/apf-topnews/feed", "name": "AP News"},
    {"url": "https://www.theguardian.com/society/rss", "name": "Guardian Society"},
    {"url": "https://feeds.bbci.co.uk/news/rss.xml", "name": "BBC News"},
    {"url": "https://www.vox.com/rss/index.xml", "name": "Vox"},
]

# Category configuration mapping
CATEGORY_FEEDS = {
    "politics": POLITICS_FEEDS,
    "economy": ECONOMY_FEEDS,
    "culture": CULTURE_FEEDS,
    "social_justice": SOCIAL_JUSTICE_FEEDS,
    "community": COMMUNITY_FEEDS,
}

# =============================================================================
# TRANSLATION FUNCTIONS (for AI news only)
# =============================================================================

def translate_to_italian(text):
    """Translate text to Italian using OpenAI API"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional translator. Translate the following English text to Italian. Maintain the same tone and style. Only return the Italian translation, nothing else."
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            temperature=0.3,
            max_tokens=1000
        )
        
        translated_text = response.choices[0].message.content.strip()
        logger.info("Successfully translated text to Italian")
        return translated_text
        
    except Exception as e:
        logger.warning(f"Failed to translate text to Italian: {e}")
        return None


def translate_news_item(news_item):
    """Translate a news item to Italian, returns original if translation fails"""
    try:
        combined_text = f"TITLE: {news_item['title']}\n\nSUMMARY: {news_item['summary'] if news_item['summary'] else 'No summary'}"
        
        translated = translate_to_italian(combined_text)
        
        if translated:
            lines = translated.split('\n')
            
            title_italian = news_item['title']
            summary_italian = news_item['summary']
            
            for i, line in enumerate(lines):
                if line.startswith('TITOLO:') or line.startswith('TITLE:'):
                    title_italian = line.replace('TITOLO:', '').replace('TITLE:', '').strip()
                elif line.startswith('RIASSUNTO:') or line.startswith('SOMMARIO:') or line.startswith('SUMMARY:'):
                    summary_parts = []
                    for j in range(i, len(lines)):
                        clean_line = lines[j].replace('RIASSUNTO:', '').replace('SOMMARIO:', '').replace('SUMMARY:', '').strip()
                        if clean_line:
                            summary_parts.append(clean_line)
                    summary_italian = ' '.join(summary_parts)
                    break
            
            if title_italian == news_item['title'] and len(lines) >= 2:
                title_italian = lines[0].strip()
                summary_italian = ' '.join(lines[1:]).strip()
            
            return {
                **news_item,
                'title': title_italian,
                'summary': summary_italian,
                'translated': True
            }
        
    except Exception as e:
        logger.error(f"Error in translate_news_item: {e}")
    
    logger.info("Using original English text due to translation failure")
    return {**news_item, 'translated': False}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

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
            time.sleep(2 ** attempt)
    return None


def send(method, **params):
    def _send():
        r = requests.post(f"{TG_API}/{method}", json=params, timeout=30)
        r.raise_for_status()
        return r.json()
    
    return safe_request(_send)


def generate_news_hash(title, link):
    """Generate a hash for news item to avoid duplicates"""
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()

# =============================================================================
# SUBSCRIBER FUNCTIONS
# =============================================================================

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

# =============================================================================
# AI NEWS FUNCTIONS (sent_news table + Telegram)
# =============================================================================

def is_news_sent(news_hash):
    """Check if AI news item was already sent"""
    def _check():
        response = supabase.table("sent_news").select("id").eq("news_hash", news_hash).execute()
        return len(response.data) > 0
    
    result = safe_request(_check)
    return result if result is not None else False


def mark_news_as_sent(news_hash, title, source, link):
    """Mark AI news item as sent"""
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
        logger.info(f"AI News marked as sent: {title[:50]}...")
    return result


def fetch_ai_news():
    """Fetch latest AI news from multiple sources with better filtering"""
    news_items = []
    feeds_processed = 0
    
    shuffled_feeds = AI_NEWS_FEEDS.copy()
    random.shuffle(shuffled_feeds)
    
    for feed_info in shuffled_feeds:
        feed_url = feed_info["url"]
        feed_name = feed_info["name"]
        
        try:
            logger.info(f"Fetching AI news from: {feed_name}")
            
            def _parse_feed():
                return feedparser.parse(feed_url)
            
            feed = safe_request(_parse_feed)
            if not feed:
                continue
                
            feeds_processed += 1
            
            for entry in feed.entries[:5]:
                try:
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_time = datetime(*entry.published_parsed[:6])
                        if datetime.now() - pub_time > timedelta(hours=48):
                            continue
                    
                    title = entry.title.strip()
                    title_lower = title.lower()
                    
                    ai_keywords = [
                        'ai', 'artificial intelligence', 'machine learning', 'deep learning',
                        'neural network', 'chatgpt', 'openai', 'claude', 'gemini', 'llm',
                        'language model', 'generative', 'automation', 'robot', 'algorithm',
                        'computer vision', 'nlp', 'natural language'
                    ]
                    
                    if not any(keyword in title_lower for keyword in ai_keywords):
                        continue
                    
                    news_hash = generate_news_hash(title, entry.link)
                    
                    if is_news_sent(news_hash):
                        continue
                    
                    summary = ""
                    if hasattr(entry, 'summary'):
                        summary = entry.summary
                    elif hasattr(entry, 'description'):
                        summary = entry.description
                    
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
                    logger.warning(f"Error processing AI entry from {feed_name}: {e}")
                    continue
                    
        except Exception as e:
            logger.warning(f"Error fetching AI news from {feed_name}: {e}")
            continue
    
    logger.info(f"AI: Processed {feeds_processed} feeds, found {len(news_items)} new items")
    return news_items[:3]

# =============================================================================
# CATEGORY NEWS FUNCTIONS (articles table only - NO Telegram)
# =============================================================================

def is_article_stored(news_hash):
    """Check if article was already stored in articles table"""
    def _check():
        response = supabase.table("articles").select("id").eq("url", news_hash).execute()
        return len(response.data) > 0
    
    result = safe_request(_check)
    return result if result is not None else False


def is_article_url_stored(url):
    """Check if article URL was already stored in articles table"""
    def _check():
        response = supabase.table("articles").select("id").eq("url", url).execute()
        return len(response.data) > 0
    
    result = safe_request(_check)
    return result if result is not None else False


def store_article(title, summary, url, source_category, published_at=None):
    """Store article in articles table"""
    payload = {
        "title": title,
        "summary": summary,
        "url": url,
        "source": source_category,  # e.g., "politics", "economy", "culture"
        "published_at": published_at if published_at else datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    def _store():
        return supabase.table("articles").insert(payload).execute()
    
    result = safe_request(_store)
    if result:
        logger.info(f"Article stored [{source_category}]: {title[:50]}...")
    return result


def fetch_category_news(category_name, feeds):
    """Fetch news from a specific category and store in articles table"""
    articles_stored = 0
    feeds_processed = 0
    
    shuffled_feeds = feeds.copy()
    random.shuffle(shuffled_feeds)
    
    for feed_info in shuffled_feeds:
        feed_url = feed_info["url"]
        feed_name = feed_info["name"]
        
        try:
            logger.info(f"Fetching {category_name} news from: {feed_name}")
            
            def _parse_feed():
                return feedparser.parse(feed_url)
            
            feed = safe_request(_parse_feed)
            if not feed:
                continue
                
            feeds_processed += 1
            
            for entry in feed.entries[:5]:
                try:
                    # Check freshness - last 48 hours
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_time = datetime(*entry.published_parsed[:6])
                        if datetime.now() - pub_time > timedelta(hours=48):
                            continue
                    
                    title = entry.title.strip()
                    link = entry.link
                    
                    # Skip if already stored (check by URL)
                    if is_article_url_stored(link):
                        continue
                    
                    # Get summary
                    summary = ""
                    if hasattr(entry, 'summary'):
                        summary = entry.summary
                    elif hasattr(entry, 'description'):
                        summary = entry.description
                    
                    if summary:
                        summary = summary.strip()[:500] + "..." if len(summary) > 500 else summary
                    
                    # Get published date
                    published_at = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                    
                    # Store in articles table
                    if store_article(title, summary, link, category_name, published_at):
                        articles_stored += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing {category_name} entry from {feed_name}: {e}")
                    continue
                    
        except Exception as e:
            logger.warning(f"Error fetching {category_name} from {feed_name}: {e}")
            continue
    
    logger.info(f"{category_name.upper()}: Processed {feeds_processed} feeds, stored {articles_stored} new articles")
    return articles_stored


def fetch_all_category_news():
    """Fetch news from all categories and store in articles table"""
    total_stored = 0
    
    for category_name, feeds in CATEGORY_FEEDS.items():
        try:
            stored = fetch_category_news(category_name, feeds)
            total_stored += stored
        except Exception as e:
            logger.error(f"Error fetching {category_name} news: {e}")
    
    logger.info(f"TOTAL: Stored {total_stored} new articles across all categories")
    return total_stored

# =============================================================================
# TELEGRAM MESSAGE FORMATTING
# =============================================================================

def format_news_message(news_item):
    """Format news item for Telegram with emojis and better formatting"""
    message = f"🤖 **Notizie AI**\n\n"
    message += f"**{news_item['title']}**\n\n"
    
    if news_item['summary']:
        message += f"📝 {news_item['summary']}\n\n"
    
    message += f"🔗 [Leggi l'articolo completo]({news_item['link']})\n"
    message += f"📰 Fonte: {news_item['source']}\n"
    
    if news_item.get('published'):
        message += f"⏰ {news_item['published']}"
    
    if not news_item.get('translated', True):
        message += "\n\n_[Articolo originale in inglese]_"
    
    return message

# =============================================================================
# BROADCAST FUNCTIONS
# =============================================================================

def broadcast_ai_news():
    """Fetch and broadcast AI news to all subscribers"""
    try:
        logger.info("Starting AI news broadcast cycle...")
        news_items = fetch_ai_news()
        
        if not news_items:
            logger.info("No new AI news found in this cycle")
            return
        
        subscribers = get_subscribers()
        if not subscribers:
            logger.warning("No subscribers found")
            return
            
        logger.info(f"Broadcasting {len(news_items)} AI news items to {len(subscribers)} subscribers")
        
        successful_sends = 0
        failed_sends = 0
        
        for news_item in news_items:
            translated_item = translate_news_item(news_item)
            message = format_news_message(translated_item)
            
            item_successful = 0
            item_failed = 0
            
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
                    logger.warning(f"Failed to send AI news to subscriber {chat_id}")
                
                time.sleep(0.05)
            
            mark_news_as_sent(
                news_item['hash'], 
                news_item['title'],
                news_item['source'],
                news_item['link']
            )
            
            logger.info(f"AI News '{news_item['title'][:50]}...' sent to {item_successful} users, failed for {item_failed}")
            
            time.sleep(1)
        
        logger.info(f"AI Broadcast complete: {successful_sends} successful, {failed_sends} failed")
        
    except Exception as e:
        logger.error(f"Critical error in broadcast_ai_news: {e}")

# =============================================================================
# CLEANUP FUNCTIONS
# =============================================================================

def cleanup_old_news():
    """Clean up old sent news to keep database lean"""
    try:
        def _cleanup():
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            return supabase.table("sent_news").delete().lt("sent_at", cutoff_date).execute()
        
        result = safe_request(_cleanup)
        if result:
            logger.info("Old AI news records cleaned up")
    except Exception as e:
        logger.warning(f"Error cleaning up old news: {e}")


def cleanup_old_articles():
    """Clean up old articles to keep database lean"""
    try:
        def _cleanup():
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            return supabase.table("articles").delete().lt("created_at", cutoff_date).execute()
        
        result = safe_request(_cleanup)
        if result:
            logger.info("Old articles cleaned up")
    except Exception as e:
        logger.warning(f"Error cleaning up old articles: {e}")

# =============================================================================
# WORKER THREADS
# =============================================================================

def news_worker():
    """Background worker for news scraping and broadcasting - runs forever"""
    logger.info(f"News worker started - running every {NEWS_INTERVAL} seconds")
    cleanup_counter = 0
    
    while True:
        try:
            # 1. Broadcast AI news to Telegram (stored in sent_news)
            broadcast_ai_news()
            
            # 2. Fetch and store all category news (stored in articles)
            fetch_all_category_news()
            
            # Clean up old records every 24 hours
            cleanup_counter += 1
            if cleanup_counter >= 288:  # ~24 hours at 5min intervals
                cleanup_old_news()
                cleanup_old_articles()
                cleanup_counter = 0
                
        except Exception as e:
            logger.error(f"Unexpected error in news worker: {e}")
        
        logger.info(f"News cycle complete. Waiting {NEWS_INTERVAL} seconds...")
        time.sleep(NEWS_INTERVAL)


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
            
            error_count = 0
            
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

# =============================================================================
# TELEGRAM COMMAND HANDLERS
# =============================================================================

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
                for news_item in news_items[:1]:
                    translated_item = translate_news_item(news_item)
                    message = format_news_message(translated_item)
                    send("sendMessage", chat_id=chat_id, text=message, parse_mode="Markdown")
            else:
                no_news_msg = "🤖 Nessuna nuova notizia AI disponibile al momento. Ricontrolla più tardi!"
                send("sendMessage", chat_id=chat_id, text=no_news_msg)
        
    except Exception as e:
        logger.error(f"Error handling update: {e}")

# =============================================================================
# MAIN FUNCTION
# =============================================================================

def main():
    """Main function - starts both threads and keeps them running"""
    logger.info("🤖 Starting Multi-Category News Bot...")
    logger.info("📰 Categories: AI (Telegram), Politics, Economy, Culture, Social Justice, Community")
    
    # Test OpenAI connection
    try:
        test_translation = translate_to_italian("Hello world")
        if test_translation:
            logger.info(f"✅ OpenAI API connected successfully. Test: 'Hello world' -> '{test_translation}'")
        else:
            logger.warning("⚠️ OpenAI API test failed, will send AI news in English if translation fails")
    except Exception as e:
        logger.warning(f"⚠️ OpenAI API initialization warning: {e}")
    
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
            
            time.sleep(30)
            
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down bot...")
    except Exception as e:
        logger.critical(f"💥 Critical error in main: {e}")


if __name__ == "__main__":
    required_vars = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TELEGRAM_TOKEN", "OPENAI_API_KEY")
    for var in required_vars:
        if not os.environ.get(var):
            raise SystemExit(f"❌ Missing env var: {var}")
    
    logger.info("🚀 All environment variables found, starting bot...")
    main()
