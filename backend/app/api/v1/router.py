"""API v1 aggregate router (plan §44)."""

from fastapi import APIRouter

from . import admin, auth, system

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(system.router)
