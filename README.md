# लेखनी (Lekhani) — Backend

Backend API for **Lekhani (लेखनी)** — a home for Hindi writers. कहानी, कविता, निबंध और संस्मरण — सब मानवीय लेखन, बिना किसी कृत्रिम लेखन सहायता के।

Built with FastAPI + SQLAlchemy.

## Requirements

- Python 3.9+
- pip

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/balludanga/merikahani-backend.git lekhani-backend
   cd lekhani-backend
   ```

2. (Optional) Copy the env example and adjust values:

   ```bash
   cp .env.example .env
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

```bash
uvicorn app.main:app --reload
```

This starts the API at `http://127.0.0.1:8000`. Local development uses SQLite (`app.db`) by default; set `DATABASE_URL` for PostgreSQL in production.

Interactive API docs: `http://127.0.0.1:8000/docs`

## API Endpoints

### Auth (`/api/auth`)
- `POST /register` — email/password registration
- `POST /login` — email/password login
- `POST /google-login` — Google sign-in
- `GET /me` — current user

### Posts (`/api/posts`)
- `GET /` — published feed (filters: `topic`, `tag`, `q`, `feed=following`)
- `GET /me` — my posts (drafts + published)
- `GET /slug/{slug}` — post by slug
- `GET /user/{user_id}` — a writer's posts
- `POST /` — create (supports `tags: ["कहानी", ...]`, max 5)
- `PUT /{post_id}` / `DELETE /{post_id}` — edit/delete own posts
- `POST /{post_id}/clap` — clap (up to 50 per reader)

### Comments (`/api/comments`)
- `GET /post/{post_id}` — responses on a post
- `POST /` / `DELETE /{comment_id}` — add/remove your response

### Users (`/api/users`)
- `GET /{user_id}` — profile with `follower_count`, `following_count`, `post_count`, `is_following`

### Follows (`/api/follows`)
- `POST /{user_id}` / `DELETE /{user_id}` — follow / unfollow
- `GET /{user_id}/followers` / `GET /{user_id}/following`

### Bookmarks (`/api/bookmarks`)
- `GET /` — my reading list
- `POST /{post_id}` / `DELETE /{post_id}` — save / remove

### Tags (`/api/tags`)
- `GET /` — popular tags (with published-post counts)
- `GET /{slug}/posts` — posts under a tag

### SEO
- `GET /sitemap.xml` — sitemap
- `GET /rss.xml` — RSS feed

Set `FRONTEND_URL` so generated sitemap/RSS links point at the Lekhani frontend.

## Project Structure

```
app/
├── api/
│   ├── endpoints/   # auth, posts, comments, users, follows, bookmarks, tags, seo
│   └── __init__.py
├── core/            # config, security, dependencies
├── models/          # User, Post, Comment, Clap, Follow, Bookmark, Tag
├── schemas/         # Pydantic schemas
├── database.py
└── main.py
tests/
```

## Tests

```bash
pytest
```

## Contributing

Feel free to submit issues or pull requests for any improvements or bug fixes.
