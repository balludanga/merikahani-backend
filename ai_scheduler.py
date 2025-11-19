"""
Scheduler for AI Content Bot
Automatically posts satirical articles at scheduled intervals
"""

import schedule
import time
from datetime import datetime
import sys
import os

# Add parent directory to path to import ai_content_bot
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_content_bot import run_ai_content_generator

def job():
    """Job to run the AI content generator"""
    print(f"\n{'='*60}")
    print(f"⏰ Scheduled job triggered at {datetime.now()}")
    print(f"{'='*60}")
    run_ai_content_generator()

def run_scheduler():
    """Run the scheduler"""
    print("""
╔════════════════════════════════════════════════════════════╗
║        🤖 AI SATIRICAL CONTENT BOT SCHEDULER 🎭           ║
╚════════════════════════════════════════════════════════════╝

    Starting automated satirical content generation...
    
    📅 Schedule: Every 5 minutes
    🇮🇳 Focus: Indian News
    🕐 Next run times will be displayed below
    
    Press Ctrl+C to stop
    
""")
    
    # Schedule: Post every 5 minutes
    schedule.every(5).minutes.do(job)
    
    # Alternative schedules (commented):
    # schedule.every(3).hours.do(job)  # Every 3 hours
    # schedule.every(6).hours.do(job)  # Every 6 hours
    # schedule.every().day.at("09:00").do(job)  # Daily at 9 AM
    # schedule.every().day.at("18:00").do(job)  # Daily at 6 PM
    
    # Run first job immediately
    print("🚀 Running first job immediately...")
    job()
    
    print(f"\n✅ Scheduler started successfully!")
    print(f"⏰ Next run: {schedule.next_run()}\n")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\n\n🛑 Scheduler stopped by user")
        print("Goodbye! 👋\n")
    except Exception as e:
        print(f"\n❌ Error: {e}")
