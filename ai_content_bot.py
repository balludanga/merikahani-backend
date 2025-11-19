import os
import requests
from datetime import datetime, timedelta
import time
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.post import Post
from app.models.user import User
import re
import google.generativeai as genai

# AI Bot User ID (create this user first)
AI_BOT_USER_ID = 1  # Update this after creating the bot user

# Gemini API Configuration
GEMINI_API_KEY = "REMOVED_LEAKED_API_KEY"
genai.configure(api_key=GEMINI_API_KEY)

# News API Configuration
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")  # Get free key from newsapi.org
NEWS_API_URL = "https://newsapi.org/v2/top-headlines"

# Track used articles to avoid duplicates
USED_ARTICLES = set()

# Indian News Sources - prioritize Indian media
INDIAN_SOURCES = "the-times-of-india,the-hindu,google-news-in"

def get_recent_news():
    """Fetch recent top news headlines from Indian sources, avoiding duplicates"""
    try:
        if not NEWS_API_KEY:
            print("⚠️  NEWS_API_KEY not found. Using fallback topics...")
            return []
        
        # Rotate through different categories to get diverse news
        categories = ['general', 'business', 'technology', 'sports', 'entertainment', 'science', 'health']
        import random
        selected_category = random.choice(categories)
        
        print(f"🎯 Fetching {selected_category} news from India...")
        
        # Try India-specific sources first
        params = {
            'apiKey': NEWS_API_KEY,
            'country': 'in',
            'category': selected_category,
            'pageSize': 20  # Get more articles to filter
        }
        response = requests.get(NEWS_API_URL, params=params, timeout=10)
        
        if response.status_code == 200:
            articles = response.json().get('articles', [])
            # Filter out already used articles
            new_articles = [a for a in articles if a.get('url') not in USED_ARTICLES]
            
            if new_articles:
                print(f"📰 Found {len(new_articles)} new Indian {selected_category} articles")
                return new_articles[:5]  # Return top 5 new articles
            
            # If no country-specific news, try Indian-focused search with variety
            print("⚠️  No country news found, searching Indian topics...")
            search_queries = [
                'India politics',
                'Indian business startup',
                'Bollywood entertainment',
                'cricket India',
                'Indian technology',
                'Mumbai Delhi',
                'Indian economy',
                'India international'
            ]
            query = random.choice(search_queries)
            
            response = requests.get('https://newsapi.org/v2/everything', params={
                'apiKey': NEWS_API_KEY,
                'q': query,
                'pageSize': 20,
                'language': 'en',
                'sortBy': 'publishedAt',
                'domains': 'timesofindia.indiatimes.com,thehindu.com,ndtv.com,indianexpress.com,hindustantimes.com,livemint.com,business-standard.com'
            }, timeout=10)
            
            if response.status_code == 200:
                articles = response.json().get('articles', [])
                new_articles = [a for a in articles if a.get('url') not in USED_ARTICLES]
                if new_articles:
                    return new_articles[:5]
            
            # Fallback to general world news
            print("⚠️  Fetching world news...")
            topics = ['technology', 'business', 'science', 'politics', 'entertainment']
            topic = random.choice(topics)
            
            response = requests.get('https://newsapi.org/v2/everything', params={
                'apiKey': NEWS_API_KEY,
                'q': topic,
                'pageSize': 20,
                'language': 'en',
                'sortBy': 'publishedAt'
            }, timeout=10)
            
            if response.status_code == 200:
                articles = response.json().get('articles', [])
                new_articles = [a for a in articles if a.get('url') not in USED_ARTICLES]
                return new_articles[:5]
        else:
            print(f"News API error: {response.status_code}")
            if response.status_code == 426:
                print("⚠️  News API requires HTTPS. Trying alternative...")
            return []
    except Exception as e:
        print(f"Error fetching news: {e}")
        return []

def generate_satirical_content(news_headline, news_description):
    """Generate satirical article using Gemini AI"""
    
    prompt = f"""You are a friendly, witty Indian friend chatting over chai about current events. Write like you're gossiping with friends - natural, conversational, and funny.

News Topic: {news_headline}
Details: {news_description}

WRITING STYLE - Sound like a real person talking:
1. Write Hindi in Devanagari (हिंदी में), English in English
2. Mix both naturally like Indians actually speak: "अरे यार, this is too much na!"
3. Use conversational fillers: "अच्छा सुनो", "मतलब", "basically"
4. Ask rhetorical questions: "अब बताओ?", "क्या कहें?", "seriously?"
5. Use everyday comparisons and examples people relate to
6. Include real emotions - frustration, amusement, disbelief
7. Write in short, punchy sentences like people talk
8. Add personal observations: "मैंने तो सोचा", "देखो न"
9. Use colloquial expressions: "बिल्कुल सही", "ऐसा ही होता है"
10. Sound like storytelling, not reporting
11. Must mention iit jodhpur

TONE: Friendly sarcasm, like joking with friends. Not mean, just amused and witty.

STRUCTURE:
- Opening: Hook readers with a relatable observation
- Middle: Tell the story with humor and personal reactions
- Ending: Witty conclusion that makes them smile

Length: 400-600 words with natural paragraph breaks

Example tone: "अरे भाई, अभी-अभी मैंने यह news पढ़ी और हंसी नहीं रुकी। मतलब seriously, कभी-कभी लगता है कि हम comedy show में रहते हैं! 😂"

Format the response as:
TITLE: [Catchy, conversational title mixing Hindi and English like people actually talk]
SUBTITLE: [A one-liner that sounds like something your friend would say]
CONTENT: [Full article written in natural, conversational style. Mix Hindi देवनागरी and English like real people speak. Use short paragraphs. Sound human, not robotic!]
"""

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.9,
                max_output_tokens=4000,
            )
        )
        
        text = response.text
        
        # Parse the response
        title_match = re.search(r'TITLE:\s*(.+?)(?:\n|$)', text)
        subtitle_match = re.search(r'SUBTITLE:\s*(.+?)(?:\n|$)', text)
        content_match = re.search(r'CONTENT:\s*(.+)', text, re.DOTALL)
        
        title = title_match.group(1).strip() if title_match else news_headline
        subtitle = subtitle_match.group(1).strip() if subtitle_match else ""
        content = content_match.group(1).strip() if content_match else text
        
        return {
            'title': title,
            'subtitle': subtitle,
            'content': content
        }
        
    except Exception as e:
        print(f"Gemini API error: {e}")
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
        # Check if bot user exists
        bot_user = db.query(User).filter(User.id == AI_BOT_USER_ID).first()
        if not bot_user:
            print(f"Bot user with ID {AI_BOT_USER_ID} not found!")
            return False
        
        # Generate unique slug
        base_slug = generate_slug(article_data['title'])
        slug = base_slug
        counter = 1
        
        while db.query(Post).filter(Post.slug == slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
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
        
        print(f"✅ Created post: {new_post.title}")
        print(f"   Slug: {new_post.slug}")
        return True
        
    except Exception as e:
        print(f"Error creating post: {e}")
        db.rollback()
        return False

def run_ai_content_generator():
    """Main function to generate and post satirical content"""
    print(f"\n🤖 AI Content Generator started at {datetime.now()}")
    print("=" * 60)
    
    # Check if News API key is available
    if not NEWS_API_KEY:
        print("⚠️  NEWS_API_KEY not found. Using fallback topics...")
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
    else:
        # Fetch real news
        news_articles = get_recent_news()
    
    if not news_articles:
        print("❌ No news articles found")
        return
    
    # Get database session
    db = SessionLocal()
    
    try:
        # Pick a random article from the list for variety
        import random
        article = random.choice(news_articles)
        headline = article.get('title', '')
        description = article.get('description', '') or article.get('content', '')
        article_url = article.get('url', '')
        
        # Track used article
        if article_url:
            USED_ARTICLES.add(article_url)
            # Keep only last 100 used articles
            if len(USED_ARTICLES) > 100:
                USED_ARTICLES.pop()
        
        print(f"\n📰 Processing news: {headline[:50]}...")
        
        # Generate satirical content
        print("🎭 Generating satirical content with AI...")
        satirical_content = generate_satirical_content(headline, description)
        
        if satirical_content:
            print("✍️  Content generated successfully!")
            
            # Create post
            success = create_satirical_post(db, satirical_content)
            
            if success:
                print("✅ Post published successfully!")
                print(f"\n📝 Title: {satirical_content['title']}")
                print(f"📌 Subtitle: {satirical_content['subtitle']}")
                print(f"📄 Content length: {len(satirical_content['content'])} characters")
            else:
                print("❌ Failed to publish post")
        else:
            print("❌ Failed to generate content")
            
    except Exception as e:
        print(f"❌ Error in AI content generator: {e}")
    finally:
        db.close()
    
    print("\n" + "=" * 60)
    print(f"🤖 AI Content Generator finished at {datetime.now()}\n")

def setup_bot_user():
    """Create the AI bot user if it doesn't exist"""
    db = SessionLocal()
    try:
        bot_user = db.query(User).filter(User.username == "satirical_bot").first()
        
        if not bot_user:
            print("Creating AI bot user...")
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
            
            print(f"✅ Bot user created with ID: {bot_user.id}")
            print(f"   Username: {bot_user.username}")
            print(f"   Update AI_BOT_USER_ID in this script to: {bot_user.id}")
            return bot_user.id
        else:
            print(f"✅ Bot user already exists with ID: {bot_user.id}")
            return bot_user.id
            
    except Exception as e:
        print(f"❌ Error setting up bot user: {e}")
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
