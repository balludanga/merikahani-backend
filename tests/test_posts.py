def test_get_posts(test_client):
    response = test_client.get("/api/posts?limit=10")
    assert response.status_code == 200
    assert isinstance(response.json(), list)