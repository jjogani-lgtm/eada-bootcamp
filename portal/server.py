#!/usr/bin/env python3
"""Local Romulus marketplace server. Uses only the Python standard library."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


PORTAL_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ROMULUS_DB_PATH", PORTAL_DIR / "romulus.sqlite3"))
# Render and similar hosts provide PORT and require a public interface bind.
# Keep the convenient localhost defaults for running the app on a laptop.
HOST = os.environ.get("ROMULUS_HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("ROMULUS_PORT", "8000")))
SESSION_SECONDS = 14 * 24 * 60 * 60
COOKIE_NAME = "romulus_session"
COOKIE_SECURE = os.environ.get("ROMULUS_COOKIE_SECURE") == "1"

NEIGHBORHOODS = {
    "Blmngtn": "Bloomington Heights", "Blueste": "Bluestem", "BrDale": "Briardale",
    "BrkSide": "Brookside", "ClearCr": "Clear Creek", "CollgCr": "College Creek",
    "Crawfor": "Crawford", "Edwards": "Edwards", "Gilbert": "Gilbert", "Greens": "Greens",
    "IDOTRR": "Iowa DOT & Rail", "MeadowV": "Meadow Village", "Mitchel": "Mitchell",
    "NAmes": "North Ames", "NPkVill": "Northpark Villa", "NWAmes": "Northwest Ames",
    "NoRidge": "Northridge", "NridgHt": "Northridge Heights", "OldTown": "Old Town",
    "SWISU": "South & West of ISU", "Sawyer": "Sawyer", "SawyerW": "Sawyer West",
    "Somerst": "Somerset", "StoneBr": "Stone Brook", "Timber": "Timberland", "Veenker": "Veenker",
}
PROPERTY_TYPES = {"house", "apartment", "condo", "townhouse", "duplex", "two-family home"}


def connect_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                property_type TEXT NOT NULL,
                address TEXT NOT NULL,
                neighborhood TEXT NOT NULL,
                price INTEGER NOT NULL,
                bedrooms INTEGER NOT NULL,
                bathrooms REAL NOT NULL,
                square_feet INTEGER NOT NULL,
                contact_phone TEXT NOT NULL DEFAULT '',
                share_phone INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS listings_recent ON listings(created_at DESC);
            CREATE INDEX IF NOT EXISTS messages_recipient ON messages(recipient_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS messages_sender ON messages(sender_id, created_at DESC);
            DELETE FROM sessions WHERE expires_at < strftime('%s','now');
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


class RomulusHandler(SimpleHTTPRequestHandler):
    server_version = "RomulusLocal/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PORTAL_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def send_json(self, payload: dict, status: int = 200, extra_headers: tuple[str, ...] = ()) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for header in extra_headers:
            self.send_header(*header.split(":", 1))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Could not read the request.") from exc
        if length <= 0 or length > 32_000:
            raise ValueError("The form is empty or too large.")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Please submit the form again.")
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Some form details could not be read.") from exc
        if not isinstance(data, dict):
            raise ValueError("Some form details could not be read.")
        return data

    def require_same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        expected = f"http://{self.headers.get('Host', '')}"
        if COOKIE_SECURE:
            expected = f"https://{self.headers.get('Host', '')}"
        if not origin or origin.rstrip("/") != expected.rstrip("/"):
            self.send_json({"error": "Please reload Romulus and try again."}, 403)
            return False
        return True

    def cookie_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else None
        except Exception:
            return None

    def current_user(self, db: sqlite3.Connection) -> sqlite3.Row | None:
        token = self.cookie_token()
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        return db.execute(
            "SELECT users.id, users.email FROM sessions JOIN users ON users.id=sessions.user_id "
            "WHERE sessions.token_hash=? AND sessions.expires_at>?",
            (token_hash, time.time()),
        ).fetchone()

    def set_session(self, db: sqlite3.Connection, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                   (token_hash, user_id, time.time() + SESSION_SECONDS))
        flags = "Path=/; HttpOnly; SameSite=Lax; Max-Age=1209600"
        if COOKIE_SECURE:
            flags += "; Secure"
        return f"{COOKIE_NAME}={token}; {flags}"

    def api_get(self, path: str) -> None:
        with connect_db() as db:
            if path == "/api/me":
                user = self.current_user(db)
                self.send_json({"user": dict(user) if user else None})
                return
            if path == "/api/listings":
                rows = db.execute("""
                    SELECT id,title,description,property_type,address,neighborhood,price,bedrooms,bathrooms,
                           square_feet,CASE WHEN share_phone=1 THEN contact_phone ELSE '' END AS contact_phone,
                           share_phone,created_at
                    FROM listings ORDER BY created_at DESC LIMIT 100
                """).fetchall()
                self.send_json({"listings": [dict(row) for row in rows]})
                return
            if path == "/api/messages":
                user = self.current_user(db)
                if not user:
                    self.send_json({"error": "Sign in to view your messages."}, 401)
                    return
                received = db.execute("""
                    SELECT messages.id,messages.body,messages.created_at,listings.id AS listing_id,
                           listings.title AS listing_title,users.email AS sender_email
                    FROM messages JOIN listings ON listings.id=messages.listing_id
                    JOIN users ON users.id=messages.sender_id
                    WHERE messages.recipient_id=? ORDER BY messages.created_at DESC LIMIT 100
                """, (user["id"],)).fetchall()
                sent = db.execute("""
                    SELECT messages.id,messages.body,messages.created_at,listings.id AS listing_id,
                           listings.title AS listing_title,users.email AS seller_email
                    FROM messages JOIN listings ON listings.id=messages.listing_id
                    JOIN users ON users.id=messages.recipient_id
                    WHERE messages.sender_id=? ORDER BY messages.created_at DESC LIMIT 100
                """, (user["id"],)).fetchall()
                self.send_json({"received": [dict(row) for row in received], "sent": [dict(row) for row in sent]})
                return
        self.send_json({"error": "That page could not be found."}, 404)

    def api_post(self, path: str) -> None:
        if not self.require_same_origin():
            return
        try:
            data = self.read_json()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return

        if path == "/api/register":
            email = str(data.get("email", "")).strip().lower()
            password = data.get("password", "")
            if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                self.send_json({"error": "Enter an email address in the usual format."}, 400)
                return
            if not isinstance(password, str) or not 8 <= len(password) <= 128:
                self.send_json({"error": "Choose a password with at least 8 characters."}, 400)
                return
            with connect_db() as db:
                try:
                    cursor = db.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",
                                        (email, password_hash(password), now_iso()))
                except sqlite3.IntegrityError:
                    self.send_json({"error": "An account with that email already exists. Sign in instead."}, 409)
                    return
                user_id = cursor.lastrowid
                cookie = self.set_session(db, user_id)
            self.send_json({"user": {"id": user_id, "email": email}}, 201, (f"Set-Cookie: {cookie}",))
            return

        if path == "/api/login":
            email = str(data.get("email", "")).strip().lower()
            password = data.get("password", "")
            if not isinstance(password, str) or len(password) > 128:
                password = ""
            with connect_db() as db:
                row = db.execute("SELECT id,email,password_hash FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
                if not row or not password_matches(password, row["password_hash"]):
                    self.send_json({"error": "That email and password do not match an account."}, 401)
                    return
                cookie = self.set_session(db, row["id"])
            self.send_json({"user": {"id": row["id"], "email": row["email"]}}, 200, (f"Set-Cookie: {cookie}",))
            return

        if path == "/api/logout":
            token = self.cookie_token()
            with connect_db() as db:
                if token:
                    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            expired = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" + ("; Secure" if COOKIE_SECURE else "")
            self.send_json({"ok": True}, extra_headers=(f"Set-Cookie: {expired}",))
            return

        if path == "/api/listings":
            with connect_db() as db:
                user = self.current_user(db)
                if not user:
                    self.send_json({"error": "Create an account or sign in to list a home."}, 401)
                    return
                try:
                    title = str(data.get("title", "")).strip()
                    description = str(data.get("description", "")).strip()
                    property_type = str(data.get("property_type", "")).strip()
                    address = str(data.get("address", "")).strip()
                    neighborhood = str(data.get("neighborhood", "")).strip()
                    price = int(data.get("price", 0))
                    bedrooms = int(data.get("bedrooms", -1))
                    bathrooms = float(data.get("bathrooms", 0))
                    square_feet = int(data.get("square_feet", 0))
                    phone = str(data.get("contact_phone", "")).strip()
                    share_phone = bool(data.get("share_phone", False))
                except (TypeError, ValueError, OverflowError):
                    self.send_json({"error": "Check the home details and try again."}, 400)
                    return
                if not 8 <= len(title) <= 90 or not 20 <= len(description) <= 2000:
                    self.send_json({"error": "Add a short title and a description of at least 20 characters."}, 400)
                    return
                if property_type not in PROPERTY_TYPES or neighborhood not in NEIGHBORHOODS:
                    self.send_json({"error": "Choose a home type and Ames neighborhood from the menus."}, 400)
                    return
                if not 5 <= len(address) <= 160 or not 1_000 <= price <= 20_000_000:
                    self.send_json({"error": "Enter an address and a price between $1,000 and $20,000,000."}, 400)
                    return
                if not 0 <= bedrooms <= 20 or not 0.5 <= bathrooms <= 20 or not 100 <= square_feet <= 50_000:
                    self.send_json({"error": "Check the bedroom, bathroom, and square footage values."}, 400)
                    return
                if len(phone) > 40 or (share_phone and not phone):
                    self.send_json({"error": "Add a phone number or turn off public phone sharing."}, 400)
                    return
                cursor = db.execute("""
                    INSERT INTO listings(user_id,title,description,property_type,address,neighborhood,price,bedrooms,
                                         bathrooms,square_feet,contact_phone,share_phone,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (user["id"], title, description, property_type, address, neighborhood, price, bedrooms,
                      bathrooms, square_feet, phone if share_phone else "", int(share_phone), now_iso()))
                listing_id = cursor.lastrowid
            self.send_json({"ok": True, "listing_id": listing_id}, 201)
            return

        if path == "/api/messages":
            try:
                listing_id = int(data.get("listing_id", 0))
                body = str(data.get("body", "")).strip()
            except (TypeError, ValueError):
                self.send_json({"error": "Choose a listing and enter a message."}, 400)
                return
            if not 10 <= len(body) <= 2000:
                self.send_json({"error": "Write a message between 10 and 2,000 characters."}, 400)
                return
            with connect_db() as db:
                user = self.current_user(db)
                if not user:
                    self.send_json({"error": "Register or sign in to contact a seller."}, 401)
                    return
                listing = db.execute("SELECT user_id FROM listings WHERE id=?", (listing_id,)).fetchone()
                if not listing:
                    self.send_json({"error": "That listing is no longer available."}, 404)
                    return
                if listing["user_id"] == user["id"]:
                    self.send_json({"error": "You can’t contact yourself about your own listing."}, 400)
                    return
                db.execute("INSERT INTO messages(listing_id,sender_id,recipient_id,body,created_at) VALUES(?,?,?,?,?)",
                           (listing_id, user["id"], listing["user_id"], body, now_iso()))
            self.send_json({"ok": True}, 201)
            return

        self.send_json({"error": "That page could not be found."}, 404)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.api_get(path)
        elif path in ("/", "/index.html", "/model.json"):
            super().do_GET()
        else:
            self.send_json({"error": "That page could not be found."}, 404)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/index.html", "/model.json"):
            super().do_HEAD()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.api_post(path)
        else:
            self.send_json({"error": "That page could not be found."}, 404)


if __name__ == "__main__":
    initialize_db()
    server = ThreadingHTTPServer((HOST, PORT), RomulusHandler)
    print(f"Romulus is ready at http://{HOST}:{PORT}")
    print(f"SQLite database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRomulus server stopped.")
    finally:
        server.server_close()
