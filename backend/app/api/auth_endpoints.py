"""
Authentication API Endpoints - Register, Login, and Get Current User.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from app.services.auth import register_doctor, login_doctor, get_current_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenRequest(BaseModel):
    token: str


@router.post("/register")
async def register(req: RegisterRequest):
    """Register a new doctor account."""
    try:
        result = register_doctor(req.email, req.password, req.name)
        return {"status": "success", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
async def login(req: LoginRequest):
    """Login with email and password."""
    try:
        result = login_doctor(req.email, req.password)
        return {"status": "success", **result}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/me")
async def get_me(token: str):
    """Get current user info from JWT token (passed as query param)."""
    try:
        user = get_current_user(token)
        return {"status": "success", **user}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
