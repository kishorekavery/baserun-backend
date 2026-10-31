"""
Profile photos. Covers the test checklist in the app repo's avatar_api.md.
"""
import io

from PIL import Image

from services import avatar_service
from tests.conftest import db_run, register, square_loop

AVATAR_URL = "/api/v1/users/me/avatar"


def encoded(image: Image.Image, fmt: str = "JPEG", **options) -> bytes:
    output = io.BytesIO()
    image.save(output, format=fmt, **options)
    return output.getvalue()


def jpeg(size=(800, 600), color="navy") -> bytes:
    return encoded(Image.new("RGB", size, color))


def upload(client, user, data: bytes, filename="avatar.jpg", content_type="image/jpeg"):
    return client.post(AVATAR_URL, files={"file": (filename, data, content_type)}, headers=user["headers"])


def path_of(url: str) -> str:
    return "/" + url.split("://", 1)[1].split("/", 1)[1]


def served(client, response) -> Image.Image:
    image = client.get(path_of(response.json()["avatar_url"]))
    assert image.status_code == 200
    return Image.open(io.BytesIO(image.content))


def test_phone_photo_comes_back_square_upright_and_without_exif(client):
    user = register(client, "photo")
    # A sideways 4000x3000 sensor image: the top of the scene is on its left edge,
    # and orientation 6 tells viewers to rotate it 90 degrees clockwise.
    sideways = Image.new("RGB", (4000, 3000), "blue")
    sideways.paste("red", (0, 0, 2000, 3000))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "Secret Camera"
    response = upload(client, user, encoded(sideways, exif=exif, quality=95))
    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Profile photo updated"

    photo = served(client, response)
    assert photo.size == (512, 512)
    assert photo.format == "JPEG"
    assert len(photo.getexif()) == 0
    assert "exif" not in photo.info

    def is_red(pixel):
        return pixel[0] > 200 and pixel[2] < 60

    assert is_red(photo.getpixel((256, 20)))
    assert not is_red(photo.getpixel((256, 490)))


def test_transparent_png_gets_a_white_background(client):
    user = register(client, "photo")
    response = upload(
        client, user, encoded(Image.new("RGBA", (600, 600), (0, 0, 0, 0)), "PNG"),
        filename="avatar.png", content_type="image/png",
    )
    assert response.status_code == 200, response.text
    assert min(served(client, response).getpixel((256, 256))) > 250


def test_webp_is_accepted_and_non_square_is_center_cropped(client):
    user = register(client, "photo")
    wide = Image.new("RGB", (900, 300), "black")
    wide.paste("white", (300, 0, 600, 300))  # only the center survives the crop
    response = upload(client, user, encoded(wide, "WEBP", lossless=True), filename="a.webp", content_type="image/webp")
    assert response.status_code == 200, response.text
    photo = served(client, response)
    assert photo.size == (512, 512)
    assert min(photo.getpixel((256, 256))) > 240
    assert min(photo.getpixel((20, 256))) > 240


def test_oversized_upload_is_rejected(client):
    user = register(client, "photo")
    response = upload(client, user, b"\xff\xd8\xff" + b"0" * (6 * 1024 * 1024))
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_file_just_over_the_limit_is_rejected_despite_the_framing_allowance(client):
    user = register(client, "photo")
    response = upload(client, user, b"\xff\xd8\xff" + b"0" * avatar_service.MAX_FILE_BYTES)
    assert response.status_code == 413


def test_oversized_chunked_upload_is_cut_off_without_a_content_length(client):
    user = register(client, "photo")

    def chunks():
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n'
        for _ in range(100):
            yield b"0" * (256 * 1024)

    response = client.post(
        AVATAR_URL, content=chunks(),
        headers={**user["headers"], "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 413


def test_executable_renamed_to_jpg_is_rejected(client):
    user = register(client, "photo")
    response = upload(client, user, b"MZ\x90\x00" + b"\x00" * 2048)
    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_corrupt_image_with_valid_magic_bytes_is_400(client):
    user = register(client, "photo")
    assert upload(client, user, b"\xff\xd8\xff\xe0" + b"garbage" * 100).status_code == 400
    truncated = encoded(Image.effect_noise((800, 600), 80).convert("RGB"))[:2000]
    assert upload(client, user, truncated).status_code == 400


def test_decompression_bomb_is_rejected_before_decoding(client):
    user = register(client, "photo")
    # Compresses to a small file; decoding it would take gigabytes
    bomb = encoded(Image.new("1", (40000, 40000)), "PNG")
    assert len(bomb) < avatar_service.MAX_FILE_BYTES
    assert upload(client, user, bomb, filename="b.png", content_type="image/png").status_code == 400


def test_missing_or_wrong_field_is_400(client):
    user = register(client, "photo")
    wrong_field = client.post(AVATAR_URL, files={"photo": ("a.jpg", jpeg(), "image/jpeg")}, headers=user["headers"])
    assert wrong_field.status_code == 400
    assert client.post(AVATAR_URL, json={}, headers=user["headers"]).status_code == 400


def test_upload_and_remove_require_auth(client):
    assert client.post(AVATAR_URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")}).status_code == 401
    assert client.delete(AVATAR_URL).status_code == 401


def test_each_upload_gets_a_new_url_and_replaces_the_old_photo(client):
    user = register(client, "photo")
    first = upload(client, user, jpeg(color="red")).json()["avatar_url"]
    second = upload(client, user, jpeg(color="blue")).json()["avatar_url"]
    assert first != second

    assert client.get(path_of(first)).status_code == 404
    current = client.get(path_of(second))
    assert current.status_code == 200
    assert current.headers["content-type"] == "image/jpeg"
    assert "immutable" in current.headers["cache-control"]
    assert current.headers["x-content-type-options"] == "nosniff"
    assert db_run("SELECT count(*) FROM avatars WHERE user_id = $1", user["user_id"], fetch="fetchval") == 1


def test_identity_response_shape_and_public_base_url(client, monkeypatch):
    user = register(client, "photo")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.example.com/")
    body = upload(client, user, jpeg()).json()
    assert body["user_id"] == str(user["user_id"])
    assert body["username"] == user["payload"]["username"]
    assert body["avatar_url"].startswith("https://cdn.example.com/api/v1/avatars/")
    assert body["avatar_url"].endswith(".jpg")


def test_forwarded_proto_is_used_behind_the_proxy(client):
    user = register(client, "photo")
    response = client.post(
        AVATAR_URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")},
        headers={**user["headers"], "x-forwarded-proto": "https"},
    )
    assert response.json()["avatar_url"].startswith("https://")


def test_new_photo_appears_everywhere_and_removal_empties_every_surface(client):
    user = register(client, "photo")
    friend = register(client, "pal")
    client.post("/api/v1/friends/requests", json={"to_user_id": friend["user_id"]}, headers=user["headers"])
    client.post("/api/v1/friends/requests", json={"to_user_id": user["user_id"]}, headers=friend["headers"])
    club_id = client.get("/api/v1/clubs").json()["clubs"][0]["id"]
    client.post(f"/api/v1/clubs/{club_id}/join", headers=user["headers"])
    base_id = client.post("/api/v1/bases/claim", json={"points": square_loop()}, headers=user["headers"]).json()["base_id"]

    def surfaces():
        signed_in = client.post("/auth/login", json={
            "username": user["payload"]["username"], "password": user["payload"]["password"],
        }).json()
        user["headers"] = {"Authorization": f"Bearer {signed_in['access_token']}"}
        board = client.get(f"/api/v1/clubs/{club_id}/leaderboard?limit=200").json()["leaderboard"]
        mine = next(e for e in board if e["user_id"] == str(user["user_id"]))
        pal = client.get("/api/v1/friends", headers=friend["headers"]).json()["friends"][0]
        return {
            "login": signed_in["avatar_url"] or "",
            "me": client.get("/users/me", headers=user["headers"]).json()["avatar_url"],
            "public": client.get(f"/users/{user['user_id']}/profile").json()["avatar_url"],
            "territory": client.get(f"/api/v1/territories/{base_id}").json()["owner_avatar"],
            "leaderboard": mine["runner_avatar"],
            "friends": pal["avatar_url"] or "",
        }

    url = upload(client, user, jpeg()).json()["avatar_url"]
    assert set(surfaces().values()) == {url}

    response = client.delete(AVATAR_URL, headers=user["headers"])
    assert response.status_code == 200
    assert response.json()["avatar_url"] == ""
    assert response.json()["message"] == "Profile photo removed"
    assert set(surfaces().values()) == {""}
    assert db_run("SELECT count(*) FROM avatars WHERE user_id = $1", user["user_id"], fetch="fetchval") == 0


def test_runner_without_a_photo_gets_empty_avatar_not_a_stock_photo(client):
    user = register(client, "photo")
    assert client.get("/users/me", headers=user["headers"]).json()["avatar_url"] == ""


def test_rate_limit_counts_uploads_even_after_removal(client, monkeypatch):
    monkeypatch.setattr(avatar_service, "MAX_UPLOADS_PER_HOUR", 2)
    user = register(client, "photo")
    assert upload(client, user, jpeg()).status_code == 200
    client.delete(AVATAR_URL, headers=user["headers"])
    assert upload(client, user, jpeg()).status_code == 200
    limited = upload(client, user, jpeg())
    assert limited.status_code == 429
    assert limited.json()["code"] == "TOO_MANY_REQUESTS"
    # Old uploads age out of the window
    db_run("UPDATE avatar_uploads SET created_at = NOW() - INTERVAL '2 hours' WHERE user_id = $1", user["user_id"])
    assert upload(client, user, jpeg()).status_code == 200


def test_setting_a_plain_url_drops_the_stored_photo_but_resending_it_does_not(client):
    user = register(client, "photo")
    url = upload(client, user, jpeg()).json()["avatar_url"]
    assert client.put("/users/identity", json={"avatar_url": url}, headers=user["headers"]).status_code == 200
    assert db_run("SELECT count(*) FROM avatars WHERE user_id = $1", user["user_id"], fetch="fetchval") == 1

    client.put("/users/identity", json={"avatar_url": "https://example.com/me.png"}, headers=user["headers"])
    assert db_run("SELECT count(*) FROM avatars WHERE user_id = $1", user["user_id"], fetch="fetchval") == 0


def test_unknown_image_key_is_404(client):
    assert client.get("/api/v1/avatars/nope.jpg").status_code == 404
