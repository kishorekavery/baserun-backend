from __future__ import annotations
from enum import Enum
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr, model_validator
from uuid import UUID


# Auth & Message models

class Message(str, Enum):
    USER_CREATED = "user created"
    LOGIN_SUCCESSFUL = "login successful"
    USER_NOT_FOUND = "user not found"
    INVALID_USERNAME_OR_PASSWORD = "invalid username or password"
    ALREADY_EXISTS = "already exists"
    INTERNAL_ERROR = "internal server error"


class LoginRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=64)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=6, max_length=64)

    @model_validator(mode="after")
    def validate_identifier(self) -> LoginRequest:
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


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=64)
    first_name: Optional[str] = Field("Runner", max_length=32)
    last_name: Optional[str] = Field(None, max_length=32)
    phone: Optional[str] = Field(None, max_length=32)
    confirm_password: Optional[str] = None


class RegisterResponse(BaseModel):
    message: Message
    token: Optional[str] = None
    access_token: Optional[str] = None
    user_id: Optional[str] = None
    expires_in: Optional[int] = None
    expires_at: Optional[str] = None
    session_id: Optional[str] = None


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
    speed: Optional[float] = Field(None, description="Speed in meters per second (crucial for anti-cheat)")
    accuracy: Optional[float] = Field(None, description="GPS accuracy radius in meters")


class LocationBatch(BaseModel):
    points: List[LocationPoint]
    activity_id: UUID = Field(..., description="Group Points to a specific run")
    user_id: UUID 


class RunPath(BaseModel):
    user_id: str
    points: List[LocationPoint]


class TelemetryResponse(BaseModel):
    status: str
    message: str
    saved_points: int
    total_received: int


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
    user_id: str
    status: str
    area: float
    perimeter: float
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
    app_name: str = "Run to Claim"
    tagline: str = "Your Run. Your Territory. A Bigger World."
    version: str = "1.0.0"
    active_season: str = "Season 1: Coastal Turf Wars"
    welcome_quote: str = "Run. Explore. Claim. Belong."


# ==========================================
# Screen 2: Social Login
# ==========================================
class SocialLoginRequest(BaseModel):
    provider: str = Field(..., description="google or apple")
    token: str = Field(..., description="OAuth identity or access token")
    email: Optional[EmailStr] = None
    name: Optional[str] = None


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

class ColorPaletteResponse(BaseModel):
    colors: List[ColorPaletteItem]

class UpdateIdentityRequest(BaseModel):
    user_id: Optional[str] = None
    username: Optional[str] = Field(None, min_length=3, max_length=32)
    territory_color: Optional[str] = Field(None, description="Hex color, e.g. #00E5FF")
    avatar_url: Optional[str] = None

class IdentityResponse(BaseModel):
    user_id: str
    username: str
    territory_color: str
    avatar_url: str
    message: str


# ==========================================
# Screen 5: Location Permission & Nearby Preview
# ==========================================
class NearbyTerritoryItem(BaseModel):
    id: int
    name: str
    owner_username: str
    color: str
    area_km2: float
    distance_km: float

class NearbyTerritoriesResponse(BaseModel):
    latitude: float
    longitude: float
    radius_km: float
    total_nearby: int
    territories: List[NearbyTerritoryItem]


# ==========================================
# Screen 6: Home Map & Start Run
# ==========================================
class HomeMapTerritory(BaseModel):
    id: int
    name: str
    owner_id: str
    owner_username: str
    owner_color: str
    club_name: Optional[str] = None
    club_color: Optional[str] = None
    area_km2: float
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
    user_id: str
    title: Optional[str] = "City Run"

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

class RunControlResponse(BaseModel):
    activity_id: str
    status: str
    message: str


# ==========================================
# Screen 8: Run Summary
# ==========================================
class FinishRunRequest(BaseModel):
    steps: Optional[int] = None
    calories: Optional[int] = None
    elevation_gain_m: Optional[float] = None

class RunSummaryResponse(BaseModel):
    activity_id: str
    user_id: str
    title: str
    status: str
    date_formatted: str
    distance_km: float
    duration_formatted: str
    pace_formatted: str
    steps: int
    calories: int
    avg_speed_kmh: float
    elevation_gain_m: float
    can_claim_territory: bool
    polygon_wkt: Optional[str] = None


# ==========================================
# Screen 9: Territory Claimed
# ==========================================
class ClaimFromRunRequest(BaseModel):
    activity_id: str
    territory_name: Optional[str] = None

class TerritoryClaimedResponse(BaseModel):
    status: str
    message: str
    territory_id: int
    territory_name: str
    area_km2: float
    perimeter_km: float
    owner_username: str
    owner_avatar: str
    polygon_wkt: str
    badge_earned: Optional[str] = None
    share_text: str


# ==========================================
# Screen 10: Territory Details & Challenge
# ==========================================
class TerritoryDetailResponse(BaseModel):
    id: int
    name: str
    area_km2: float
    perimeter_km: float
    claimed_on: str
    owner_id: str
    owner_username: str
    owner_avatar: str
    owner_color: str
    total_runs: int
    total_km: float
    days_active: int
    polygon_wkt: str
    is_owner: bool

class ChallengeRequest(BaseModel):
    challenger_id: str

class ChallengeResponse(BaseModel):
    challenge_id: str
    territory_id: int
    territory_name: str
    current_owner: str
    challenger_id: str
    status: str
    instructions: str


# ==========================================
# Screen 11: Club Leaderboard
# ==========================================
class ClubItem(BaseModel):
    id: int
    name: str
    description: str
    avatar_url: str
    color: str
    member_count: int
    territory_count: int
    total_km: float
    is_joined: bool = False

class ClubListResponse(BaseModel):
    total: int
    clubs: List[ClubItem]

class ClubLeaderboardMember(BaseModel):
    rank: int
    user_id: str
    username: str
    avatar_url: str
    territories: int
    total_km: float
    is_current_user: bool = False

class ClubLeaderboardResponse(BaseModel):
    club_id: int
    club_name: str
    total_members: int
    total_territories: int
    leaderboard: List[ClubLeaderboardMember]


# ==========================================
# Screen 12: User Profile, History & Achievements
# ==========================================
class FullUserProfileResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    first_name: str
    last_name: Optional[str] = None
    avatar_url: str
    city: str
    territory_color: str
    total_territories: int
    total_km: float
    total_runs: int
    club_id: Optional[int] = None
    club_name: Optional[str] = None

class RunHistoryItem(BaseModel):
    id: int
    activity_id: str
    title: str
    date: str
    distance_km: float
    duration: str
    pace: str
    calories: int
    status: str

class RunHistoryResponse(BaseModel):
    user_id: str
    total_runs: int
    runs: List[RunHistoryItem]

class AchievementItem(BaseModel):
    id: int
    title: str
    description: str
    badge_icon: str
    unlocked_at: str

class AchievementsResponse(BaseModel):
    user_id: str
    total_badges: int
    achievements: List[AchievementItem]



