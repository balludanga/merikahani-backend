"""Tests for per-account text highlights."""

from tests.test_posts import _create_post


def test_highlight_roundtrip(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "हाइलाइट परीक्षा")
    passage = "यह एक परीक्षण लेख है।"

    created = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": passage},
        headers=writer_a["headers"],
    )
    assert created.status_code == 201, created.text
    highlight = created.json()
    assert highlight["text"] == passage
    assert highlight["post_id"] == post["id"]

    # Re-highlighting the same passage returns the existing row
    duplicate = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": passage},
        headers=writer_a["headers"],
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == highlight["id"]

    listed = test_client.get(
        "/api/highlights", params={"post_id": post["id"]}, headers=writer_a["headers"]
    )
    assert listed.status_code == 200
    assert any(item["id"] == highlight["id"] for item in listed.json())

    removed = test_client.delete(
        f"/api/highlights/{highlight['id']}", headers=writer_a["headers"]
    )
    assert removed.status_code == 204
    after = test_client.get("/api/highlights", headers=writer_a["headers"])
    assert all(item["id"] != highlight["id"] for item in after.json())


def test_my_highlight_list_is_private_but_delete_is_owner_only(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "निजी सूची")
    created = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": "दूसरा अंश यहाँ"},
        headers=writer_a["headers"],
    )
    assert created.status_code == 201

    # The "my highlights" list only ever holds the caller's own rows
    other_view = test_client.get("/api/highlights", headers=writer_b["headers"])
    assert other_view.status_code == 200
    assert all(item["id"] != created.json()["id"] for item in other_view.json())

    denied = test_client.delete(
        f"/api/highlights/{created.json()['id']}", headers=writer_b["headers"]
    )
    assert denied.status_code == 403


def test_every_reader_sees_who_highlighted(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "सबके हाइलाइट")
    passage = "यह एक परीक्षण"
    first = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": passage, "start": 0, "prefix": "", "suffix": " लेख है।"},
        headers=writer_a["headers"],
    )
    second = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": passage, "start": 0},
        headers=writer_b["headers"],
    )
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    # Another reader, and a signed-out visitor, both see both marks and the names
    for headers in (writer_b["headers"], {}):
        listed = test_client.get(f"/api/highlights/post/{post['id']}", headers=headers)
        assert listed.status_code == 200, listed.text
        by_id = {item["id"]: item for item in listed.json()}
        assert by_id[first.json()["id"]]["user_id"] == writer_a["user"]["id"]
        assert by_id[first.json()["id"]]["user"]["username"] == writer_a["user"]["username"]
        assert by_id[second.json()["id"]]["user"]["username"] == writer_b["user"]["username"]
        assert by_id[first.json()["id"]]["start_offset"] == 0
        assert "email" not in by_id[first.json()["id"]]["user"]


def test_same_words_at_two_places_are_two_highlights(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "दो जगह")
    ids = set()
    for start in (0, 22):
        response = test_client.post(
            "/api/highlights",
            json={"post_id": post["id"], "text": "यह एक", "start": start},
            headers=writer_a["headers"],
        )
        assert response.status_code == 201
        ids.add(response.json()["id"])
    assert len(ids) == 2
    again = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": "यह एक", "start": 22},
        headers=writer_a["headers"],
    )
    assert again.json()["id"] in ids and len(ids) == 2


def test_old_highlight_without_a_position_adopts_one(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "पुराना हाइलाइट")
    old = test_client.post(
        "/api/highlights", json={"post_id": post["id"], "text": "परीक्षण लेख"}, headers=writer_a["headers"]
    ).json()
    assert old["start_offset"] is None
    healed = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": "परीक्षण लेख", "start": 7, "prefix": "एक ", "suffix": " है।"},
        headers=writer_a["headers"],
    ).json()
    assert healed["id"] == old["id"]
    assert healed["start_offset"] == 7 and healed["prefix"] == "एक "


def test_signed_out_visitor_gets_only_highlights_inside_the_teaser(test_client, writer_a, writer_b):
    body = "\n\n".join(f"अनुच्छेद संख्या {n} में कुछ शब्द लिखे गए हैं ताकि लेख लंबा हो जाए।" for n in range(40))
    response = test_client.post(
        "/api/posts",
        json={"title": "लंबी रचना हाइलाइट", "subtitle": "उप", "content": body, "published": 1},
        headers=writer_a["headers"],
    )
    assert response.status_code == 201, response.text
    post = response.json()
    early = "अनुच्छेद संख्या 0 में कुछ शब्द"
    late = "अनुच्छेद संख्या 39 में कुछ शब्द"
    for text in (early, late):
        assert test_client.post(
            "/api/highlights", json={"post_id": post["id"], "text": text}, headers=writer_b["headers"]
        ).status_code == 201

    anonymous = test_client.get(f"/api/highlights/post/{post['id']}").json()
    assert [item["text"] for item in anonymous] == [early]
    member = test_client.get(f"/api/highlights/post/{post['id']}", headers=writer_a["headers"]).json()
    assert {item["text"] for item in member} == {early, late}


def test_draft_highlights_are_not_listed_to_others(test_client, writer_a, writer_b):
    draft = _create_post(test_client, writer_a["headers"], "मसौदा हाइलाइट", published=0)
    assert test_client.get(f"/api/highlights/post/{draft['id']}", headers=writer_b["headers"]).status_code == 404
    assert test_client.get(f"/api/highlights/post/{draft['id']}").status_code == 404


def test_highlight_requires_auth(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "अनधिकृत हाइलाइट")
    response = test_client.post(
        "/api/highlights", json={"post_id": post["id"], "text": "बिना खाते के"}
    )
    assert response.status_code == 401
