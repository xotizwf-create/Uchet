from __future__ import annotations

from pathlib import Path
import datetime as dt
import hmac
import hashlib
import json
import os
import secrets
import smtplib
from email.message import EmailMessage

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from sqlalchemy import desc, select

from backend.db import SessionLocal, commit_with_retry, init_db
from backend.services import archive, commercials, contracts, dashboard, pricelist, warehouse
from backend.services.storage import get_user_storage_dir, normalize_user_id
from backend.models import OTPChallenge, Profile, TelegramPending, TrustedDevice, User


def _load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"Failed to load {path}: {exc}")


def create_app() -> Flask:

    app = Flask(__name__)
    _load_env_file()
    app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(16))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SEND_FILE_MAX_AGE_DEFAULT=0,
        TEMPLATES_AUTO_RELOAD=True,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=1),
    )
    app.jinja_env.auto_reload = True

    login_manager = LoginManager()
    login_manager.init_app(app)

    init_db()

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        db_session = SessionLocal()
        try:
            return db_session.get(User, int(user_id))
        finally:
            db_session.close()

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        target = request.path
        return redirect(url_for("landing", auth="login", next=target))

    def _now() -> dt.datetime:
        return dt.datetime.utcnow()

    def _hash_otp(code: str, salt: str) -> str:
        secret = app.secret_key or "dev-secret"
        digest = hmac.new(secret.encode("utf-8"), f"{salt}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{salt}${digest}"

    def _verify_otp_hash(code: str, stored_hash: str) -> bool:
        try:
            salt, digest = stored_hash.split("$", 1)
        except ValueError:
            return False
        candidate = _hash_otp(code, salt).split("$", 1)[1]
        return hmac.compare_digest(candidate, digest)

    def _is_fetch_request() -> bool:
        return (
            request.headers.get("X-Requested-With") == "fetch"
            or "application/json" in (request.headers.get("Accept", "") or "")
            or request.headers.get("Sec-Fetch-Mode") == "cors"
        )

    TRUSTED_DEVICE_COOKIE = "gd_trusted_devices"
    TRUSTED_DEVICE_TTL = dt.timedelta(days=1)

    def _mask_email(email: str) -> str:
        if "@" not in email:
            return email
        name, domain = email.split("@", 1)
        masked = f"{name[:2]}***" if len(name) > 2 else f"{name}***"
        return f"{masked}@{domain}"

    def _generate_otp_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def _hash_device_token(token: str) -> str:
        secret = app.secret_key or "dev-secret"
        digest = hmac.new(secret.encode("utf-8"), f"device:{token}".encode("utf-8"), hashlib.sha256).hexdigest()
        return digest

    def _load_trusted_device_map() -> dict[str, str]:
        raw = request.cookies.get(TRUSTED_DEVICE_COOKIE, "") or ""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        cleaned: dict[str, str] = {}
        for key, value in data.items():
            key_str = str(key)
            if not key_str.isdigit():
                continue
            if not isinstance(value, str) or not value:
                continue
            cleaned[str(int(key_str))] = value
        return cleaned

    def _set_trusted_device_cookie(response, device_map: dict[str, str]) -> None:
        payload = json.dumps(device_map, separators=(",", ":"), ensure_ascii=True)
        response.set_cookie(
            TRUSTED_DEVICE_COOKIE,
            payload,
            max_age=int(TRUSTED_DEVICE_TTL.total_seconds()),
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
        )

    def _get_device_token_for_user(device_map: dict[str, str], user_id: int) -> str:
        return device_map.get(str(user_id), "") or ""

    def _ensure_trusted_device(
        db_session, user: User, token: str | None
    ) -> tuple[str, dt.datetime]:
        token = (token or "").strip()
        now = _now()
        expires_at = now + TRUSTED_DEVICE_TTL
        attempts = 0
        while attempts < 3:
            if not token:
                token = secrets.token_urlsafe(32)
            token_hash = _hash_device_token(token)
            existing = (
                db_session.execute(
                    select(TrustedDevice).where(TrustedDevice.token_hash == token_hash)
                )
                .scalars()
                .first()
            )
            if existing and existing.user_id != user.id:
                token = ""
                attempts += 1
                continue
            device = (
                db_session.execute(
                    select(TrustedDevice).where(
                        TrustedDevice.user_id == user.id,
                        TrustedDevice.token_hash == token_hash,
                    )
                )
                .scalars()
                .first()
            )
            if not device:
                device = TrustedDevice(
                    user_id=user.id,
                    token_hash=token_hash,
                    created_at=now,
                )
                db_session.add(device)
            device.last_used_at = now
            device.expires_at = expires_at
            device.user_agent = request.headers.get("User-Agent")
            device.ip_address = request.remote_addr
            return token, expires_at
        token = secrets.token_urlsafe(32)
        token_hash = _hash_device_token(token)
        device = TrustedDevice(
            user_id=user.id,
            token_hash=token_hash,
            created_at=now,
            last_used_at=now,
            expires_at=expires_at,
            user_agent=request.headers.get("User-Agent"),
            ip_address=request.remote_addr,
        )
        db_session.add(device)
        return token, expires_at

    def _find_trusted_device(db_session, user_id: int, token: str) -> TrustedDevice | None:
        token = (token or "").strip()
        if not token:
            return None
        token_hash = _hash_device_token(token)
        return (
            db_session.execute(
                select(TrustedDevice).where(
                    TrustedDevice.user_id == user_id,
                    TrustedDevice.token_hash == token_hash,
                    TrustedDevice.expires_at >= _now(),
                )
            )
            .scalars()
            .first()
        )

    OTP_STORAGE_DIR = Path("instance/otp_codes")

    def _otp_storage_path(user_id: int) -> Path:
        return OTP_STORAGE_DIR / f"{user_id}.json"

    def _write_otp_file(user_id: int, code_hash: str, expires_at: dt.datetime, challenge_id: int) -> None:
        OTP_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "code_hash": code_hash,
            "expires_at": expires_at.isoformat(),
            "challenge_id": challenge_id,
        }
        _otp_storage_path(user_id).write_text(json.dumps(payload), encoding="utf-8")

    def _read_otp_file(user_id: int) -> dict | None:
        path = _otp_storage_path(user_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            path.unlink(missing_ok=True)
            return None

    def _clear_otp_file(user_id: int) -> None:
        _otp_storage_path(user_id).unlink(missing_ok=True)

    def _ensure_admin_flag(db_session, user: User) -> None:
        admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
        if admin_email and user.email and user.email.lower() == admin_email and not user.is_admin:
            user.is_admin = True
            commit_with_retry(db_session)

    def _require_admin() -> bool:
        if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
            flash("Admin access required.", "error")
            return False
        return True

    def _send_email_otp(recipient: str, code: str, purpose: str) -> tuple[bool, str | None]:
        smtp_host = os.getenv("SMTP_HOST")
        if not smtp_host:
            app.logger.info("SMTP not configured. OTP for %s: %s", recipient, code)
            return False, "SMTP is not configured. Set SMTP_HOST/SMTP_USER/SMTP_PASSWORD."
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")
        if smtp_password:
            smtp_password = "".join(smtp_password.split())
        smtp_use_tls = os.getenv("SMTP_USE_TLS", "1") != "0"
        sender = os.getenv("SMTP_FROM", smtp_user or "no-reply@example.com")

        message = EmailMessage()
        message["Subject"] = f"Your OTP code for {purpose}"
        message["From"] = sender
        message["To"] = recipient
        message.set_content(f"Your OTP code: {code}\nIt expires in 3 minutes.")

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
                if smtp_use_tls:
                    smtp.starttls()
                if smtp_user:
                    smtp.login(smtp_user, smtp_password)
                smtp.send_message(message)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Failed to send email OTP: %s", exc)
            return False, "Failed to send OTP email. Check SMTP settings."
        return True, None

    def _send_telegram_message(chat_id: str, text: str) -> tuple[bool, str | None]:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            app.logger.info("Telegram bot token not configured. OTP for %s: %s", chat_id, text)
            return False, "Telegram bot token is not configured."
        import requests

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Failed to send Telegram OTP: %s", exc)
            return False, "Failed to send Telegram message."
        if not response.ok:
            app.logger.warning("Telegram API error: %s", response.text)
            return False, "Telegram API error. Try again later."
        return True, None

    def _rate_limit_otp(db_session, user_id: int, channel: str, purpose: str) -> str | None:
        now = _now()
        recent = (
            db_session.execute(
                select(OTPChallenge)
                .where(
                    OTPChallenge.user_id == user_id,
                    OTPChallenge.channel == channel,
                    OTPChallenge.purpose == purpose,
                )
                .order_by(desc(OTPChallenge.created_at))
                .limit(20)
            )
            .scalars()
            .all()
        )
        last_minute = [c for c in recent if (now - c.created_at).total_seconds() < 60]
        if len(last_minute) >= 3:
            return "OTP was sent too often. Please wait a minute."
        last_hour = [c for c in recent if (now - c.created_at).total_seconds() < 3600]
        if len(last_hour) >= 20:
            return "OTP request limit exceeded. Try again later."
        return None

    def _create_otp_challenge(
        db_session, user_id: int, channel: str, purpose: str
    ) -> tuple[OTPChallenge | None, str | None]:
        rate_error = _rate_limit_otp(db_session, user_id, channel, purpose)
        if rate_error:
            return None, rate_error
        code = _generate_otp_code()
        salt = secrets.token_urlsafe(8)
        challenge = OTPChallenge(
            user_id=user_id,
            channel=channel,
            purpose=purpose,
            code_hash=_hash_otp(code, salt),
            expires_at=_now() + dt.timedelta(minutes=3),
            created_at=_now(),
        )
        db_session.add(challenge)
        commit_with_retry(db_session)
        if channel == "email":
            user = db_session.get(User, user_id)
            if user and user.email:
                sent, send_error = _send_email_otp(user.email, code, purpose)
                if not sent:
                    db_session.delete(challenge)
                    commit_with_retry(db_session)
                    _clear_otp_file(user_id)
                    return None, send_error
            else:
                db_session.delete(challenge)
                commit_with_retry(db_session)
                _clear_otp_file(user_id)
                return None, "Email address is missing."
        elif channel == "telegram":
            user = db_session.get(User, user_id)
            if user and user.telegram_chat_id:
                sent, send_error = _send_telegram_message(user.telegram_chat_id, f"OTP code: {code}")
                if not sent:
                    db_session.delete(challenge)
                    commit_with_retry(db_session)
                    _clear_otp_file(user_id)
                    return None, send_error
            else:
                db_session.delete(challenge)
                commit_with_retry(db_session)
                _clear_otp_file(user_id)
                return None, "Telegram chat is not linked yet."
        _write_otp_file(user_id, challenge.code_hash, challenge.expires_at, challenge.id)
        return challenge, None

    def _consume_otp(db_session, challenge: OTPChallenge, code: str) -> bool:
        if challenge.consumed_at is not None:
            return False
        if challenge.expires_at and challenge.expires_at < _now():
            _clear_otp_file(challenge.user_id)
            return False
        if challenge.attempts >= 5:
            return False
        if not challenge.code_hash or not _verify_otp_hash(code, challenge.code_hash):
            challenge.attempts += 1
            commit_with_retry(db_session)
            return False
        challenge.consumed_at = _now()
        commit_with_retry(db_session)
        _clear_otp_file(challenge.user_id)
        return True

    @app.route("/")
    def landing():
        return render_template("public/landing.html")

    @app.post("/api/appBackend")
    def app_backend():
        if not current_user.is_authenticated:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        db_session = SessionLocal()
        try:
            user = db_session.get(User, current_user.id)
            if not user:
                return jsonify({"success": False, "error": "No user found in database"}), 404
            
            data = request.get_json(silent=True) or {}
            module = data.get("module")
            action = data.get("action")
            payload = data.get("payload") or {}
            payload["userId"] = user.id
            
            handlers = {
                "contracts": contracts,
                "warehouse": warehouse,
                "pricelist": pricelist,
                "dashboard": dashboard,
                "commercials": commercials,
                "archive": archive,
            }

            handler = handlers.get(module)
            if not handler:
                return jsonify({"success": False, "error": f"Unknown module: {module}"})

            return jsonify(handler.handle(action, payload))
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)})
        finally:
            db_session.close()

    @app.route("/drive/<file_id>")
    @login_required
    def download_drive_file(file_id: str):
        from backend.db import SessionLocal
        from backend.models import DriveFile

        user_id = normalize_user_id(str(current_user.id))
        session = SessionLocal()
        try:
            file = session.execute(select(DriveFile).where(DriveFile.id == file_id)).scalar_one_or_none()
            if not file or file.user_id != user_id:
                return ("Not found", 404)
            drive_dir = get_user_storage_dir(file.user_id)
            return send_from_directory(drive_dir, file.storage_name, as_attachment=True, download_name=file.name)
        finally:
            session.close()

    @app.route("/archive/<filename>")
    def download_archive(filename: str):
        archive_dir = Path("instance/archives")
        return send_from_directory(archive_dir, filename, as_attachment=True)

    @app.route("/app")
    @login_required
    def app_index():
        return render_template("index.html")

    @app.route("/logout")
    def logout():
        logout_user()
        return redirect(url_for("landing"))

    @app.route("/auth")
    def auth_root():
        return redirect(url_for("landing", auth="register"))

    @app.route("/auth/register")
    def auth_register():
        return redirect(url_for("landing", auth="register"))

    @app.route("/auth/login")
    def auth_login():
        return redirect(url_for("landing", auth="login"))

    @app.post("/auth/password/request")
    def password_request():
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            return jsonify({"success": False, "error": "Email обязателен."}), 400
        db_session = SessionLocal()
        try:
            user = db_session.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                return jsonify({"success": False, "error": "Аккаунт не найден."}), 404
            challenge, error = _create_otp_challenge(db_session, user.id, "email", "reset")
            if not challenge:
                return jsonify({"success": False, "error": error or "Failed to send OTP."}), 400
            session["reset_challenge_id"] = challenge.id
            session["reset_user_id"] = user.id
            session["reset_sent_at"] = _now().timestamp()
            session["reset_verified"] = False
            return jsonify({"success": True, "masked_email": _mask_email(user.email or email)})
        finally:
            db_session.close()

    @app.post("/auth/password/verify")
    def password_verify():
        code = (request.form.get("code") or "").strip()
        challenge_id = session.get("reset_challenge_id")
        user_id = session.get("reset_user_id")
        if not challenge_id or not user_id:
            return jsonify({"success": False, "error": "Запрос на восстановление не найден."}), 400
        db_session = SessionLocal()
        try:
            challenge = db_session.get(OTPChallenge, challenge_id)
            if not challenge or challenge.user_id != user_id:
                return jsonify({"success": False, "error": "OTP challenge not found."}), 400
            if not _consume_otp(db_session, challenge, code):
                return jsonify({"success": False, "error": "Invalid or expired code."}), 400
            session["reset_verified"] = True
            return jsonify({"success": True})
        finally:
            db_session.close()

    @app.post("/auth/password/reset")
    def password_reset():
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        user_id = session.get("reset_user_id")
        if not user_id or not session.get("reset_verified"):
            return jsonify({"success": False, "error": "Сначала подтвердите код."}), 400
        if not password:
            return jsonify({"success": False, "error": "Пароль обязателен."}), 400
        if password != confirm:
            return jsonify({"success": False, "error": "Пароли не совпадают."}), 400
        db_session = SessionLocal()
        try:
            user = db_session.get(User, user_id)
            if not user:
                return jsonify({"success": False, "error": "User not found."}), 404
            user.set_password(password)
            commit_with_retry(db_session)
            session.pop("reset_challenge_id", None)
            session.pop("reset_user_id", None)
            session.pop("reset_verified", None)
            session.pop("reset_sent_at", None)
            return jsonify({"success": True})
        finally:
            db_session.close()

    @app.get("/auth/accounts")
    def auth_accounts():
        device_map = _load_trusted_device_map()
        if not device_map:
            return jsonify({"accounts": []})
        db_session = SessionLocal()
        try:
            accounts = []
            for user_id_str, token in device_map.items():
                try:
                    user_id = int(user_id_str)
                except ValueError:
                    continue
                trusted_device = _find_trusted_device(db_session, user_id, token)
                if not trusted_device:
                    continue
                user = db_session.get(User, user_id)
                if not user:
                    continue
                profile = user.profile
                name = ""
                if profile and profile.full_name:
                    name = profile.full_name.strip()
                if not name:
                    name = user.email or f"User #{user.id}"
                accounts.append(
                    {
                        "id": user.id,
                        "email": user.email or "",
                        "name": name,
                    }
                )
            return jsonify({"accounts": accounts})
        finally:
            db_session.close()

    @app.post("/auth/switch/<int:user_id>")
    def auth_switch(user_id: int):
        device_map = _load_trusted_device_map()
        token = _get_device_token_for_user(device_map, user_id)
        if not token:
            return jsonify({"success": False, "error": "Account not trusted on this device."}), 403
        db_session = SessionLocal()
        try:
            trusted_device = _find_trusted_device(db_session, user_id, token)
            if not trusted_device:
                return jsonify({"success": False, "error": "Account not trusted on this device."}), 403
            user = db_session.get(User, user_id)
            if not user:
                return jsonify({"success": False, "error": "User not found."}), 404
            token, _ = _ensure_trusted_device(db_session, user, token)
            device_map[str(user.id)] = token
            commit_with_retry(db_session)
            login_user(user)
            session.permanent = True
            if _is_fetch_request():
                response = jsonify({"success": True, "redirect": url_for("app_index")})
            else:
                response = redirect(url_for("app_index"))
            _set_trusted_device_cookie(response, device_map)
            return response
        finally:
            db_session.close()

    @app.post("/auth/accounts/<int:user_id>/remove")
    def auth_remove_account(user_id: int):
        device_map = _load_trusted_device_map()
        token = _get_device_token_for_user(device_map, user_id)
        if not token:
            return jsonify({"success": True})
        db_session = SessionLocal()
        try:
            token_hash = _hash_device_token(token)
            db_session.execute(
                TrustedDevice.__table__.delete().where(
                    TrustedDevice.user_id == user_id,
                    TrustedDevice.token_hash == token_hash,
                )
            )
            device_map.pop(str(user_id), None)
            commit_with_retry(db_session)
            response = jsonify({"success": True})
            _set_trusted_device_cookie(response, device_map)
            return response
        finally:
            db_session.close()

    @app.route("/auth/register/email", methods=["GET", "POST"])
    def register_email():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            is_fetch = _is_fetch_request()
            if not email or not password:
                if is_fetch:
                    return jsonify({"success": False, "error": "Email and password are required."}), 400
                flash("Email and password are required.", "error")
                return redirect(url_for("register_email"))
            db_session = SessionLocal()
            try:
                existing = db_session.execute(select(User).where(User.email == email)).scalar_one_or_none()
                if existing:
                    if existing.is_email_verified:
                        if is_fetch:
                            return jsonify({"success": False, "error": "Email already registered."}), 409
                        flash("Email already registered. Please login.", "error")
                        return redirect(url_for("login_email"))
                    if existing.password_hash and not existing.check_password(password):
                        if is_fetch:
                            return jsonify({"success": False, "error": "Email already registered."}), 409
                        flash("Email already registered. Please login.", "error")
                        return redirect(url_for("login_email"))
                    existing.set_password(password)
                    commit_with_retry(db_session)
                    _ensure_admin_flag(db_session, existing)
                    challenge, error = _create_otp_challenge(db_session, existing.id, "email", "signup")
                    if not challenge:
                        if is_fetch:
                            return jsonify({"success": False, "error": error or "Failed to send OTP."}), 400
                        flash(error or "Failed to send OTP.", "error")
                        return redirect(url_for("register_email"))
                    session["otp_challenge_id"] = challenge.id
                    session["otp_channel"] = "email"
                    session["otp_purpose"] = "signup"
                    if is_fetch:
                        return jsonify(
                            {"success": True, "next": "otp", "masked_email": _mask_email(existing.email or email)}
                        )
                    return redirect(url_for("verify_otp"))
                user = User(email=email)
                user.set_password(password)
                profile = Profile(user=user)
                db_session.add_all([user, profile])
                commit_with_retry(db_session)
                _ensure_admin_flag(db_session, user)
                challenge, error = _create_otp_challenge(db_session, user.id, "email", "signup")
                if not challenge:
                    if is_fetch:
                        return jsonify({"success": False, "error": error or "Failed to send OTP."}), 400
                    flash(error or "Failed to send OTP.", "error")
                    return redirect(url_for("register_email"))
                session["otp_challenge_id"] = challenge.id
                session["otp_channel"] = "email"
                session["otp_purpose"] = "signup"
                session["otp_sent_at"] = _now().timestamp()
                if is_fetch:
                    return jsonify({"success": True, "next": "otp", "masked_email": _mask_email(user.email or email)})
                flash("Код успешно отправлен!", "success")
                return redirect(url_for("verify_otp"))
            finally:
                db_session.close()
        return render_template("auth/register_email.html")

    @app.route("/auth/login/email", methods=["GET", "POST"])
    def login_email():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            is_fetch = _is_fetch_request()
            db_session = SessionLocal()
            try:
                user = db_session.execute(select(User).where(User.email == email)).scalar_one_or_none()
                if not user:
                    if is_fetch:
                        return jsonify({"success": False, "error": "Аккаунт не найден."}), 404
                    flash("Аккаунт не найден.", "error")
                    return redirect(url_for("login_email"))
                if not user.check_password(password):
                    if is_fetch:
                        return jsonify({"success": False, "error": "Неверный пароль."}), 401
                    flash("Неверный пароль.", "error")
                    return redirect(url_for("login_email"))
                _ensure_admin_flag(db_session, user)
                device_map = _load_trusted_device_map()
                device_token = _get_device_token_for_user(device_map, user.id)
                trusted_device = _find_trusted_device(db_session, user.id, device_token)
                if trusted_device:
                    token, _ = _ensure_trusted_device(db_session, user, device_token)
                    device_map[str(user.id)] = token
                    commit_with_retry(db_session)
                    login_user(user)
                    session.permanent = True
                    if is_fetch:
                        response = jsonify({"success": True, "redirect": url_for("app_index")})
                    else:
                        response = redirect(url_for("app_index"))
                    _set_trusted_device_cookie(response, device_map)
                    return response
                challenge, error = _create_otp_challenge(db_session, user.id, "email", "login")
                if not challenge:
                    if is_fetch:
                        return jsonify({"success": False, "error": error or "Failed to send OTP."}), 400
                    flash(error or "Failed to send OTP.", "error")
                    return redirect(url_for("login_email"))
                session["otp_challenge_id"] = challenge.id
                session["otp_channel"] = "email"
                session["otp_purpose"] = "login"
                session["otp_sent_at"] = _now().timestamp()
                if is_fetch:
                    return jsonify(
                        {"success": True, "next": "otp", "masked_email": _mask_email(user.email or email)}
                    )
                flash("Код успешно отправлен!", "success")
                return redirect(url_for("verify_otp"))
            finally:
                db_session.close()
        return render_template("auth/login_email.html")

    @app.route("/auth/register/telegram")
    def register_telegram():
        db_session = SessionLocal()
        try:
            token = secrets.token_urlsafe(24)
            pending = TelegramPending(
                token=token,
                purpose="signup",
                created_at=_now(),
            )
            db_session.add(pending)
            commit_with_retry(db_session)
            bot_username = os.getenv("TELEGRAM_BOT_USERNAME", "GoogleDeck_Bot")
            return render_template("auth/register_telegram.html", token=token, purpose="signup", bot_username=bot_username)
        finally:
            db_session.close()

    @app.route("/auth/login/telegram")
    def login_telegram():
        db_session = SessionLocal()
        try:
            token = secrets.token_urlsafe(24)
            pending = TelegramPending(
                token=token,
                purpose="login",
                created_at=_now(),
            )
            db_session.add(pending)
            commit_with_retry(db_session)
            bot_username = os.getenv("TELEGRAM_BOT_USERNAME", "GoogleDeck_Bot")
            return render_template("auth/register_telegram.html", token=token, purpose="login", bot_username=bot_username)
        finally:
            db_session.close()

    @app.route("/auth/telegram/status")
    def telegram_status():
        token = request.args.get("token")
        if not token:
            return jsonify({"linked": False, "expired": True})
        db_session = SessionLocal()
        try:
            pending = db_session.execute(select(TelegramPending).where(TelegramPending.token == token)).scalar_one_or_none()
            if not pending:
                return jsonify({"linked": False, "expired": True})
            expired = pending.created_at < (_now() - dt.timedelta(minutes=10))
            return jsonify({"linked": pending.consumed_at is not None, "expired": expired})
        finally:
            db_session.close()

    @app.route("/telegram/link", methods=["POST"])
    def telegram_link():
        payload = request.get_json(silent=True) or {}
        token = payload.get("token")
        telegram_user_id = str(payload.get("telegram_user_id") or "")
        chat_id = str(payload.get("chat_id") or "")
        if not token or not telegram_user_id or not chat_id:
            return jsonify({"success": False, "error": "invalid payload"}), 400
        db_session = SessionLocal()
        try:
            pending = (
                db_session.execute(select(TelegramPending).where(TelegramPending.token == token))
                .scalar_one_or_none()
            )
            if not pending:
                return jsonify({"success": False, "error": "invalid token"}), 404
            if pending.consumed_at is not None:
                return jsonify({"success": False, "error": "token consumed"}), 400
            if pending.created_at < (_now() - dt.timedelta(minutes=10)):
                return jsonify({"success": False, "error": "token expired"}), 400

            user = None
            if pending.user_id:
                user = db_session.get(User, pending.user_id)
            if not user:
                user = db_session.execute(
                    select(User).where(User.telegram_user_id == telegram_user_id)
                ).scalar_one_or_none()
            if not user:
                if pending.purpose == "login":
                    return jsonify({"success": False, "error": "account not found"}), 404
                user = User()
                profile = Profile(user=user)
                db_session.add_all([user, profile])
                commit_with_retry(db_session)

            user.telegram_user_id = telegram_user_id
            user.telegram_chat_id = chat_id
            pending.user_id = user.id
            pending.telegram_user_id = telegram_user_id
            pending.telegram_chat_id = chat_id
            pending.consumed_at = _now()
            commit_with_retry(db_session)

            challenge, error = _create_otp_challenge(db_session, user.id, "telegram", pending.purpose)
            if not challenge:
                status = 429 if (error or "").startswith("OTP") else 400
                return jsonify({"success": False, "error": error or "otp error"}), status

            return jsonify({"success": True})
        finally:
            db_session.close()

    @app.route("/auth/verify", methods=["GET", "POST"])
    def verify_otp():
        db_session = SessionLocal()
        try:
            challenge = None
            if "otp_challenge_id" in session:
                challenge = db_session.get(OTPChallenge, session.get("otp_challenge_id"))
            if not challenge:
                token = request.args.get("token")
                if token:
                    pending = (
                        db_session.execute(select(TelegramPending).where(TelegramPending.token == token))
                        .scalar_one_or_none()
                    )
                    if pending and pending.consumed_at is not None:
                        challenge = (
                            db_session.execute(
                                select(OTPChallenge)
                                .where(
                                    OTPChallenge.user_id == pending.user_id,
                                    OTPChallenge.channel == "telegram",
                                    OTPChallenge.purpose == pending.purpose,
                                )
                                .order_by(desc(OTPChallenge.created_at))
                            )
                            .scalars()
                            .first()
                        )
                        if challenge:
                            session["otp_challenge_id"] = challenge.id
                            session["otp_channel"] = "telegram"
                            session["otp_purpose"] = pending.purpose
                            if "otp_sent_at" not in session:
                                session["otp_sent_at"] = _now().timestamp()
                                flash("Код успешно отправлен!", "success")

            if request.method == "POST":
                is_fetch = _is_fetch_request()
                code = (request.form.get("code") or "").strip()
                if not challenge:
                    if is_fetch:
                        return jsonify({"success": False, "error": "OTP challenge not found."}), 400
                    flash("OTP challenge not found. Please restart.", "error")
                    return redirect(url_for("landing"))
                if not _consume_otp(db_session, challenge, code):
                    fallback = (
                        db_session.execute(
                            select(OTPChallenge)
                            .where(
                                OTPChallenge.user_id == challenge.user_id,
                                OTPChallenge.channel == challenge.channel,
                                OTPChallenge.purpose == challenge.purpose,
                                OTPChallenge.consumed_at.is_(None),
                                OTPChallenge.expires_at >= _now(),
                            )
                            .order_by(desc(OTPChallenge.created_at))
                        )
                        .scalars()
                        .first()
                    )
                    if not fallback or not _consume_otp(db_session, fallback, code):
                        if is_fetch:
                            return jsonify({"success": False, "error": "Invalid or expired code."}), 400
                        flash("Invalid or expired code.", "error")
                        return redirect(request.url)
                    challenge = fallback
                user = db_session.get(User, challenge.user_id)
                if not user:
                    if is_fetch:
                        return jsonify({"success": False, "error": "User not found."}), 404
                    flash("User not found.", "error")
                    return redirect(url_for("landing"))
                if challenge.purpose == "signup" and challenge.channel == "email":
                    user.is_email_verified = True
                    user.preferred_channel = "email"
                if challenge.purpose in {"signup", "login"} and challenge.channel == "telegram":
                    user.is_telegram_verified = True
                    user.preferred_channel = "telegram"
                device_cookie = None
                if challenge.channel == "email" and challenge.purpose in {"signup", "login"}:
                    device_map = _load_trusted_device_map()
                    device_token = _get_device_token_for_user(device_map, user.id)
                    token, _ = _ensure_trusted_device(db_session, user, device_token)
                    device_map[str(user.id)] = token
                    device_cookie = device_map
                commit_with_retry(db_session)
                login_user(user)
                if challenge.channel == "email":
                    session.permanent = True
                session.pop("otp_challenge_id", None)
                session.pop("otp_channel", None)
                session.pop("otp_purpose", None)
                if is_fetch:
                    response = jsonify({"success": True, "redirect": url_for("app_index")})
                else:
                    response = redirect(url_for("app_index"))
                if device_cookie:
                    _set_trusted_device_cookie(response, device_cookie)
                return response

            return render_template("auth/verify.html", challenge=challenge)
        finally:
            db_session.close()

    @app.route("/app/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        db_session = SessionLocal()
        try:
            user = db_session.get(User, current_user.id)
            if not user:
                return redirect(url_for("logout"))
            if not user.profile:
                user.profile = Profile(user=user)
                commit_with_retry(db_session)
            is_fetch = _is_fetch_request()
            if request.method == "POST":
                first_name = (request.form.get("first_name") or "").strip()
                last_name = (request.form.get("last_name") or "").strip()
                full_name = (f"{first_name} {last_name}").strip()
                if not full_name:
                    full_name = (request.form.get("full_name") or "").strip()
                user.profile.full_name = full_name
                age_raw = (request.form.get("age") or "").strip()
                user.profile.age = int(age_raw) if age_raw.isdigit() else None
                user.profile.activity = (request.form.get("activity") or "").strip()
                commit_with_retry(db_session)
                if is_fetch:
                    return jsonify({"success": True})
                flash("Profile updated.", "success")
                return redirect(url_for("profile"))
            if is_fetch:
                full_name = (user.profile.full_name or "").strip()
                first_name = ""
                last_name = ""
                if full_name:
                    parts = full_name.split()
                    first_name = parts[0]
                    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
                return jsonify(
                    {
                        "success": True,
                        "profile": {
                            "first_name": first_name,
                            "last_name": last_name,
                            "activity": user.profile.activity or "",
                            "age": user.profile.age or "",
                            "full_name": full_name,
                        },
                    }
                )
            return render_template("app/profile.html", user=user)
        finally:
            db_session.close()

    @app.post("/app/password/update")
    @login_required
    def update_password():
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if not current_password or not new_password or not confirm_password:
            return jsonify({"success": False, "error": "Заполните все поля."}), 400
        if new_password != confirm_password:
            return jsonify({"success": False, "error": "Пароли не совпадают."}), 400
        db_session = SessionLocal()
        try:
            user = db_session.get(User, current_user.id)
            if not user:
                return jsonify({"success": False, "error": "User not found."}), 404
            if not user.check_password(current_password):
                return jsonify({"success": False, "error": "Неверный текущий пароль."}), 400
            user.set_password(new_password)
            commit_with_retry(db_session)
            return jsonify({"success": True})
        finally:
            db_session.close()

    @app.route("/app/profile/delete/request", methods=["POST"])
    @login_required
    def request_delete_account():
        db_session = SessionLocal()
        try:
            user = db_session.get(User, current_user.id)
            if not user:
                return redirect(url_for("logout"))
            channel = user.preferred_channel or "email"
            challenge, error = _create_otp_challenge(db_session, user.id, channel, "delete")
            if not challenge:
                flash(error or "Failed to send OTP.", "error")
                return redirect(url_for("profile"))
            session["delete_challenge_id"] = challenge.id
            return redirect(url_for("delete_account"))
        finally:
            db_session.close()

    @app.route("/app/profile/delete", methods=["GET"])
    @login_required
    def delete_account():
        return render_template("app/delete_account.html")

    @app.route("/app/profile/delete/confirm", methods=["POST"])
    @login_required
    def confirm_delete_account():
        code = (request.form.get("code") or "").strip()
        db_session = SessionLocal()
        try:
            challenge_id = session.get("delete_challenge_id")
            if not challenge_id:
                flash("OTP challenge not found. Request again.", "error")
                return redirect(url_for("delete_account"))
            challenge = db_session.get(OTPChallenge, challenge_id)
            if not challenge or not _consume_otp(db_session, challenge, code):
                flash("Invalid or expired code.", "error")
                return redirect(url_for("delete_account"))
            user = db_session.get(User, current_user.id)
            if user:
                db_session.delete(user)
                commit_with_retry(db_session)
            session.pop("delete_challenge_id", None)
            logout_user()
            return redirect(url_for("landing"))
        finally:
            db_session.close()

    @app.route("/admin")
    @login_required
    def admin_index():
        if not _require_admin():
            return redirect(url_for("landing"))
        db_session = SessionLocal()
        try:
            users = db_session.execute(select(User).order_by(User.id)).scalars().all()
            return render_template(
                "admin/index.html",
                users=users,
                impersonating=session.get("admin_impersonator_id"),
            )
        finally:
            db_session.close()

    @app.route("/admin/impersonate/<int:user_id>", methods=["POST"])
    @login_required
    def admin_impersonate(user_id: int):
        if not _require_admin():
            return redirect(url_for("landing"))
        db_session = SessionLocal()
        try:
            target = db_session.get(User, user_id)
            if not target:
                flash("User not found.", "error")
                return redirect(url_for("admin_index"))
            if "admin_impersonator_id" not in session:
                session["admin_impersonator_id"] = current_user.id
            login_user(target)
            flash("Impersonation enabled.", "success")
            return redirect(url_for("app_index"))
        finally:
            db_session.close()

    @app.route("/admin/impersonate/stop", methods=["POST"])
    @login_required
    def admin_impersonate_stop():
        admin_id = session.get("admin_impersonator_id")
        if not admin_id:
            flash("Impersonation is not active.", "error")
            return redirect(url_for("admin_index"))
        db_session = SessionLocal()
        try:
            admin_user = db_session.get(User, admin_id)
            if not admin_user:
                flash("Admin account not found.", "error")
                return redirect(url_for("landing"))
            login_user(admin_user)
            session.pop("admin_impersonator_id", None)
            flash("Impersonation stopped.", "success")
            return redirect(url_for("admin_index"))
        finally:
            db_session.close()

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
