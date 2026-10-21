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
