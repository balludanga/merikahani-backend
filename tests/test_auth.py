def test_register_and_login(test_client):
    import uuid

    unique = uuid.uuid4().hex[:8]
    payload = {
        "email": f"auth_{unique}@example.com",
        "username": f"auth_{unique}",
        "password": "secret123",
    }
    response = test_client.post("/api/auth/register", json=payload)
    assert response.status_code == 201

    login = test_client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert login.status_code == 200
    assert "access_token" in login.json()
    assert "user" in login.json()


def test_google_login(test_client):
    payload = {
        "token": "mock_google_token"
    }
    response = test_client.post("/api/auth/google-login", json=payload)
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert "user" in response.json()


def test_google_username_fits_the_profile_shape(test_client):
    """A Google account inherits its username from the address, so the address has to be
    cut down to what the profile itself allows: ^[\w.]{3,30}$."""
    from app.api.endpoints.auth import _unique_username
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        # Dots are part of the allowed shape; the rest of an address's punctuation goes.
        assert _unique_username(db, "a.b+c") == "a.bc"
        assert _unique_username(db, ".jaya.") == "jaya"
        assert _unique_username(db, "+ +") == "writer"
        assert _unique_username(db, "ab") == "writerab"
        assert _unique_username(db, "x" * 40) == "x" * 30

        # A name already taken gets a random suffix that stays inside the same cap.
        from app.models.user import User

        taken = "y" * 30
        clash = User(
            email="clash-test@example.com", username=taken,
            hashed_password="x", auth_provider="google",
        )
        db.add(clash)
        db.commit()
        try:
            made = _unique_username(db, "y" * 40)
            assert made != taken
            assert 3 <= len(made) <= 30
        finally:
            db.delete(clash)
            db.commit()
    finally:
        db.close()
