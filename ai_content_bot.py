import os
import requests
import logging
from datetime import datetime, timedelta
import time
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.post import Post
from app.models.user import User
import re
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Log to console
    ]
)

logger = logging.getLogger(__name__)

# AI Bot User ID (create this user first)
AI_BOT_USER_ID = 1  # AI Content Bot user

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # Set this in your environment
logger.info(f"Gemini API Key loaded: {'Yes' if GEMINI_API_KEY else 'No'}")
genai.configure(api_key=GEMINI_API_KEY)

# News API Configuration
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")  # Get free key from newsapi.org
NEWS_API_URL = "https://newsapi.org/v2/top-headlines"

# Alternative News API (NewsData.io) - more reliable
NEWSDATA_API_KEY = os.getenv("NEWSDATA_API_KEY", "")  # Get from newsdata.io
NEWSDATA_URL = "https://newsdata.io/api/1/news"

# Track used articles to avoid duplicates
USED_ARTICLES = set()

# Indian News Sources - prioritize Indian media
INDIAN_SOURCES = "the-times-of-india,the-hindu,google-news-in"

def get_recent_news_newsdata():
    """Fetch recent news using NewsData.io API (more reliable alternative)"""
    logger.info("Fetching news using NewsData.io API")
    try:
        if not NEWSDATA_API_KEY:
            logger.warning("NEWSDATA_API_KEY not found")
            return []

        # NewsData.io parameters for Indian news
        params = {
            'apikey': NEWSDATA_API_KEY,
            'country': 'in',  # India
            'language': 'en,hi',  # English and Hindi
            'size': 10,  # Get 10 articles
            'category': 'top'  # Top news
        }

        response = requests.get(NEWSDATA_URL, params=params, timeout=15)

        if response.status_code == 200:
            data = response.json()
            articles = data.get('results', [])

            # Convert NewsData.io format to match our expected format
            formatted_articles = []
            for article in articles:
                if article.get('link') and article.get('link') not in USED_ARTICLES:
                    formatted_article = {
                        'title': article.get('title', ''),
                        'description': article.get('description', '') or article.get('content', ''),
                        'url': article.get('link', ''),
                        'source': {'name': article.get('source_id', 'newsdata')},
                        'publishedAt': article.get('pubDate', '')
                    }
                    formatted_articles.append(formatted_article)

            if formatted_articles:
                logger.info(f"Found {len(formatted_articles)} articles from NewsData.io")
                return formatted_articles[:5]

        else:
            logger.error(f"NewsData.io API error: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error fetching from NewsData.io: {str(e)}")

    return []

def get_recent_news():
    """Fetch recent top news headlines from Indian sources, avoiding duplicates"""
    logger.info("Starting news fetch process")

    # Try NewsData.io first (more reliable, better rate limits)
    if NEWSDATA_API_KEY:
        logger.info("Trying NewsData.io API first...")
        articles = get_recent_news_newsdata()
        if articles:
            return articles
        logger.warning("NewsData.io failed, falling back to NewsAPI...")

    # Fallback to NewsAPI.org with rate limiting protection
    try:
        if not NEWS_API_KEY:
            logger.warning("NEWS_API_KEY not found. Using fallback topics...")
            return []

        # Single attempt with rate limiting protection
        selected_category = 'general'  # Use general instead of random to reduce API calls

        logger.info(f"Fetching {selected_category} news from India...")

        params = {
            'apiKey': NEWS_API_KEY,
            'country': 'in',
            'category': selected_category,
            'pageSize': 10  # Reduced from 20 to be more conservative
        }

        response = requests.get(NEWS_API_URL, params=params, timeout=10)

        if response.status_code == 200:
            articles = response.json().get('articles', [])
            logger.info(f"News API returned {len(articles)} articles")
            # Filter out already used articles
            new_articles = [a for a in articles if a.get('url') not in USED_ARTICLES]

            if new_articles:
                logger.info(f"Found {len(new_articles)} new Indian articles")
                return new_articles[:5]

        elif response.status_code == 429:
            logger.warning("NewsAPI rate limit exceeded. Waiting before retry...")
            time.sleep(60)  # Wait 1 minute for rate limit reset

            # Retry once after waiting
            response = requests.get(NEWS_API_URL, params=params, timeout=10)
            if response.status_code == 200:
                articles = response.json().get('articles', [])
                new_articles = [a for a in articles if a.get('url') not in USED_ARTICLES]
                if new_articles:
                    logger.info(f"After rate limit reset: Found {len(new_articles)} articles")
                    return new_articles[:5]

            logger.error("Still rate limited after waiting")

        else:
            logger.error(f"News API error: {response.status_code} - {response.text[:100]}...")

    except Exception as e:
        logger.error(f"Error fetching news: {str(e)}")

    # Final fallback - hardcoded topics (guaranteed to work)
    logger.warning("All APIs failed, using hardcoded fallback topics...")
    fallback_topics = [
        {
            'title': 'Political Rally Promises Free Everything Except Common Sense',
            'description': 'Another election season, another set of impossible promises'
        },
        {
            'title': 'Social Media Outrage Reaches New Heights Over Absolutely Nothing',
            'description': 'Twitter trends show collective anger about trivial matters'
        },
        {
            'title': 'Traffic Jam in Mumbai Reaches Philosophical Levels',
            'description': 'Commuters achieve enlightenment while stuck in eternal gridlock'
        },
        {
            'title': 'Celebrity Announces New Diet: Eating Less Food',
            'description': 'Revolutionary weight loss method shocks nutrition experts worldwide'
        }
    ]

    # Return fallback topics in article format
    formatted_fallbacks = []
    for topic in fallback_topics:
        formatted_fallbacks.append({
            'title': topic['title'],
            'description': topic['description'],
            'url': f'fallback-{hash(topic["title"])}',  # Unique URL for tracking
            'source': {'name': 'Fallback Content'}
        })

    logger.info(f"Using {len(formatted_fallbacks)} fallback topics")
    return formatted_fallbacks[:5]

def generate_satirical_content(news_headline, news_description):
    """Generate satirical article using Gemini AI"""
    logger.info(f"Generating satirical content for headline: {news_headline[:50]}...")
    
    prompt = f"""You are a friendly, witty Indian friend chatting over chai about current events. Write like you're gossiping with friends - natural, conversational, and funny.

News Topic: {news_headline}
Details: {news_description}

WRITING STYLE - Sound like a real person talking:
1. Write Hindi in Devanagari (हिंदी में), English in English
2. Mix both naturally like Indians actually speak
3. Use conversational fillers
4. Ask rhetorical questions
5. Use everyday comparisons and examples people relate to
6. Include real emotions - frustration, amusement, disbelief
7. Write in short, punchy sentences like people talk
8. Add personal observations
9. Use colloquial expressions
10. Sound like storytelling, not reporting
11. dont write like -- "अरे भाई, अभी-अभी मैंने यह news पढ़ी और मेरी तो हंसी नहीं रुक रही! मतलब seriously, कभी-कभी लगता है कि हम एक comedy show में रहते हैं, जहाँ हर दिन एक नया एपिसोड आता है।", "सुनो ना, यार! अभी-अभी मैंने एक न्यूज़ पढ़ी और मेरा तो दिमाग ही घूम गया। मतलब, कभी-कभी मुझे लगता है कि हम एक ऐसे *parallel universe* में रहते हैं जहाँ सब कुछ उल्टा चलता है", "मेरा तो दिमाग ही घूमने लगा", "अच्छा सुनो, एक बात बताऊँ?" it looks very forced and unnatural
12. dont use -- अरे भाई, अरे यार, यार etc it is overused and sounds unnatural 


TONE: Friendly sarcasm, like joking with friends. Not mean, just amused and witty.

STRUCTURE:
- Opening: Hook readers with a relatable observation
- Middle: Tell the story with humor and personal reactions
- Ending: Witty conclusion that makes them smile

Length: 400-600 words with natural paragraph breaks

IMPORTANT: 
- Title and subtitle should NOT appear anywhere in the CONTENT section
- CONTENT should start directly with the article text
- Do not repeat the title or subtitle in the body
- Write the full article content naturally without any headers
- Avoid phrases like "अरे भाई", "अरे यार", "यार" as they sound forced
- Keep it natural and conversational like real friends chatting

Format the response as:
TITLE: [Catchy, conversational title mixing Hindi and English like people actually talk]
SUBTITLE: [A one-liner that sounds like something your friend would say]
CONTENT: [Full article written in natural, conversational style. Mix Hindi देवनागरी and English like real people speak. Use short paragraphs. Sound human, not robotic! Do NOT include the title or subtitle in this section.]
"""

    try:
        logger.info("Calling Gemini AI for content generation")
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.9,
                max_output_tokens=4000,
            )
        )
        
        text = response.text
        logger.info(f"AI generated content of length: {len(text)} characters")
        
        # Parse the response
        title_match = re.search(r'TITLE:\s*(.+?)(?:\n|$)', text)
        subtitle_match = re.search(r'SUBTITLE:\s*(.+?)(?:\n|$)', text)
        content_match = re.search(r'CONTENT:\s*(.+)', text, re.DOTALL)
        
        title = title_match.group(1).strip() if title_match else news_headline
        subtitle = subtitle_match.group(1).strip() if subtitle_match else ""
        content = content_match.group(1).strip() if content_match else text
        
        # Clean up content - remove any embedded title/subtitle text
        if content:
            # Remove any TITLE: or SUBTITLE: sections that might be in the content
            content = re.sub(r'TITLE:\s*.+?(?:\n|$)', '', content, flags=re.IGNORECASE)
            content = re.sub(r'SUBTITLE:\s*.+?(?:\n|$)', '', content, flags=re.IGNORECASE)
            # Remove any duplicate title/subtitle text that might appear in content
            if title and title in content:
                content = content.replace(title, '', 1)  # Remove first occurrence only
            if subtitle and subtitle in content:
                content = content.replace(subtitle, '', 1)  # Remove first occurrence only
            # Clean up extra whitespace
            content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)  # Remove excessive newlines
            content = content.strip()
        
        logger.info(f"Parsed AI response - Title: '{title[:50]}...', Subtitle: '{subtitle[:50]}...', Content length: {len(content)}")
        
        return {
            'title': title,
            'subtitle': subtitle,
            'content': content
        }
        
    except Exception as e:
        logger.error(f"Gemini API error in generate_satirical_content: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        return None

def generate_slug(title: str) -> str:
    """Generate URL-friendly slug from title"""
    # Remove special characters and convert to lowercase
    slug = re.sub(r'[^\w\s-]', '', title.lower())
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug[:100]

def create_satirical_post(db: Session, article_data):
    """Create and save a satirical post to database"""
    try:
        logger.info("Creating satirical post in database")
        
        # Check if bot user exists
        bot_user = db.query(User).filter(User.id == AI_BOT_USER_ID).first()
        if not bot_user:
            logger.error(f"Bot user with ID {AI_BOT_USER_ID} not found!")
            # Try querying all users to debug
            all_users = db.query(User).all()
            logger.info(f"Debug: Found {len(all_users)} users in database")
            for u in all_users:
                logger.info(f"  User {u.id}: {u.username}")
            return False
        
        logger.info(f"Bot user found: {bot_user.username}")
        
        # Generate unique slug
        base_slug = generate_slug(article_data['title'])
        slug = base_slug
        counter = 1
        
        while db.query(Post).filter(Post.slug == slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        logger.info(f"Generated slug: {slug}")
        
        # Create post
        new_post = Post(
            title=article_data['title'],
            subtitle=article_data['subtitle'],
            content=article_data['content'],
            slug=slug,
            author_id=AI_BOT_USER_ID,
            published=1,  # Automatically publish
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(new_post)
        db.commit()
        db.refresh(new_post)
        
        logger.info(f"✅ Created post: {new_post.title}")
        logger.info(f"   Slug: {new_post.slug}")
        logger.info(f"   Post ID: {new_post.id}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating satirical post: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        db.rollback()
        return False

def run_ai_content_generator():
    """Main function to generate and post satirical content"""
    logger.info(f"🤖 AI Content Generator started at {datetime.now()}")
    logger.info("=" * 60)
    
    # Check if News API key is available
    if not NEWS_API_KEY:
        logger.warning("⚠️  NEWS_API_KEY not found. Using fallback topics...")
        # Fallback: Generate content on common trending topics
        fallback_topics = [
            {
                'title': 'Political Rally Promises Free Everything Except Common Sense',
                'description': 'Another election season, another set of impossible promises'
            },
            {
                'title': 'Social Media Outrage Reaches New Heights Over Absolutely Nothing',
                'description': 'Twitter trends show collective anger about trivial matters'
            }
        ]
        import random
        topic = random.choice(fallback_topics)
        news_articles = fallback_topics
        logger.info(f"Using fallback topic: {topic['title']}")
    else:
        # Fetch real news
        logger.info("Fetching real news articles...")
        news_articles = get_recent_news()
        
        # If news fetch failed, use fallback topics
        if not news_articles:
            logger.warning("⚠️  News API failed, using fallback topics...")
            fallback_topics = [
                {
                    'title': 'Political Rally Promises Free Everything Except Common Sense',
                    'description': 'Another election season, another set of impossible promises'
                },
                {
                    'title': 'Social Media Outrage Reaches New Heights Over Absolutely Nothing',
                    'description': 'Twitter trends show collective anger about trivial matters'
                },
                {
                    'title': 'Traffic Jam in Mumbai Reaches Philosophical Levels',
                    'description': 'Commuters achieve enlightenment while stuck in eternal gridlock'
                },
                {
                    'title': 'Celebrity Announces New Diet: Eating Less Food',
                    'description': 'Revolutionary weight loss method shocks nutrition experts worldwide'
                }
            ]
            news_articles = fallback_topics
            logger.info("Using fallback topics for content generation")
    
    if not news_articles:
        logger.error("❌ No news articles found")
        return
    
    logger.info(f"Found {len(news_articles)} news articles to process")
    
    # Get database session
    db = SessionLocal()
    
    try:
        # Pick a random article from the list for variety
        import random
        article = random.choice(news_articles)
        headline = article.get('title', '')
        description = article.get('description', '') or article.get('content', '')
        article_url = article.get('url', '')
        
        logger.info(f"Selected article: {headline[:50]}...")
        logger.info(f"Article URL: {article_url}")
        
        # Track used article
        if article_url:
            USED_ARTICLES.add(article_url)
            # Keep only last 100 used articles
            if len(USED_ARTICLES) > 100:
                USED_ARTICLES.pop()
        
        logger.info("📰 Processing news article...")
        
        # Generate satirical content
        logger.info("🎭 Generating satirical content with AI...")
        satirical_content = generate_satirical_content(headline, description)
        
        if satirical_content:
            logger.info("✍️  Content generated successfully!")
            
            # Create post
            logger.info("📝 Creating satirical post in database...")
            success = create_satirical_post(db, satirical_content)
            
            if success:
                logger.info("✅ Post published successfully!")
                logger.info(f"📝 Title: {satirical_content['title']}")
                logger.info(f"📌 Subtitle: {satirical_content['subtitle']}")
                logger.info(f"📄 Content length: {len(satirical_content['content'])} characters")
            else:
                logger.error("❌ Failed to publish post")
        else:
            logger.error("❌ Failed to generate content")
            
    except Exception as e:
        logger.error(f"❌ Error in AI content generator: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
    finally:
        db.close()
    
    logger.info("=" * 60)
    logger.info(f"🤖 AI Content Generator finished at {datetime.now()}\n")

def setup_bot_user():
    """Create the AI bot user if it doesn't exist"""
    logger.info("Setting up AI bot user...")
    db = SessionLocal()
    try:
        bot_user = db.query(User).filter(User.username == "satirical_bot").first()
        
        if not bot_user:
            logger.info("Creating AI bot user...")
            from app.core.security import get_password_hash
            
            bot_user = User(
                email="satirical.bot@merikahani.com",
                username="satirical_bot",
                full_name="व्यंग्य लेखक",
                hashed_password=get_password_hash("securepassword123"),
                bio="दुनिया की खबरों पर व्यंग्यात्मक नज़रिया। सच को मजाकिया अंदाज में पेश करना हमारा काम है! 🎭📰",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(bot_user)
            db.commit()
            db.refresh(bot_user)
            
            logger.info(f"✅ Bot user created with ID: {bot_user.id}")
            logger.info(f"   Username: {bot_user.username}")
            logger.info(f"   Update AI_BOT_USER_ID in this script to: {bot_user.id}")
            return bot_user.id
        else:
            logger.info(f"✅ Bot user already exists with ID: {bot_user.id}")
            return bot_user.id
            
    except Exception as e:
        logger.error(f"❌ Error setting up bot user: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        db.rollback()
        return None
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        # Setup bot user
        bot_id = setup_bot_user()
        if bot_id:
            print(f"\n📝 Update the AI_BOT_USER_ID variable to: {bot_id}")
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test run (single post)
        run_ai_content_generator()
    else:
        print("""
🤖 AI Satirical Content Generator
==================================

Usage:
  python ai_content_bot.py setup    # Create bot user
  python ai_content_bot.py test     # Generate one test post
  
For scheduled posting, use the scheduler script or cron job.
        """)
