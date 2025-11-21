from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.api import api_router
from app.core.config import settings
from app.database import engine, Base

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)

# Update CORS to handle dynamic Vercel URLs
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOW_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",  # Allow all Vercel deployments
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Medium Clone API!"}

@app.post("/api/trigger-ai-bot")
async def trigger_ai_bot(request: Request):
    # Require custom token header
    token = request.headers.get("X-KAHANI-BACKGROUND-BOT-TOKEN")
    if token != "e547365bae0244f3afd6b511581e99eb5a4c6246e83e464fafd784c52e832e93":
        raise HTTPException(status_code=401, detail="Invalid bot token")
    """
    Endpoint to manually trigger AI bot content generation.
    Requires X-KAHANI-BACKGROUND-BOT-TOKEN header for security.
    Can be called by external cron services (e.g., cron-job.org, EasyCron)
    """
    try:
        import subprocess
        import sys
        from pathlib import Path
        
        # Use sys.path to find where the app module is
        # The ai_content_bot.py should be in the same directory as the app folder
        app_dir = Path(__file__).parent.parent  # This is backend/app, go up one level
        bot_script = app_dir / "ai_content_bot.py"
        
        # Run the AI bot script using the same python interpreter
        result = subprocess.run(
            [sys.executable, str(bot_script), "test"],
            cwd=str(app_dir),
            capture_output=True,
            text=True,
            timeout=120
        )
        
        return {
            "status": "success" if result.returncode == 0 else "error",
            "message": "AI bot executed",
            "returncode": result.returncode,
            "bot_script_path": str(bot_script),
            "bot_script_exists": bot_script.exists(),
            "stdout": result.stdout[:1000] if result.stdout else None,
            "stderr": result.stderr[:1000] if result.stderr else None
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc()[:1000]
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)