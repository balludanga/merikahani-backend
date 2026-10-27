from app.core.config import settings


def test_read_root(test_client):
    response = test_client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "लेखनी — हिंदी लेखकों का अपना मंच", "name": "लेखनी"}


def test_the_headers_cors_middleware_leaves_to_us(test_client):
    """COOP must allow the Google sign-in popup to come back, and a browser on a local network
    asks for a private-network opt-in before it will even reach a development backend."""
    plain = test_client.get("/")
    assert plain.headers["cross-origin-opener-policy"] == "same-origin-allow-popups"
    # Only said when asked: a normal reader's response carries no private-network claim.
    assert "access-control-allow-private-network" not in plain.headers

    asked = test_client.get("/", headers={"access-control-request-private-network": "true"})
    assert asked.headers["access-control-allow-private-network"] == "true"


def test_a_private_network_preflight_is_answered_not_refused(test_client):
    """The marker rides on the *preflight*, and CORSMiddleware answers preflights itself
    without ever reaching a route or the middleware above. If it answers
    "400 Disallowed CORS private-network", the browser never sends the real request, and a
    public page loses every way to talk to a backend that lives on a private address."""
    origin = settings.FRONTEND_URL.rstrip("/")
    preflight = test_client.options(
        "/posts",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers["access-control-allow-origin"] == origin
    # Exactly one value: set once, not appended to by a second middleware.
    assert preflight.headers["access-control-allow-private-network"] == "true"
