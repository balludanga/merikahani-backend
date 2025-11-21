from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    app_name: str = "My Fullstack Project"
    admin_email: str = "admin@example.com"
    items_per_page: int = 10
    debug: bool = True
    
    # Database
    DATABASE_URL: str = "sqlite:///./app.db"
    
    # Security
    SECRET_KEY: str = "your-secret-key-here-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # CORS
    ALLOW_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:3001"]
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from .env file

settings = Settings()