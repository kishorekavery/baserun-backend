from fastapi import APIRouter, Depends, Query

from models.datamodel import (
    FriendActionResponse,
    FriendRequestsResponse,
    FriendsResponse,
    RunnerSearchResponse,
    SendFriendRequest,
)
from routers.core import get_current_user
from services import friend_service

router = APIRouter(prefix="/api/v1", tags=["friends"])


@router.get("/users/search", response_model=RunnerSearchResponse)
async def search_runners(
    q: str = Query(..., min_length=2, max_length=32),
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    """
    Find runners by username or name (case-insensitive). Each result carries
    the caller's friendship_status and any pending request_id.
    """
    return await friend_service.search_runners(current_user["id"], q, limit)


@router.get("/friends", response_model=FriendsResponse)
async def list_friends(current_user: dict = Depends(get_current_user)):
    """
    My friends, sorted by username, plus the incoming request count for the badge.
    """
    return await friend_service.list_friends(current_user["id"])


@router.get("/friends/requests", response_model=FriendRequestsResponse)
async def list_friend_requests(current_user: dict = Depends(get_current_user)):
    """
    Pending requests in both directions, newest first.
    """
    return await friend_service.list_requests(current_user["id"])


@router.post("/friends/requests", response_model=FriendActionResponse)
async def send_friend_request(request: SendFriendRequest, current_user: dict = Depends(get_current_user)):
    """
    Send a request by `to_user_id` (or `username`). If the other runner already
    asked you, their request is accepted instead.
    """
    return await friend_service.send_request(current_user, request)


@router.post("/friends/requests/{request_id}/accept", response_model=FriendActionResponse)
async def accept_friend_request(request_id: int, current_user: dict = Depends(get_current_user)):
    """Accept an incoming request (recipient only)."""
    return await friend_service.accept_request(current_user, request_id)


@router.post("/friends/requests/{request_id}/decline", response_model=FriendActionResponse)
async def decline_friend_request(request_id: int, current_user: dict = Depends(get_current_user)):
    """Decline an incoming request (recipient only). The sender is not notified."""
    return await friend_service.decline_request(current_user, request_id)


@router.delete("/friends/requests/{request_id}", response_model=FriendActionResponse)
async def withdraw_friend_request(request_id: int, current_user: dict = Depends(get_current_user)):
    """Withdraw an outgoing request (sender only)."""
    return await friend_service.withdraw_request(current_user, request_id)


@router.delete("/friends/{user_id}", response_model=FriendActionResponse)
async def unfriend(user_id: int, current_user: dict = Depends(get_current_user)):
    """Remove a friend (both directions)."""
    return await friend_service.unfriend(current_user, user_id)
