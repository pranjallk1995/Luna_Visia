import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image
from starlette.testclient import TestClient

from config import SSOConfig, UIConfig
from gateway import UIGateway
from sso import CURRENT_USER

OWNER = {"user_id": "owner", "email": "owner@example.com", "email_verified": True}
STRANGER = {"user_id": "stranger", "email": "other@example.com", "email_verified": True}
TOKEN = "t" * 32
PIN = "246810"


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.image_dir = Path(directory.name) / "u_owner"
        self.image_dir.mkdir()
        Image.new("RGB", (64, 32), "red").save(self.image_dir / "a.png")
        Image.new("RGB", (16, 16), "blue").save(self.image_dir / "b b.png")
        self.backend_requests: list[httpx.Request] = []
        self.backend_status = 200

        self.pin_set, self.sentia_up, self.hidden = True, True, set()
        self.sso_requests: list[httpx.Request] = []

        def sso(request: httpx.Request) -> httpx.Response:
            self.sso_requests.append(request)
            session = {"owner": OWNER, "owner-phone": OWNER, "stranger": STRANGER}.get(request.headers["X-Luna-Session"])
            if not session:
                return httpx.Response(401)
            if request.url.path == "/pin/status":
                return httpx.Response(200, json={"pin_set": self.pin_set, "locked_seconds": 0})
            if request.url.path == "/pin/verify":
                if not self.sentia_up:
                    return httpx.Response(503)
                if json.loads(request.content)["pin"] == PIN:
                    return httpx.Response(200, json={"ok": True})
                return httpx.Response(403, json={"error": "Wrong PIN. 4 tries left.", "attempts_left": 4})
            return httpx.Response(200, json=session)

        def backend(request: httpx.Request) -> httpx.Response:
            self.backend_requests.append(request)
            if request.url.path == "/api/hidden/images":
                return httpx.Response(200, json={"images": [{"name": name} for name in sorted(self.hidden)]})
            if request.url.path == "/api/metadata":
                images = [{"name": "a.png", "tags": ["x"]}, {"name": "b b.png"}, {"name": "gone.png"}]
                return httpx.Response(self.backend_status, json={"images": images})
            return httpx.Response(self.backend_status, json={"status": "ok"})

        config = UIConfig(
            image_dir=self.image_dir.parent,
            api_url="http://backend",
            api_token=TOKEN,
            allowed_emails=frozenset({"owner@example.com"}),
            sso=SSOConfig(public_url="https://auth.example.com"),
        )
        self.gateway = UIGateway(config, httpx.MockTransport(backend), httpx.MockTransport(sso))
        self.client = TestClient(self.gateway.application(), base_url="https://visia.example.com")

    def unlock(self, pin: str = None) -> httpx.Response:
        return self.client.post("/app/hidden/unlock", json={"pin": pin or PIN})

    def sign_in(self, who: str = "owner") -> None:
        self.client.cookies.set(SSOConfig.COOKIE_NAME, who)


class SessionTests(GatewayTestCase):
    def test_signed_out_gets_a_login_link_back_to_this_app(self):
        session = self.client.get("/auth/session").json()
        self.assertEqual(session["status"], "signed_out")
        self.assertEqual(
            session["login_url"], "https://auth.example.com/login?next=https%3A%2F%2Fvisia.example.com%2F"
        )

    def test_forwarded_headers_give_the_public_address(self):
        session = self.client.get(
            "/auth/session", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "public.example.com"}
        ).json()
        self.assertIn("next=https%3A%2F%2Fpublic.example.com%2F", session["login_url"])

    def test_allowed_person_gets_settings_but_never_the_api_token(self):
        self.sign_in()
        response = self.client.get("/auth/session")
        self.assertEqual(response.json()["status"], "signed_in")
        self.assertEqual(response.json()["settings"]["images_per_page"], UIConfig.IMAGES_PER_PAGE)
        self.assertNotIn(TOKEN, response.text)

    def test_unlisted_person_is_forbidden(self):
        self.sign_in("stranger")
        self.assertEqual(self.client.get("/auth/session").json()["status"], "forbidden")


class HealthTests(GatewayTestCase):
    def test_health_paths_answer_without_sign_in(self):
        for path in ("/health", "/_stcore/health"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


class ProtectionTests(GatewayTestCase):
    ROUTES = (
        ("GET", "/app/images"),
        ("GET", "/app/hidden"),
        ("GET", "/app/thumbnails/a.png"),
        ("GET", "/app/files/a.png"),
        ("POST", "/api/favorites"),
        ("POST", "/api/hidden/delete"),
        ("POST", "/api/images/delete"),
        ("POST", "/api/images/metadata"),
        ("POST", "/api/hidden/images/metadata"),
    )

    def test_every_data_route_requires_sign_in(self):
        for method, path in self.ROUTES:
            self.assertEqual(self.client.request(method, path).status_code, 401, path)
        self.assertEqual(self.backend_requests, [])

    def test_every_data_route_rejects_unlisted_people(self):
        self.sign_in("stranger")
        for method, path in self.ROUTES:
            self.assertEqual(self.client.request(method, path).status_code, 403, path)
        self.assertEqual(self.backend_requests, [])

    def test_unknown_session_is_signed_out(self):
        self.sign_in("nobody")
        self.assertEqual(self.client.get("/app/images").status_code, 401)


class CollectionTests(GatewayTestCase):
    def test_records_gain_file_facts_and_missing_files_are_dropped(self):
        self.sign_in()
        images = self.client.get("/app/images").json()["images"]
        self.assertEqual({image["name"] for image in images}, {"a.png", "b b.png"})
        first = next(image for image in images if image["name"] == "a.png")
        self.assertEqual((first["width"], first["height"]), (64, 32))
        self.assertEqual(first["size_bytes"], (self.image_dir / "a.png").stat().st_size)
        self.assertEqual(first["tags"], ["x"])

    def test_saved_showcase_rank_orders_after_unranked_images(self):
        CURRENT_USER.set("owner")
        records = self.gateway.files.collection(
            [{"name": "a.png", "showcase_rank": 2}, {"name": "b b.png", "showcase_rank": 1}]
        )
        self.assertEqual([record["name"] for record in records], ["b b.png", "a.png"])

    def test_backend_failure_is_reported(self):
        self.sign_in()
        self.backend_status = 500
        self.assertEqual(self.client.get("/app/images").status_code, 502)


class FileTests(GatewayTestCase):
    def test_thumbnail_is_a_cacheable_webp_of_the_card_size(self):
        self.sign_in()
        response = self.client.get("/app/thumbnails/a.png")
        self.assertEqual(response.headers["content-type"], "image/webp")
        self.assertIn("immutable", response.headers["cache-control"])
        with Image.open(__import__("io").BytesIO(response.content)) as thumbnail:
            self.assertEqual(thumbnail.size, UIConfig.THUMBNAIL_SIZE)

    def test_file_downloads_under_the_requested_name(self):
        self.sign_in()
        response = self.client.get("/app/files/a.png", params={"download": "cat.png"})
        self.assertIn('filename="cat.png"', response.headers["content-disposition"])
        self.assertEqual(response.content, (self.image_dir / "a.png").read_bytes())

    def test_nothing_outside_the_vault_is_served(self):
        self.sign_in()
        (self.image_dir.parent / "secret.png").write_bytes(b"secret")
        self.addCleanup((self.image_dir.parent / "secret.png").unlink)
        for name in ("..%2Fsecret.png", "%2Fetc%2Fpasswd", "missing.png"):
            self.assertEqual(self.client.get(f"/app/files/{name}").status_code, 404, name)
            self.assertEqual(self.client.get(f"/app/thumbnails/{name}").status_code, 404, name)

    def test_only_image_files_are_served(self):
        self.sign_in()
        (self.image_dir / "notes.txt").write_text("private")
        self.assertEqual(self.client.get("/app/files/notes.txt").status_code, 404)


class ProxyTests(GatewayTestCase):
    def test_allowed_route_is_forwarded_with_the_private_token(self):
        self.sign_in()
        response = self.client.post("/api/favorites", json={"names": ["a.png"], "favorite": True})
        self.assertEqual(response.status_code, 200)
        forwarded = self.backend_requests[-1]
        self.assertEqual(forwarded.headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(json.loads(forwarded.content), {"names": ["a.png"], "favorite": True})

    def test_names_stay_encoded_on_the_way_to_the_backend(self):
        self.sign_in()
        self.client.delete("/api/images/b%20b.png")
        self.assertEqual(self.backend_requests[-1].url.raw_path, b"/api/images/b%20b.png")

    def test_embedded_metadata_is_readable_through_the_gateway(self):
        self.sign_in()
        self.assertEqual(self.client.get("/api/images/a.png/metadata").status_code, 200)
        self.assertEqual(self.backend_requests[-1].url.raw_path, b"/api/images/a.png/metadata")

    def test_bulk_routes_are_forwarded_with_a_batch_timeout(self):
        self.sign_in()
        self.unlock()
        for path in ("/api/images/delete", "/api/images/metadata", "/api/hidden/images/metadata", "/api/hidden/hide"):
            self.assertEqual(self.client.post(path, json={}).status_code, 200, path)
            self.assertEqual(self.backend_requests[-1].url.path, path)
            self.assertEqual(self.backend_requests[-1].extensions["timeout"]["read"], UIConfig.BATCH_TIMEOUT_SECONDS)

    def test_single_image_routes_keep_the_short_timeout(self):
        self.sign_in()
        self.client.post("/api/images/a.png/metadata", json={})
        self.assertEqual(self.backend_requests[-1].extensions["timeout"]["read"], UIConfig.API_TIMEOUT_SECONDS)

    def test_other_backend_routes_are_not_reachable(self):
        self.sign_in()
        for method, path in (
            ("POST", "/api/companion/images"),
            ("POST", "/api/uploads/token"),
            ("GET", "/api/images/a.png"),
            ("GET", "/api/downloads/token"),
            ("POST", "/api/hidden/unknown"),
        ):
            self.assertEqual(self.client.request(method, path).status_code, 404, path)
        self.assertEqual(self.backend_requests, [])

    def test_backend_errors_keep_their_status_and_message(self):
        self.sign_in()
        self.backend_status = 400
        self.assertEqual(self.client.post("/api/favorites", json={}).status_code, 400)

    def test_responses_carry_security_headers(self):
        response = self.client.get("/auth/session")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")


class SessionCacheTests(GatewayTestCase):
    def test_confirmed_session_survives_sentia_being_unreachable_but_never_invents_one(self):
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        self.sign_in()
        self.assertEqual(self.client.get("/auth/session").json()["status"], "signed_in")
        self.gateway.sso.client = httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
        self.gateway.sso.sessions["owner"]["checked"] = -1e9
        self.assertEqual(self.client.get("/auth/session").json()["status"], "signed_in")
        self.client.cookies.set(SSOConfig.COOKIE_NAME, "someone-else")
        self.assertEqual(self.client.get("/auth/session").json()["status"], "signed_out")


class SeparatePeopleTests(GatewayTestCase):
    def test_the_backend_is_told_who_and_a_browser_cannot_say_otherwise(self):
        self.sign_in()
        self.client.get("/app/images", headers={"X-Luna-User": "stranger"})
        self.assertEqual(self.backend_requests[-1].headers["X-Luna-User"], "owner")

    def test_files_and_lists_come_from_the_signed_in_persons_folder(self):
        self.gateway.sso.allowed_emails = frozenset({"*"})
        other = self.image_dir.parent / "u_stranger"
        other.mkdir()
        Image.new("RGB", (8, 8), "green").save(other / "only-theirs.png")
        self.sign_in()
        self.assertEqual(self.client.get("/app/files/only-theirs.png").status_code, 404)
        self.client.cookies.clear()
        self.sign_in("stranger")
        self.assertEqual(self.client.get("/app/files/only-theirs.png").status_code, 200)
        self.assertEqual(self.client.get("/app/files/a.png").status_code, 404)
        self.assertEqual(self.client.get("/app/images").json()["images"], [])
        self.assertEqual(self.client.get("/app/thumbnails/a.png").status_code, 404)

    def test_a_session_without_a_user_reaches_nothing(self):
        self.gateway.sso.sessions.clear()
        no_id = {"email": "owner@example.com", "email_verified": True}
        async def nobody(token):
            return no_id
        self.gateway.sso.user = nobody
        self.sign_in()
        self.assertEqual(self.client.get("/app/images").status_code, 502)
        self.assertEqual(self.backend_requests, [])



class HiddenPinTests(GatewayTestCase):
    """Hidden images wait for the PIN that Luna Sentia checks."""

    FILE_ROUTES = (
            "/app/thumbnails/{name}",
            "/app/files/{name}",
    )

    def expire_unlock(self):
        self.gateway.pin.unlocks = {key: (0.0, 0.0) for key in self.gateway.pin.unlocks}

    def test_the_hidden_tab_waits_for_the_right_pin(self):
        self.sign_in()
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)
        status = self.client.get("/app/hidden/pin").json()
        self.assertEqual((status["pin_set"], status["unlocked"]), (True, False))
        self.assertEqual(status["settings_url"], "https://auth.example.com/pin?next=https%3A%2F%2Fvisia.example.com%2F")
        wrong = self.unlock("111111")
        self.assertEqual((wrong.status_code, wrong.json()["attempts_left"]), (403, 4))
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)
        self.assertEqual(self.unlock().json(), {"unlocked": True})
        self.assertEqual(self.client.get("/app/hidden").status_code, 200)
        self.assertTrue(self.client.get("/app/hidden/pin").json()["unlocked"])

    def test_locking_again_and_going_idle_close_it(self):
        self.sign_in()
        self.unlock()
        self.client.post("/app/hidden/lock")
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)
        self.unlock()
        self.expire_unlock()
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)

    def test_the_pin_is_checked_by_sentia_with_this_session(self):
        self.sign_in()
        self.unlock()
        verify = [request for request in self.sso_requests if request.url.path == "/pin/verify"][-1]
        self.assertEqual(verify.headers["X-Luna-Session"], "owner")
        self.assertEqual(str(verify.url), "http://luna-sentia-auth:8000/pin/verify")

    def test_unlocking_lasts_only_for_this_session(self):
        self.sign_in()
        self.unlock()
        self.sign_in("owner-phone")
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)

    def test_without_a_pin_the_tab_asks_for_one_to_be_set(self):
        self.pin_set = False
        self.sign_in()
        status = self.client.get("/app/hidden/pin").json()
        self.assertEqual((status["pin_set"], status["unlocked"]), (False, False))
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)

    def test_sentia_being_away_is_reported_and_unlocks_nothing(self):
        self.sentia_up = False
        self.sign_in()
        self.assertEqual(self.unlock().status_code, 409)
        self.assertEqual(self.client.get("/app/hidden").status_code, 423)

    def test_hidden_files_wait_for_the_pin_and_are_never_cached(self):
        self.hidden = {"a.png"}
        self.sign_in()
        for route in self.FILE_ROUTES:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route.format(name="a.png")).status_code, 404)
                self.assertNotEqual(self.client.get(route.format(name="b b.png")).status_code, 404)
        self.unlock()
        for route in self.FILE_ROUTES:
            with self.subTest(route=route, unlocked=True):
                answer = self.client.get(route.format(name="a.png"))
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.headers["Cache-Control"], "private, no-store")

    def test_hiding_an_item_locks_its_files_straight_away(self):
        self.sign_in()
        route = self.FILE_ROUTES[-1].format(name="a.png")
        self.assertEqual(self.client.get(route).status_code, 200)
        self.hidden = {"a.png"}
        self.assertEqual(self.client.post("/api/hidden/hide", json={"names": ["a.png"]}).status_code, 200)
        self.assertEqual(self.client.get(route).status_code, 404)

    def test_changing_hidden_items_waits_for_the_pin_but_hiding_does_not(self):
        self.sign_in()
        self.assertEqual(self.client.post("/api/hidden/restore", json={"names": ["a.png"]}).status_code, 423)
        self.assertEqual(self.client.post("/api/hidden/delete", json={"names": ["a.png"]}).status_code, 423)
        self.assertEqual(self.client.post("/api/hidden/hide", json={"names": ["a.png"]}).status_code, 200)
        self.unlock()
        self.assertEqual(self.client.post("/api/hidden/restore", json={"names": ["a.png"]}).status_code, 200)

    def test_files_are_refused_when_hidden_items_cannot_be_checked(self):
        self.sign_in()
        self.gateway.pin.forget_names("owner")
        original = self.gateway.backend._transport
        self.gateway.backend._transport = httpx.MockTransport(lambda request: httpx.Response(503))
        self.addCleanup(setattr, self.gateway.backend, "_transport", original)
        self.assertEqual(self.client.get(self.FILE_ROUTES[-1].format(name="b b.png")).status_code, 502)


if __name__ == "__main__":
    unittest.main()
