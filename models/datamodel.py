from __future__ import annotations
from enum import Enum
from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, EmailStr, AliasChoices, field_validator, model_validator

from utils.helper import HEX_COLOR_RE, USERNAME_RE


# Auth & Message models

class Message(str, Enum):
    USER_CREATED = "user created"
    LOGIN_SUCCESSFUL = "login successful"
    USER_NOT_FOUND = "user not found"
    INVALID_USERNAME_OR_PASSWORD = "invalid username or password"
    ALREADY_EXISTS = "already exists"
    INTERNAL_ERROR = "internal server error"


def _validate_username(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    value = value.strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Username must be 3-32 characters of letters, numbers, '_', '.' or '-'")
    return value


def _validate_hex_color(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    value = value.strip()
    if not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("Value must be a valid hex color starting with # (e.g. #00E5FF)")
    return value.upper()


class LoginRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=255, description="Username or email")
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=6, max_length=128)

    @model_validator(mode="after")
    def validate_identifier(self) -> LoginRequest:
        if self.username:
            self.username = self.username.strip()
        if not self.username and not self.email:
            raise ValueError("Either username or email is required")
        return self


class LoginResponse(BaseModel):
    access_token: str
    token: Optional[str] = None
    token_type: str = "bearer"
    message: Message
    expires_in: int
    expires_at: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    territory_color: Optional[str] = None
    avatar_url: Optional[str] = None
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    is_new_user: Optional[bool] = None


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    first_name: Optional[str] = Field("Runner", max_length=64)
    last_name: Optional[str] = Field(None, max_length=64)
    phone: Optional[str] = Field(None, max_length=32)
    confirm_password: Optional[str] = None

    check_username = field_validator("username")(_validate_username)

    @model_validator(mode="after")
    def passwords_match(self) -> RegisterRequest:
        if self.confirm_password is not None and self.confirm_password != self.password:
            raise ValueError("Passwords do not match")
        return self


class RegisterResponse(LoginResponse):
    pass


class LogoutResponse(BaseModel):
    status: str = "success"
    message: str = "Successfully logged out"


class UserProfile(BaseModel):
    id: int
    username: str
    email: EmailStr
    first_name: str
    last_name: Optional[str] = None
    phone: Optional[str] = None
    created_at: Optional[str] = None


# Core tracking models

class LocationPoint(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, description="GPS Latitude")
    longitude: float = Field(..., ge=-180, le=180, description="GPS Longitude")
    timestamp: datetime = Field(..., description="When this point was recorded by the device")

    # Optional fields for turf-war anti-cheat and tracking
    altitude: Optional[float] = Field(None, description="Elevation in meters")
    speed: Optional[float] = Field(None, ge=0, description="Speed in meters per second (crucial for anti-cheat)")
    accuracy: Optional[float] = Field(None, ge=0, description="GPS accuracy radius in meters")


class LocationBatch(BaseModel):
    points: List[LocationPoint] = Field(..., max_length=5000)
    activity_id: Optional[str] = Field(None, max_length=128, description="Group points to a specific run")
    user_id: Optional[int | str] = Field(None, description="Ignored; identity comes from the token")
    steps: Optional[int] = Field(None, ge=0)
    active_seconds: Optional[int] = Field(None, ge=0)


class RunPath(BaseModel):
    user_id: Optional[int | str] = None
    points: List[LocationPoint] = Field(..., max_length=20000)


# Aliases for backward compatibility
TelemetryData = LocationBatch
ClaimBaseRequest = RunPath


class TelemetryResponse(BaseModel):
    status: str
    message: str
    activity_id: Optional[str] = None
    points_ingested: int
    saved_points: int
    total_received: int
    total_distance_km: float = 0.0
    is_loop_closed: bool = False


class ClaimBaseResponse(BaseModel):
    status: str
    message: str
    base_id: Optional[int] = None
    area: Optional[float] = None
    perimeter: Optional[float] = None
    polygon_wkt: Optional[str] = None


# UI View Models

class BaseItem(BaseModel):
    id: int
    name: str
    user_id: str
    club_id: Optional[int] = None
    status: str
    area: float = Field(..., description="Area in m²")
    perimeter: float = Field(..., description="Perimeter in m")
    area_km2: float
    perimeter_km: float
    owner_color: Optional[str] = None
    polygon_wkt: str
    created_at: str


class BaseListResponse(BaseModel):
    total: int
    bases: List[BaseItem]


class UserStatsResponse(BaseModel):
    user_id: str
    total_bases: int
    total_area_sq_meters: float
    total_perimeter_meters: float


class TelemetryPointItem(BaseModel):
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed: Optional[float] = None
    accuracy: Optional[float] = None
    timestamp: str


class ActivityTelemetryResponse(BaseModel):
    activity_id: str
    user_id: str
    total_points: int
    points: List[TelemetryPointItem]


# ==========================================
# Screen 1: Splash Screen
# ==========================================
class AppConfigResponse(BaseModel):
    app_name: str
    tagline: str
    current_season: str
    active_season: str
    version: str
    min_version: str
    maintenance_mode: bool
    server_time: str
    welcome_quote: str


# ==========================================
# Screen 2: Social Login
# ==========================================
class SocialLoginRequest(BaseModel):
    provider: str = Field(..., description="google or apple")
    token: Optional[str] = Field(None, description="Provider ID token (Google id_token / Apple identityToken)")
    id_token: Optional[str] = Field(None, description="Alternative alias for token from Flutter plugins")
    email: Optional[EmailStr] = None
    name: Optional[str] = Field(None, max_length=128)

    @model_validator(mode="after")
    def validate_social_token(self) -> SocialLoginRequest:
        self.provider = self.provider.strip().lower()
        if not self.token and not self.id_token:
            raise ValueError("Either token or id_token is required")
        if not self.token:
            self.token = self.id_token
        return self


# ==========================================
# Screen 3: Register & Username Check
# ==========================================
class CheckUsernameResponse(BaseModel):
    username: str
    available: bool
    message: str


# ==========================================
# Screen 4: Choose Your Identity (Username & Color)
# ==========================================
class ColorPaletteItem(BaseModel):
    name: str
    hex: str
    is_premium: bool = False


class ColorPaletteResponse(BaseModel):
    colors: List[ColorPaletteItem]


class UpdateIdentityRequest(BaseModel):
    user_id: Optional[int | str] = Field(None, description="Ignored; identity comes from the token")
    username: Optional[str] = Field(None, min_length=3, max_length=32)
    territory_color: Optional[str] = Field(None, description="Hex color, e.g. #00E5FF")
    avatar_url: Optional[str] = Field(None, max_length=2048)
    bio: Optional[str] = Field(None, max_length=500)
    first_name: Optional[str] = Field(None, max_length=64)
    last_name: Optional[str] = Field(None, max_length=64)
    city: Optional[str] = Field(None, max_length=120)
    club_id: Optional[int] = Field(None, description="Join this club; send null explicitly to leave your club")
    privacy_level: Optional[Literal["public", "friends", "private"]] = None

    check_username = field_validator("username")(_validate_username)
    check_color = field_validator("territory_color")(_validate_hex_color)


class IdentityResponse(BaseModel):
    user_id: str
    username: str
    territory_color: str
    avatar_url: str
    bio: Optional[str] = None
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    privacy_level: Optional[str] = None
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class AccountActionResponse(BaseModel):
    status: str = "success"
    message: str


class DeleteAccountRequest(BaseModel):
    password: Optional[str] = Field(None, max_length=128, description="Required for password accounts")
    confirmation: Optional[str] = Field(None, description='Social accounts have no password: send "DELETE"')


class ConnectedApp(BaseModel):
    provider: str
    name: str
    connected: bool
    connected_at: Optional[str] = None
    is_primary: bool = Field(..., description="The account was created with this provider")


class ConnectedAppsResponse(BaseModel):
    apps: List[ConnectedApp]


# ==========================================
# Screen 5: Location Permission & Nearby Preview
# ==========================================
class NearbyTerritoryItem(BaseModel):
    id: int
    name: str
    owner_id: Optional[str] = None
    owner_username: str
    owner_color: str
    color: str
    area_km2: float
    distance_meters: float
    distance_km: float


class NearbyTerritoriesResponse(BaseModel):
    latitude: float
    longitude: float
    radius_km: float
    count: int
    total_nearby: int
    territories: List[NearbyTerritoryItem]


# ==========================================
# Screen 6: Home Map & Start Run
# ==========================================
class HomeMapTerritory(BaseModel):
    id: int
    name: str
    owner_id: Optional[str] = None
    owner_username: str
    owner_color: str
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    club_color: Optional[str] = None
    status: str
    area_km2: float
    defense_level: int
    polygon_wkt: str
    is_user_territory: bool
    is_club_territory: bool


class HomeMapResponse(BaseModel):
    active_runners_nearby: int
    user_territories_count: int
    club_territories_count: int
    unclaimed_territories_count: int
    territories: List[HomeMapTerritory]


class StartRunRequest(BaseModel):
    user_id: Optional[int | str] = Field(None, description="Ignored; identity comes from the token")
    activity_id: Optional[str] = Field(None, min_length=1, max_length=128, description="Client-generated id (offline starts)")
    title: Optional[str] = Field(None, max_length=120)
    target_territory_id: Optional[int] = None
    route_mode: Optional[str] = Field("free_run", max_length=32)
    start_latitude: Optional[float] = Field(None, ge=-90, le=90)
    start_longitude: Optional[float] = Field(None, ge=-180, le=180)
    device_info: Optional[str] = Field(None, max_length=255)


class StartRunResponse(BaseModel):
    activity_id: str
    user_id: str
    title: str
    status: str
    start_time: str
    message: str


# ==========================================
# Screen 7: Run In Progress (Live Tracking)
# ==========================================
class LiveRunStatsResponse(BaseModel):
    activity_id: str
    status: str
    distance_km: float
    duration_seconds: int
    duration_formatted: str
    pace_formatted: str
    current_speed_kmh: float
    points_count: int
    is_loop_closed: bool = False
    current_latitude: Optional[float] = None
    current_longitude: Optional[float] = None
    recent_points: Optional[List[dict]] = None


class RunControlResponse(BaseModel):
    activity_id: str
    status: str
    message: str


# ==========================================
# Screen 8: Run Summary
# ==========================================
class FinishRunRequest(BaseModel):
    steps: Optional[int] = Field(None, ge=0, validation_alias=AliasChoices("total_steps", "steps"))
    active_seconds: Optional[int] = Field(None, ge=0)
    calories: Optional[int] = Field(None, ge=0)
    elevation_gain_m: Optional[float] = Field(None, ge=0)
    end_latitude: Optional[float] = Field(None, ge=-90, le=90)
    end_longitude: Optional[float] = Field(None, ge=-180, le=180)
    points: Optional[List[LocationPoint]] = Field(None, max_length=20000)
    distance_km: Optional[float] = Field(None, ge=0, le=1000)


class RunSummaryResponse(BaseModel):
    activity_id: str
    user_id: str
    title: str
    status: str
    date_formatted: str
    start_time: str
    end_time: Optional[str] = None
    distance_km: float
    duration_seconds: int
    duration_formatted: str
    pace_formatted: str
    steps: int
    calories: int
    avg_speed_kmh: float
    elevation_gain_m: float
    is_loop_closed: bool
    can_claim_territory: bool
    is_claimed: bool
    suggested_area_km2: float
    polygon_wkt: Optional[str] = None
    badges_earned: List[str] = []


# ==========================================
# Screen 9: Territory Claimed
# ==========================================
class ClaimFromRunRequest(BaseModel):
    activity_id: str = Field(..., min_length=1, max_length=128)
    territory_name: Optional[str] = Field(None, max_length=120)
    points: Optional[List[LocationPoint]] = Field(None, max_length=20000)
    area_km2: Optional[float] = Field(None, description="Ignored; the server computes the area")
    perimeter_km: Optional[float] = Field(None, description="Ignored; the server computes the perimeter")


class TerritoryClaimedResponse(BaseModel):
    status: str = Field(..., description="success | fortified | takeover | challenge_failed")
    message: str
    territory_id: int
    territory_name: str
    area_km2: float
    perimeter_km: float
    owner_username: str
    owner_avatar: str
    polygon_wkt: str
    defense_level: int = 1
    badge_earned: Optional[str] = None
    badges_earned: List[str] = []
    share_text: str


# ==========================================
# Screen 10: Territory Details & Challenge
# ==========================================
class TerritoryDetailResponse(BaseModel):
    id: int
    name: str
    status: str
    area_km2: float
    perimeter_km: float
    claimed_on: str
    held_since: str
    owner_id: Optional[str] = None
    owner_username: str
    owner_avatar: str
    owner_color: str
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    club_color: Optional[str] = None
    record_pace: str
    record_pace_seconds: Optional[int] = None
    defense_level: int
    times_defended: int
    times_challenged: int
    total_runs: int
    total_km: float
    days_active: int
    polygon_wkt: str
    is_owner: bool
    is_user_territory: bool
    can_challenge: bool


class ChallengeRequest(BaseModel):
    challenger_id: Optional[int | str] = Field(None, description="Ignored; identity comes from the token")


class ChallengeResponse(BaseModel):
    challenge_id: str
    territory_id: int
    target_territory_id: int
    territory_name: str
    current_owner: str
    challenger_id: str
    status: str
    target_pace: str
    target_distance_km: float
    instructions: str


# ==========================================
# Screen 11: Club Leaderboard
# ==========================================
class ClubItem(BaseModel):
    id: int
    name: str
    tagline: str
    description: str
    avatar_url: str
    color: str
    member_count: int
    territory_count: int
    total_area_km2: float
    total_km: float
    is_joined: bool = False


class ClubListResponse(BaseModel):
    total: int
    clubs: List[ClubItem]


class ClubLeaderboardMember(BaseModel):
    rank: int
    user_id: str
    username: str
    runner_name: str
    avatar_url: str
    runner_avatar: str
    territories: int
    territories_held: int
    total_km: float
    avg_pace: str
    is_current_user: bool = False


class ClubLeaderboardResponse(BaseModel):
    club_id: int
    club_name: str
    total_members: int
    total_territories: int
    leaderboard: List[ClubLeaderboardMember]


class ClubMembershipResponse(BaseModel):
    status: str
    message: str
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    territories_transferred: int = 0


# ==========================================
# Screen 12: User Profile, History & Achievements
# ==========================================
class UserStats(BaseModel):
    total_runs: int
    total_km: float
    total_duration_hours: float
    bases_claimed: int
    bases_held: int
    avg_pace: str


class FullUserProfileResponse(BaseModel):
    id: int
    username: str
    email: Optional[EmailStr] = None
    first_name: str
    last_name: Optional[str] = None
    avatar_url: str
    bio: Optional[str] = None
    city: str
    territory_color: str
    total_territories: int
    total_km: float
    total_runs: int
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    favorite_base: Optional[str] = None
    stats: UserStats
    is_restricted: bool = Field(False, description="True when privacy settings hide this runner's details from the viewer")
    privacy_level: Optional[str] = Field(None, description="Only returned to the runner themselves")
    auth_provider: Optional[str] = Field(None, description="password | google | apple; only returned to the runner themselves")


class RunHistoryItem(BaseModel):
    id: int
    activity_id: str
    title: str
    date: str
    distance_km: float
    duration: str
    duration_seconds: int
    pace: str
    calories: int
    status: str
    is_claimed: bool
    badge: str


class RunHistoryResponse(BaseModel):
    user_id: str
    total_runs: int
    limit: int
    offset: int
    runs: List[RunHistoryItem]


class AchievementItem(BaseModel):
    id: int
    key: str
    title: str
    description: str
    badge_icon: str
    unlocked_at: str


class AchievementsResponse(BaseModel):
    user_id: str
    total_badges: int
    achievements: List[AchievementItem]


# ==========================================
# Friends & Friend Requests
# ==========================================
class RunnerSummary(BaseModel):
    user_id: int
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    territory_color: Optional[str] = None
    club_name: Optional[str] = None
    total_km: Optional[float] = None
    bases_held: Optional[int] = None
    friendship_status: str = Field(..., description="none | friends | outgoing_pending | incoming_pending | self")
    request_id: Optional[int] = None
    friends_since: Optional[str] = None


class RunnerSearchResponse(BaseModel):
    query: str
    results: List[RunnerSummary]


class FriendsResponse(BaseModel):
    total: int
    incoming_pending_count: int
    friends: List[RunnerSummary]


class FriendRequestItem(BaseModel):
    id: int
    direction: str = Field(..., description="incoming | outgoing")
    status: str
    user: RunnerSummary
    created_at: str


class FriendRequestsResponse(BaseModel):
    incoming: List[FriendRequestItem]
    outgoing: List[FriendRequestItem]


class SendFriendRequest(BaseModel):
    to_user_id: Optional[int] = None
    username: Optional[str] = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_target(self) -> SendFriendRequest:
        if self.to_user_id is None and not (self.username and self.username.strip()):
            raise ValueError("Either to_user_id or username is required")
        return self


class FriendActionResponse(BaseModel):
    status: str = "success"
    message: str
    friendship_status: str
    request_id: Optional[int] = None
