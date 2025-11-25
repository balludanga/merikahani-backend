from fastapi import APIRouter, Depends, HTTPException, status, Request
import requests
import os
import logging
from app.schemas.user import Token, UserResponse
from sqlalchemy.orm import Session
from datetime import timedelta
from app.database import get_db
from app.models.user import User
from app.core.security import get_password_hash, create_access_token
from app.core.config import settings
from app.core.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/google-login", response_model=Token)
async def google_login(request: Request, db: Session = Depends(get_db)):
    logger.info("Google login attempt initiated")
    data = await request.json()
    token = data.get("token")
    if not token:
        logger.warning("Google login failed: Missing token in request")
        raise HTTPException(status_code=400, detail="Missing Google token")

    # Verify Google token
    google_client_id = settings.GOOGLE_CLIENT_ID
    verify_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
    logger.info(f"Verifying Google token with client_id: {google_client_id[:10]}...")
    resp = requests.get(verify_url)
    if resp.status_code != 200:
        logger.error(f"Google token verification failed: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {resp.text}")
    payload = resp.json()
    logger.info(f"Token payload received: email={payload.get('email')}, aud={payload.get('aud')}")
    if payload.get("aud") != google_client_id:
        logger.error(f"Audience mismatch: expected {google_client_id}, got {payload.get('aud')}")
        raise HTTPException(status_code=401, detail=f"Invalid Google client ID: expected {google_client_id}, got {payload.get('aud')}")

    email = payload.get("email")
    full_name = payload.get("name")
    avatar_url = payload.get("picture")
    if not email:
        logger.warning("Google login failed: No email in token payload")
        raise HTTPException(status_code=400, detail="Google account missing email")

    # Find or create user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        logger.info(f"Creating new user for email: {email}")
        from datetime import datetime
        user = User(
            email=email,
            username=email.split('@')[0],
            full_name=full_name,
            avatar_url=avatar_url,
            hashed_password=get_password_hash(token),  # Not used, but required
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"New user created: id={user.id}, email={email}")
    else:
        logger.info(f"Existing user found: id={user.id}, email={email}")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    logger.info(f"Google login successful for user {email}, token expires in {settings.ACCESS_TOKEN_EXPIRE_MINUTES} minutes")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }

@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    logger.info(f"User info requested for user id={current_user.id}, email={current_user.email}")
    return current_user

