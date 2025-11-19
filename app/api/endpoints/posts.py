from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import re
from app.database import get_db
from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostCreate, PostUpdate, PostResponse
from app.core.dependencies import get_current_user

router = APIRouter()

def generate_slug(title: str) -> str:
    slug = re.sub(r'[^\w\s-]', '', title.lower())
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug[:100]

@router.get("", response_model=List[PostResponse])
def get_posts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    published: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(Post)
    if published is not None:
        query = query.filter(Post.published == published)
    else:
        query = query.filter(Post.published == 1)  # Only published by default
    
    posts = query.order_by(Post.created_at.desc()).offset(skip).limit(limit).all()
    return posts

@router.get("/{post_id}", response_model=PostResponse)
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post

@router.get("/slug/{slug}", response_model=PostResponse)
def get_post_by_slug(slug: str, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.slug == slug).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post

@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(post_data: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slug = generate_slug(post_data.title)
    # Ensure slug is unique
    existing = db.query(Post).filter(Post.slug == slug).first()
    if existing:
        slug = f"{slug}-{datetime.now().timestamp()}"
    
    db_post = Post(
        title=post_data.title,
        subtitle=post_data.subtitle,
        content=post_data.content,
        slug=slug,
        cover_image=post_data.cover_image,
        author_id=current_user.id,
        published=post_data.published
    )
    db.add(db_post)
    db.commit()
    db.refresh(db_post)
    return db_post

@router.put("/{post_id}", response_model=PostResponse)
def update_post(
    post_id: int,
    post_data: PostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this post")
    
    update_data = post_data.dict(exclude_unset=True)
    if "title" in update_data and update_data["title"] != post.title:
        slug = generate_slug(update_data["title"])
        existing = db.query(Post).filter(Post.slug == slug, Post.id != post_id).first()
        if existing:
            slug = f"{slug}-{datetime.now().timestamp()}"
        update_data["slug"] = slug
    
    for field, value in update_data.items():
        setattr(post, field, value)
    
    db.commit()
    db.refresh(post)
    return post

@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this post")
    
    db.delete(post)
    db.commit()
    return None

@router.get("/user/{user_id}", response_model=List[PostResponse])
def get_user_posts(
    user_id: int,
    published: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(Post).filter(Post.author_id == user_id)
    if published is not None:
        query = query.filter(Post.published == published)
    posts = query.order_by(Post.created_at.desc()).all()
    return posts

