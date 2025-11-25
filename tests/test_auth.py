def test_google_login(test_client):
    payload = {
        "token": "mock_google_token"
    }
    response = test_client.post("/api/auth/google-login", json=payload)
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert "user" in response.json()