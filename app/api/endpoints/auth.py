
from fastapi import APIRouter, Depends, HTTPException, status, Request
import requests
import os
from app.schemas.user import Token
from sqlalchemy.orm import Session
from datetime import timedelta
from app.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse
from app.core.security import verify_password, get_password_hash, create_access_token
from app.core.config import settings
from app.core.dependencies import get_current_user

router = APIRouter()

@router.post("/google-login", response_model=Token)
async def google_login(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    token = data.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="Missing Google token")

    # Verify Google token
    google_client_id = settings.GOOGLE_CLIENT_ID
    verify_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
    resp = requests.get(verify_url)
    if resp.status_code != 200:
        print(f"Google token verification failed: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {resp.text}")
    payload = resp.json()
    print(f"Token payload: {payload}")
    if payload.get("aud") != google_client_id:
        print(f"Audience mismatch: expected {google_client_id}, got {payload.get('aud')}")
        raise HTTPException(status_code=401, detail=f"Invalid Google client ID: expected {google_client_id}, got {payload.get('aud')}")

    email = payload.get("email")
    full_name = payload.get("name")
    avatar_url = payload.get("picture")
    if not email:
        raise HTTPException(status_code=400, detail="Google account missing email")

    # Find or create user
    user = db.query(User).filter(User.email == email).first()
    if not user:
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

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    try:
        # Check if user already exists
        db_user = db.query(User).filter(User.email == user_data.email).first()
        if db_user:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        db_user = db.query(User).filter(User.username == user_data.username).first()
        if db_user:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Create new user
        hashed_password = get_password_hash(user_data.password)
        from datetime import datetime
        db_user = User(
            email=user_data.email,
            username=user_data.username,
            full_name=user_data.full_name,
            hashed_password=hashed_password,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"Error in register endpoint: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/login", response_model=Token)
def login(user_credentials: UserLogin, db: Session = Depends(get_db)):
    print(f"Login attempt: email={user_credentials.email}, password length={len(user_credentials.password)}")
    user = db.query(User).filter(User.email == user_credentials.email).first()
    if not user or not verify_password(user_credentials.password, user.hashed_password):
        print(f"Login failed: user found={user is not None}, password valid={user and verify_password(user_credentials.password, user.hashed_password)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    print(f"Login successful for user {user.email}")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }

@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    return current_user

