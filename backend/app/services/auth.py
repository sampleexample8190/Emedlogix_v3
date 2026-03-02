"""
Authentication Service - Handles user registration, login, and JWT tokens.
Now uses PostgreSQL via db.py instead of in-memory store.
"""

import uuid
import os
import jwt
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from app.services.db import save_doctor, get_doctor_by_email

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is not set in .env — refusing to start without a secret key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def register_doctor(email: str, password: str, name: str) -> Dict[str, Any]:
    """Register a new doctor. Returns doctor info + JWT token."""
    email = email.strip().lower()

    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    # Hash password
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # Generate unique doctor ID
    doctor_id = str(uuid.uuid4())

    # Save to DB (raises ValueError if email exists)
    save_doctor(doctor_id, name.strip(), email, password_hash)

    print(f"✅ Registered doctor: {name} ({email}) → ID: {doctor_id}")

    # Generate JWT token
    token = _create_token(doctor_id, email, name.strip())

    return {
        "doctor_id": doctor_id,
        "email": email,
        "name": name.strip(),
        "token": token,
    }


def login_doctor(email: str, password: str) -> Dict[str, Any]:
    """Login a doctor. Returns doctor info + JWT token."""
    email = email.strip().lower()

    user = get_doctor_by_email(email)
    if not user:
        raise ValueError("Invalid email or password.")

    # Verify password
    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise ValueError("Invalid email or password.")

    print(f"✅ Login: {user['name']} ({email})")

    # Generate JWT token
    token = _create_token(str(user["doctor_id"]), email, user["name"])

    return {
        "doctor_id": str(user["doctor_id"]),
        "email": email,
        "name": user["name"],
        "token": token,
    }


def get_current_user(token: str) -> Dict[str, Any]:
    """Decode JWT token and return user info."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "doctor_id": payload.get("doctor_id"),
            "email": payload.get("email"),
            "name": payload.get("name"),
        }
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired. Please login again.")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token.")


def _create_token(doctor_id: str, email: str, name: str) -> str:
    """Create a JWT token."""
    payload = {
        "doctor_id": doctor_id,
        "email": email,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
