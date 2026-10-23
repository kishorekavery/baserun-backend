# BaseRun Backend API Usage & Integration Guide

All endpoints communicate using JSON (`Content-Type: application/json`). Protected routes require a valid JWT passed via `Authorization: Bearer <TOKEN>`.

### Base URLs
* **Local Development:** `http://localhost:8000` (or `http://10.0.2.2:8000` for Android Emulator)
* **Production Deployment:** `https://baserun-backend-gamma.vercel.app`
* **Interactive Swagger UI:** `/docs`
* **OpenAPI Schema:** `/openapi.json`

### Conventions
* **Storage:** PostgreSQL only. All areas, perimeters, distances, durations and paces are computed by the server; client-supplied values for these are ignored.
* **Route aliases:** `/auth/*`, `/users/*` and `/runs/*` are also served under `/api/v1/` (e.g. `/api/v1/runs/start`).
* **Auth levels used below:**
  * **No** — public.
  * **Optional** — public, but a valid Bearer token personalises the response (`is_joined`, `is_user_territory`, ...). A missing, expired or revoked token is treated as an anonymous viewer (never a 401).
  * **`Bearer <TOKEN>`** — required; `401` without a valid session.
* **Identity always comes from the token.** Body/query fields such as `user_id`, `challenger_id` or `current_user_id` are ignored when a token is sent.
* **Timestamps** are ISO 8601 UTC, e.g. `2026-10-24T01:35:10.123456Z`.
* **Pace** is formatted `M'SS"` per km (`"6'30\""`); **duration** is `MM:SS`, or `H:MM:SS` from one hour.

### Error Format
Every error returns a `detail` and a machine-readable `code`:
```json
{ "detail": "Session has been revoked", "code": "UNAUTHORIZED" }
```
| HTTP | `code` | Typical cause |
| :--- | :--- | :--- |
| 400 | `BAD_REQUEST` | Duplicate account, run already claimed, loop not closed |
| 401 | `UNAUTHORIZED` | Missing/invalid/expired/revoked token, wrong credentials |
| 403 | `FORBIDDEN` | Acting on another runner's run or account |
| 404 | `NOT_FOUND` | Unknown run, territory, club or user |
| 409 | `CONFLICT` | Run already completed, duplicate `activity_id` on start |
| 422 | `VALIDATION_ERROR` | Invalid body/query (`detail` is the standard FastAPI error list) |
| 503 | `SERVICE_UNAVAILABLE` | Database unreachable — safe to retry |

Validation error example:
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "territory_color"],
      "msg": "Value error, Value must be a valid hex color starting with # (e.g. #00E5FF)",
      "input": "blue"
    }
  ],
  "code": "VALIDATION_ERROR"
}
```

---

## 1. App Configuration & System Health

### `GET /` — Root Server Health
* **Auth Required:** No
* **Description:** Check if the backend server is online and operational.
* **Response `(200 OK)`:**
```json
{
  "status": "ok",
  "message": "Welcome to the Runners Application!"
}
```

### `GET /health/db` — Database Connection Health
* **Auth Required:** No
* **Description:** Pings PostgreSQL. Returns `503` with `"connection": "error"` when the database is unreachable.
* **Response `(200 OK)`:**
```json
{
  "database": "Ready",
  "connection": "ok",
  "active_sessions": 24,
  "latency_ms": 3.2,
  "config": {
    "db_type": "postgresql",
    "host": "aws-0-ap-northeast-1.pooler.supabase.com",
    "port": 5432,
    "name": "baserun",
    "user": "postgres.xxxx",
    "password": "******",
    "sslmode": "require",
    "min_connections": 1,
    "max_connections": 10,
    "timeout": 10.0,
    "database_url": null
  }
}
```

### `GET /api/v1/app/config` — App Branding & Season Metadata
* **Auth Required:** No
* **Screen / Caller:** `WelcomeScreen` (Splash)
* **Notes:** `current_season` comes from the `seasons` table. `maintenance_mode`, `version` and `min_version` come from the `MAINTENANCE_MODE`, `APP_VERSION` and `APP_MIN_VERSION` environment variables. `active_season` is a legacy alias of `current_season`.
* **Response `(200 OK)`:**
```json
{
  "app_name": "BASER",
  "tagline": "Turn Your Miles into Territory",
  "current_season": "Season 1: Chennai Conquest",
  "active_season": "Season 1: Chennai Conquest",
  "version": "1.0.0",
  "min_version": "1.0.0",
  "maintenance_mode": false,
  "server_time": "2026-10-24T01:30:00Z",
  "welcome_quote": "Run. Explore. Claim. Belong."
}
```

### `GET /api/v1/app/color-palette` — Territory Identity Swatches
* **Auth Required:** No
* **Screen / Caller:** `ProfileSetupScreen`
* **Response `(200 OK)`:**
```json
{
  "colors": [
    { "name": "Electric Cyan", "hex": "#00E5FF", "is_premium": false },
    { "name": "Neon Lime", "hex": "#76FF03", "is_premium": false },
    { "name": "Hot Magenta", "hex": "#FF007F", "is_premium": false },
    { "name": "Cyber Purple", "hex": "#7928CA", "is_premium": false },
    { "name": "Blaze Orange", "hex": "#FF5722", "is_premium": false },
    { "name": "Blaze Gold", "hex": "#FFD700", "is_premium": true }
  ]
}
```

---

## 2. Authentication & Identity

Sessions are stored server-side. A token stays valid for `expires_in` seconds (default 24 h); in the default `idle` mode every request extends it, up to 30 days after login. By default only **one** session per runner is active: logging in on a new device revokes the previous token (`MAX_SESSIONS_PER_USER`).

### `GET /auth/check-username?username={username}` — Username Availability Check
* **Auth Required:** No
* **Screen / Caller:** `AuthScreen` (Real-time input validation)
* **Notes:** Case-insensitive (`Runner` and `runner` collide). Usernames must be 3–32 characters of `[a-zA-Z0-9_.-]`; invalid input returns `available: false` with an explanation.
* **Response `(200 OK)`:**
```json
{
  "username": "chennairunner",
  "available": true,
  "message": "Username is available"
}
```

### `POST /auth/register` (Alias: `POST /api/v1/bases/register`) — Register Account
* **Auth Required:** No
* **Screen / Caller:** `AuthScreen` (Sign Up)
* **Request Body:** (`first_name`, `last_name`, `phone`, `confirm_password` are optional; `password` is 6–128 characters; if `confirm_password` is sent it must match)
```json
{
  "username": "chennairunner",
  "email": "runner@example.com",
  "password": "SecurePassword123!",
  "confirm_password": "SecurePassword123!",
  "first_name": "Vikram",
  "last_name": "R"
}
```
* **Response `(200 OK)`:** the runner is signed in immediately.
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
  "token": "eyJhbGciOiJIUzI1NiIsIn...",
  "token_type": "bearer",
  "message": "user created",
  "expires_in": 86400,
  "expires_at": "2026-10-25T01:30:00Z",
  "session_id": "4b7b25a9...",
  "user_id": 14,
  "username": "chennairunner",
  "email": "runner@example.com",
  "first_name": "Vikram",
  "last_name": "R",
  "territory_color": "#00E5FF",
  "avatar_url": null,
  "club_id": null,
  "club_name": null,
  "is_new_user": null
}
```
* **Errors:** `400 "already exists"` (username or email taken, case-insensitive), `422` (invalid username characters, password mismatch).

### `POST /auth/login` (Alias: `POST /api/v1/bases/login`) — Authenticate Runner
* **Auth Required:** No
* **Screen / Caller:** `AuthScreen` (Login)
* **Request Body:** `username` accepts **either the username or the email** (case-insensitive). An `email` field is also accepted.
```json
{
  "username": "chennairunner",
  "password": "SecurePassword123!"
}
```
* **Response `(200 OK)`:** includes the profile fields, so no extra `/users/me` call is needed after sign-in.
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
  "token": "eyJhbGciOiJIUzI1NiIsIn...",
  "token_type": "bearer",
  "message": "login successful",
  "expires_in": 86400,
  "expires_at": "2026-10-25T01:30:00Z",
  "session_id": "4b7b25a9...",
  "user_id": 14,
  "username": "chennairunner",
  "email": "runner@example.com",
  "first_name": "Vikram",
  "last_name": "R",
  "territory_color": "#00E5FF",
  "avatar_url": "https://assets.baserun.app/avatars/runner_01.png",
  "club_id": 1,
  "club_name": "Marina Striders",
  "is_new_user": null
}
```
* **Errors:** `401 "invalid username or password"` — returned for both an unknown account and a wrong password.

### `POST /auth/social-login` — Google / Apple SSO
* **Auth Required:** No
* **Screen / Caller:** `AuthScreen` (Continue with Google / Apple)
* **Notes:**
  * `token` (or `id_token`) must be the provider's **ID token**: Google `idToken` / Apple `identityToken` — not an OAuth access token.
  * The server verifies the token with Google/Apple and uses the **verified** email; the `email` in the body is ignored in production.
  * The account is found by that email or created on first sign-in (`is_new_user: true`).
* **Request Body:**
```json
{
  "provider": "google",
  "token": "eyJhbGciOiJSUzI1NiIsImtpZCI6...",
  "name": "Vikram R"
}
```
* **Response `(200 OK)`:** same shape as `/auth/login`, with `"is_new_user": true | false`.
* **Errors:** `400 "Unsupported social provider"`, `401 "Invalid Google token"` / `"Invalid Apple token"` / `"Google account email is not verified"`.

### `POST /auth/logout` (Alias: `POST /api/v1/bases/logout`) — Invalidate Session
* **Auth Required:** `Bearer <TOKEN>`
* **Screen / Caller:** `ProfileScreen` (Log Out)
* **Response `(200 OK)`:** the token is revoked; further calls with it return `401 "Session has been revoked"`.
```json
{
  "status": "success",
  "message": "Successfully logged out"
}
```

### `GET /api/v1/bases/me` — Minimal Session Check
* **Auth Required:** `Bearer <TOKEN>`
* **Response `(200 OK)`:**
```json
{
  "id": 14,
  "username": "chennairunner",
  "email": "runner@example.com",
  "first_name": "Vikram",
  "last_name": "R",
  "phone": "",
  "created_at": "2026-10-18T10:00:00Z"
}
```

---

## 3. Territories & Home Map

### `GET /api/v1/map/home` — Home Map Data
* **Auth Required:** Optional
* **Screen / Caller:** `HomeMapScreen`
* **Query Parameters:**
  * **Viewport (preferred):** `min_lat`, `min_lng`, `max_lat`, `max_lng` — all four together, or `422`.
  * **Radius (otherwise):** `lat`, `lng`, `radius_km` (default `10.0`, max `100`).
  * **Legacy:** `user_id` — viewer id, used only when no token is sent.
* **Notes:**
  * `is_user_territory` / `is_club_territory` are computed for the caller.
  * `owner_color` is the owner's **current** `territory_color`.
  * `defense_level` drops one level per 7 days without a defense run (minimum 1).
  * Polygons over 2 km² are simplified.
  * `user_territories_count` / `club_territories_count` are the caller's totals.
  * `active_runners_nearby` counts runners who sent telemetry inside the view in the last 10 minutes.
  * Returns at most 1000 territories.
* **Example:** `GET /api/v1/map/home?min_lat=13.0&min_lng=80.2&max_lat=13.1&max_lng=80.3`
* **Response `(200 OK)`:**
```json
{
  "active_runners_nearby": 4,
  "user_territories_count": 2,
  "club_territories_count": 5,
  "unclaimed_territories_count": 0,
  "territories": [
    {
      "id": 1,
      "name": "Marina Beach Loop",
      "owner_id": "14",
      "owner_username": "chennairunner",
      "owner_color": "#00E5FF",
      "club_id": 1,
      "club_name": "Marina Striders",
      "club_color": "#2196F3",
      "status": "active",
      "area_km2": 0.3821,
      "defense_level": 2,
      "polygon_wkt": "POLYGON ((80.2707 13.0827, 80.275 13.085, 80.278 13.081, 80.2707 13.0827))",
      "is_user_territory": true,
      "is_club_territory": true
    }
  ]
}
```

### `GET /api/v1/territories/nearby?lat={lat}&lng={lng}&radius_km={r}` — Nearby Zones
* **Auth Required:** No
* **Screen / Caller:** `LocationPermissionScreen`, `HomeMapScreen`
* **Query Parameters:** `lat`, `lng`, `radius_km` (default `5.0`, max `50`), `limit` (default `50`, max `200`).
* **Notes:** Only active territories within `radius_km` are returned, nearest first. Distance is measured to the territory's edge; it is `0` when the point is inside the territory.
* **Response `(200 OK)`:**
```json
{
  "latitude": 13.0827,
  "longitude": 80.2707,
  "radius_km": 5.0,
  "count": 1,
  "total_nearby": 1,
  "territories": [
    {
      "id": 101,
      "name": "Marina Promenade Loop",
      "owner_id": "14",
      "owner_username": "chennairunner",
      "owner_color": "#00E5FF",
      "color": "#00E5FF",
      "area_km2": 0.45,
      "distance_meters": 420.5,
      "distance_km": 0.42
    }
  ]
}
```

### `GET /api/v1/territories/{territory_id}` — Territory Details
* **Auth Required:** Optional (legacy `?current_user_id={id}` is used when no token is sent)
* **Screen / Caller:** `TerritoryDetailsScreen`
* **Response `(200 OK)`:**
```json
{
  "id": 42,
  "name": "Marina Circuit Alpha",
  "status": "active",
  "area_km2": 0.4215,
  "perimeter_km": 3.42,
  "claimed_on": "Oct 18, 2026",
  "held_since": "2026-10-18T10:00:00Z",
  "owner_id": "14",
  "owner_username": "chennairunner",
  "owner_avatar": "https://assets.baserun.app/avatars/runner_01.png",
  "owner_color": "#00E5FF",
  "club_id": 1,
  "club_name": "Marina Striders",
  "club_color": "#2196F3",
  "record_pace": "5'45\"",
  "record_pace_seconds": 345,
  "defense_level": 3,
  "times_defended": 4,
  "times_challenged": 2,
  "total_runs": 5,
  "total_km": 17.1,
  "days_active": 6,
  "polygon_wkt": "POLYGON ((80.2707 13.0827, 80.275 13.085, 80.278 13.081, 80.2707 13.0827))",
  "is_owner": true,
  "is_user_territory": true,
  "can_challenge": false
}
```
* **UI hint:** if `is_owner`, show "Fortify Base"; if `can_challenge`, show "Challenge Base". Anonymous viewers get `can_challenge: false`.

### `POST /api/v1/territories/{territory_id}/challenge` — Challenge Owner
* **Auth Required:** `Bearer <TOKEN>`
* **Screen / Caller:** `TerritoryDetailsScreen`
* **Request Body:** none required (`{"challenger_id": ...}` is accepted and ignored).
* **Notes:** Registers the intent. Calling it again returns the same open challenge. To win, run the perimeter faster than `target_pace` and submit the run to `claim-from-run`.
* **Response `(200 OK)`:**
```json
{
  "challenge_id": "chal_3f9a1c0b7d2e",
  "territory_id": 42,
  "target_territory_id": 42,
  "territory_name": "Marina Circuit Alpha",
  "current_owner": "chennairunner",
  "challenger_id": "99",
  "status": "challenge_active",
  "target_pace": "5'45\"",
  "target_distance_km": 3.42,
  "instructions": "Run the 3.42 km perimeter of Marina Circuit Alpha faster than 5'45\" /km to claim this base from @chennairunner!"
}
```
* **Errors:** `400 "You cannot challenge your own territory"`, `404`.

---

## 4. Run Session & Live Telemetry Tracking

Run `status` values are `recording`, `paused` and `completed`.

### `POST /api/v1/runs/start` — Start Run Session
* **Auth Required:** `Bearer <TOKEN>`
* **Screen / Caller:** `HomeMapScreen` ("Start Run" Button)
* **Request Body:** (all optional)
  * `activity_id` — a client-generated id, for runs started offline.
  * `target_territory_id` — must exist, or `404`.
```json
{
  "title": "Marina Beach Loop",
  "target_territory_id": null,
  "route_mode": "free_run",
  "start_latitude": 13.0827,
  "start_longitude": 80.2707,
  "device_info": "Pixel 7 - Android 14"
}
```
* **Response `(200 OK)`:**
```json
{
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "user_id": "14",
  "title": "Marina Beach Loop",
  "status": "recording",
  "start_time": "2026-10-24T01:35:10Z",
  "message": "Run session started successfully"
}
```
* **Errors:** `409` if the supplied `activity_id` already exists.

### `POST /api/v1/runs/{activity_id}/telemetry` — Stream GPS Breadcrumbs
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunInProgressScreen` (10-second buffer flush / app pause)
* **Notes:**
  * **Dropped fixes:** fixes with `accuracy > 30` m or `speed > 27.78` m/s are discarded.
  * **Retries are safe:** points not newer than the last stored fix are ignored, so a resent batch adds nothing.
  * **Offline runs:** an unknown `activity_id` is created on the fly.
  * **Loop flag:** `is_loop_closed` is true once the run has covered ≥ 300 m and is back within 50 m of its first fix.
* **Request Body:** (`steps` and `active_seconds` optional)
```json
{
  "points": [
    {
      "latitude": 13.08275,
      "longitude": 80.27082,
      "altitude": 12.4,
      "speed": 3.1,
      "accuracy": 4.2,
      "timestamp": "2026-10-24T01:35:20Z"
    },
    {
      "latitude": 13.08289,
      "longitude": 80.27105,
      "altitude": 12.6,
      "speed": 3.2,
      "accuracy": 3.8,
      "timestamp": "2026-10-24T01:35:30Z"
    }
  ],
  "steps": 142,
  "active_seconds": 20
}
```
* **Response `(200 OK)`:**
```json
{
  "status": "success",
  "message": "telemetry recorded",
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "points_ingested": 2,
  "saved_points": 2,
  "total_received": 2,
  "total_distance_km": 0.048,
  "is_loop_closed": false
}
```
* **Errors:** `403` (another runner's run), `409 "Run is already completed"`.

### `POST /api/v1/runs/{activity_id}/pause` & `/resume` — Pause/Resume Run
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunInProgressScreen`
* **Notes:** Paused time is excluded from the run duration. Pausing a paused run, or resuming a recording one, does nothing.
* **Response `(200 OK)`:**
```json
{
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "status": "paused",
  "message": "Run paused"
}
```
`/resume` returns `"status": "recording"`, `"message": "Run resumed"`.
* **Errors:** `403`, `404`, `409 "Run is already completed"`.

### `GET /api/v1/runs/{activity_id}/live` — Live HUD & Coordinates
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunInProgressScreen` (Polled for live HUD metrics)
* **Response `(200 OK)`:** (`recent_points` = last 50 fixes, oldest first)
```json
{
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "status": "recording",
  "distance_km": 1.45,
  "duration_seconds": 540,
  "duration_formatted": "09:00",
  "pace_formatted": "6'12\"",
  "current_speed_kmh": 9.7,
  "points_count": 54,
  "is_loop_closed": false,
  "current_latitude": 13.0835,
  "current_longitude": 80.2715,
  "recent_points": [
    { "latitude": 13.0835, "longitude": 80.2715, "speed": 2.7, "timestamp": "2026-10-24T01:44:10Z" }
  ]
}
```

### `GET /api/v1/runs/{activity_id}/path` — Full GPS Breadcrumb Path
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunInProgressScreen`, `RunSummaryScreen`
* **Response `(200 OK)`:**
```json
{
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "total_points": 54,
  "path": [
    { "latitude": 13.0820, "longitude": 80.2700, "altitude": 10.5, "speed": 3.2, "timestamp": "2026-10-24T01:35:20Z" }
  ]
}
```

### `POST /api/v1/runs/{activity_id}/finish` — Finalize Run Session
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunInProgressScreen` → `RunSummaryScreen`
* **Notes:**
  * **Idempotent:** finishing an already completed run returns the stored summary unchanged.
  * **Offline runs:** an unknown `activity_id` is recovered from `points`. Breadcrumbs the server is missing are ingested with the same filters as telemetry.
  * **Server-computed metrics:** distance comes from the stored points; `distance_km` and `total_steps` are fallbacks only when there are fewer than 2 points.
  * **Duration** is wall-clock time minus pauses. For runs started offline, it is `active_seconds`, falling back to the time between the first and last point.
  * **Claimable when** the loop is closed: ≥ 300 m, ends ≤ 50 m from the start, and ≥ 3 points enclosing an area.
* **Request Body:** (all optional; `total_steps` may also be sent as `steps`)
```json
{
  "end_latitude": 13.08271,
  "end_longitude": 80.27072,
  "total_steps": 4210,
  "active_seconds": 1335,
  "calories": 310,
  "elevation_gain_m": 12.0,
  "distance_km": 3.42,
  "points": [ { "latitude": 13.0820, "longitude": 80.2700, "timestamp": "2026-10-24T01:35:20Z" } ]
}
```
* **Response `(200 OK)`:**
```json
{
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "user_id": "14",
  "title": "Marina Beach Loop",
  "status": "completed",
  "date_formatted": "Oct 24, 2026 - 01:35 AM",
  "start_time": "2026-10-24T01:35:10Z",
  "end_time": "2026-10-24T01:57:25Z",
  "distance_km": 3.42,
  "duration_seconds": 1335,
  "duration_formatted": "22:15",
  "pace_formatted": "6'30\"",
  "steps": 4210,
  "calories": 310,
  "avg_speed_kmh": 9.2,
  "elevation_gain_m": 12.0,
  "is_loop_closed": true,
  "can_claim_territory": true,
  "is_claimed": false,
  "suggested_area_km2": 0.4215,
  "polygon_wkt": "POLYGON ((80.2707 13.0827, 80.275 13.085, 80.278 13.081, 80.2707 13.0827))",
  "badges_earned": ["First Steps"]
}
```
`polygon_wkt` is `null` and `can_claim_territory` is `false` when the loop is open — show "Loop was not closed. Activity saved to profile history."

### `GET /api/v1/runs/{activity_id}/summary` — Stored Run Summary
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunSummaryScreen`
* **Response `(200 OK)`:** same shape as `/finish` (`badges_earned` is always `[]`). `can_claim_territory` is `false` once the run has been claimed.

---

## 5. Territory Claiming & Bases

### `POST /api/v1/territories/claim-from-run` — Acquire Base from Completed Run
* **Auth Required:** `Bearer <TOKEN>` (run owner)
* **Screen / Caller:** `RunSummaryScreen` ("Claim This Base") → `TerritoryClaimedScreen`
* **Notes:**
  * **Unfinished runs:** if the run is not finished yet, it is finalised first. `points` are used to recover offline runs, as for `/finish`.
  * **Server-computed geometry:** area and perimeter are computed geodesically by the server; `area_km2` / `perimeter_km` in the body are ignored.
  * **Result `status`:**
    * `success` — a new base was minted.
    * `fortified` — the loop matches (> 75% overlap) a base you already hold: its defense level rises and its record pace may improve.
    * `takeover` — the loop matches a rival's base and your pace beat its record: you now own it.
    * `challenge_failed` — the loop matches a rival's base but you were slower; the base stays with its owner.
  * The run can only be claimed once (any outcome).
* **Request Body:**
```json
{
  "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
  "territory_name": "Marina Circuit Alpha",
  "points": [
    { "latitude": 13.0827, "longitude": 80.2707, "timestamp": "2026-10-24T01:35:20Z" },
    { "latitude": 13.0850, "longitude": 80.2750, "timestamp": "2026-10-24T01:42:00Z" },
    { "latitude": 13.0810, "longitude": 80.2780, "timestamp": "2026-10-24T01:50:00Z" },
    { "latitude": 13.0827, "longitude": 80.2707, "timestamp": "2026-10-24T01:57:20Z" }
  ]
}
```
* **Response `(200 OK)`:**
```json
{
  "status": "success",
  "message": "You now hold Marina Circuit Alpha!",
  "territory_id": 42,
  "territory_name": "Marina Circuit Alpha",
  "area_km2": 0.4215,
  "perimeter_km": 3.42,
  "owner_username": "chennairunner",
  "owner_avatar": "https://assets.baserun.app/avatars/runner_01.png",
  "polygon_wkt": "POLYGON ((80.2707 13.0827, 80.275 13.085, 80.278 13.081, 80.2707 13.0827))",
  "defense_level": 1,
  "badge_earned": "First Conquest",
  "badges_earned": ["First Conquest"],
  "share_text": "I just claimed Marina Circuit Alpha (0.42 km²) on BASER! Can you beat my pace? #RunToClaim"
}
```
* **Errors:**
  * `400 "This run has already been claimed as a territory"`
  * `400 "Loop was not closed. ..."`
  * `400 "Territory is too large (max 10.0 km²)"`
  * `403`

### `GET /api/v1/bases?user_id={id}&limit=50&offset=0` — List Bases
* **Auth Required:** No
* **Notes:** Active bases, newest first. `area` is m², `perimeter` is m (legacy); `area_km2` / `perimeter_km` are also included.
* **Response `(200 OK)`:**
```json
{
  "total": 3,
  "bases": [
    {
      "id": 42,
      "name": "Marina Circuit Alpha",
      "user_id": "14",
      "club_id": 1,
      "status": "active",
      "area": 421500.0,
      "perimeter": 3420.0,
      "area_km2": 0.4215,
      "perimeter_km": 3.42,
      "owner_color": "#00E5FF",
      "polygon_wkt": "POLYGON ((80.2707 13.0827, ...))",
      "created_at": "2026-10-18T10:00:00Z"
    }
  ]
}
```
The same `bases` item shape is returned by:
* `GET /api/v1/bases/{base_id}` — a single item.
* `GET /users/{user_id}/territories` — a runner's held bases.
* `GET /api/v1/clubs/{club_id}/territories` — a club's bases.

### `GET /api/v1/bases/user/{user_id}/stats` — Territory Totals
* **Auth Required:** No
* **Response `(200 OK)`:**
```json
{
  "user_id": "14",
  "total_bases": 3,
  "total_area_sq_meters": 1142000.0,
  "total_perimeter_meters": 9120.0
}
```

### Legacy Endpoints
These are kept for older app builds:
* `POST /api/v1/bases/telemetry` — Bearer token required. Same as run telemetry with `activity_id` in the body; a new id is minted when it is omitted.
* `GET /api/v1/bases/telemetry/{activity_id}` — Bearer token required (run owner). Returns the run's breadcrumbs.
* `POST /api/v1/bases/claim` — Bearer token required. Mints a base directly from ≥ 5 points (100 m² – 0.5 km²) without a run.

---

## 6. Runner Profile & Badges

### `GET /users/me` (Alias: `GET /users/profile`) — Authenticated Runner Profile
* **Auth Required:** `Bearer <TOKEN>`
* **Screen / Caller:** `ProfileScreen`, `WelcomeScreen` (session check)
* **Notes:** `total_territories`, `total_km` and `total_runs` are legacy flat copies of `stats`.
* **Response `(200 OK)`:**
```json
{
  "id": 14,
  "username": "chennairunner",
  "email": "runner@example.com",
  "first_name": "Vikram",
  "last_name": "R",
  "avatar_url": "https://assets.baserun.app/avatars/runner_01.png",
  "bio": "Conquering Marina Beach one mile at a time.",
  "city": "Chennai, India",
  "territory_color": "#00E5FF",
  "total_territories": 4,
  "total_km": 128.4,
  "total_runs": 28,
  "club_id": 1,
  "club_name": "Marina Striders",
  "favorite_base": "Marina Circuit Alpha",
  "stats": {
    "total_runs": 28,
    "total_km": 128.4,
    "total_duration_hours": 14.3,
    "bases_claimed": 14,
    "bases_held": 4,
    "avg_pace": "5'42\""
  }
}
```

### `GET /users/{user_id}/profile` — Public Runner Profile
* **Auth Required:** Optional
* **Response `(200 OK)`:** same shape as `/users/me`; `email` is `null` unless you are viewing your own profile.

### `PUT /users/identity` (or `PUT /users/{user_id}` for your own id) — Update Identity
* **Auth Required:** `Bearer <TOKEN>`
* **Screen / Caller:** `ProfileSetupScreen`, `EditProfile`
* **Request Body:** every field is optional; only fields that are sent are changed.
  * `territory_color` — must be `#RRGGBB`.
  * `username` — same rules as registration.
  * `club_id` — a number joins that club; an explicit `null` leaves your club; omit it to keep your club.
  * A color change repaints all your bases immediately.
  * A club change moves your active bases to the new club.
```json
{
  "territory_color": "#00E5FF",
  "avatar_url": "https://assets.baserun.app/avatars/runner_01.png",
  "bio": "Conquering Marina Beach one mile at a time.",
  "club_id": 1
}
```
Other accepted fields: `username`, `first_name`, `last_name`, `city`.
* **Response `(200 OK)`:**
```json
{
  "user_id": "14",
  "username": "chennairunner",
  "territory_color": "#00E5FF",
  "avatar_url": "https://assets.baserun.app/avatars/runner_01.png",
  "bio": "Conquering Marina Beach one mile at a time.",
  "club_id": 1,
  "club_name": "Marina Striders",
  "message": "Identity updated successfully"
}
```
* **Errors:**
  * `400 "Username is already taken"`
  * `403` (`PUT /users/{user_id}` with another runner's id)
  * `404 "Club not found"`
  * `422` (invalid color or username)

### `GET /users/{user_id}/runs?limit=20&offset=0` — User Run History
* **Auth Required:** `Bearer <TOKEN>` (restricted to the account owner, otherwise `403`)
* **Screen / Caller:** `ProfileScreen` (Recent runs list)
* **Notes:** Newest first; `limit` max `100`. `badge` is `"Claimed Base"` for a minted, fortified or captured base, otherwise `"Open Run"`.
* **Response `(200 OK)`:**
```json
{
  "user_id": "14",
  "total_runs": 28,
  "limit": 20,
  "offset": 0,
  "runs": [
    {
      "id": 311,
      "activity_id": "act_5d0f2b6e9a1c4e7f3b8d2a10",
      "title": "Marina Beach Loop",
      "date": "2026-10-24T01:35:10Z",
      "distance_km": 3.42,
      "duration": "22:15",
      "duration_seconds": 1335,
      "pace": "6'30\"",
      "calories": 310,
      "status": "completed",
      "is_claimed": true,
      "badge": "Claimed Base"
    }
  ]
}
```

### `GET /users/{user_id}/achievements` — Unlocked Badges
* **Auth Required:** `Bearer <TOKEN>` (any runner may view any runner's badges)
* **Screen / Caller:** `ProfileScreen` (Awards section)
* **Badge catalog (`key`):** `first_run`, `first_conquest`, `5k_conqueror`, `10k_club`, `club_hero`, `hostile_takeover`. Badges are awarded automatically by `/finish` and `claim-from-run`.
* **Response `(200 OK)`:**
```json
{
  "user_id": "14",
  "total_badges": 2,
  "achievements": [
    {
      "id": 2,
      "key": "first_conquest",
      "title": "First Conquest",
      "description": "Claimed your first base territory",
      "badge_icon": "trophy",
      "unlocked_at": "2026-10-18T10:15:00Z"
    },
    {
      "id": 4,
      "key": "10k_club",
      "title": "10K Club",
      "description": "Ran over 10 km in a single session",
      "badge_icon": "military_tech",
      "unlocked_at": "2026-10-21T07:22:00Z"
    }
  ]
}
```

---

## 7. Clubs & Social Leaderboards

Member, territory, area and distance totals are aggregated live. A runner belongs to at most one club.

### `GET /api/v1/clubs` — List Running Clubs
* **Auth Required:** Optional (legacy `?user_id={id}` when no token)
* **Notes:** Sorted by territories held. `is_joined` is set for the caller's club.
* **Response `(200 OK)`:**
```json
{
  "total": 3,
  "clubs": [
    {
      "id": 3,
      "name": "Marina Striders",
      "tagline": "Ruling the coastline",
      "description": "Coastal runners patrolling the beaches from Lighthouse to Napier Bridge.",
      "avatar_url": "https://images.unsplash.com/photo-1476480862126-209bfaa8edc8?auto=format&fit=crop&w=150&q=80",
      "color": "#2196F3",
      "member_count": 142,
      "territory_count": 18,
      "total_area_km2": 7.45,
      "total_km": 1150.2,
      "is_joined": true
    }
  ]
}
```

### `GET /api/v1/clubs/{club_id}` — Club Card
* **Auth Required:** Optional
* **Response `(200 OK)`:** a single club item (same shape as above).

### `POST /api/v1/clubs/{club_id}/join` — Join or Switch Club
* **Auth Required:** `Bearer <TOKEN>`
* **Notes:** Joining your current club changes nothing. Switching clubs moves your active bases to the new club.
* **Response `(200 OK)`:**
```json
{
  "status": "success",
  "message": "Successfully joined Marina Striders!",
  "club_id": 3,
  "club_name": "Marina Striders",
  "territories_transferred": 2
}
```
* **Errors:** `404 "Club not found"`.

### `POST /api/v1/clubs/{club_id}/leave` — Leave Club ("Solo Rogue")
* **Auth Required:** `Bearer <TOKEN>`
* **Response `(200 OK)`:**
```json
{
  "status": "success",
  "message": "You left the club",
  "club_id": null,
  "club_name": null,
  "territories_transferred": 2
}
```
* **Errors:** `400 "You are not a member of this club"`.

### `GET /api/v1/clubs/{club_id}/leaderboard?limit=50` — Club Leaderboard
* **Auth Required:** Optional (legacy `?current_user_id={id}` when no token)
* **Notes:** Members are ranked by bases held for the club, then total km. Rank #1–3 get gold, silver and bronze in the UI. `username` / `avatar_url` / `territories` are legacy aliases.
* **Response `(200 OK)`:**
```json
{
  "club_id": 3,
  "club_name": "Marina Striders",
  "total_members": 142,
  "total_territories": 18,
  "leaderboard": [
    {
      "rank": 1,
      "user_id": "14",
      "username": "chennairunner",
      "runner_name": "chennairunner",
      "avatar_url": "https://assets.baserun.app/avatars/runner_01.png",
      "runner_avatar": "https://assets.baserun.app/avatars/runner_01.png",
      "territories": 4,
      "territories_held": 4,
      "total_km": 42.8,
      "avg_pace": "5'12\"",
      "is_current_user": true
    }
  ]
}
```

### `GET /api/v1/clubs/{club_id}/territories` — Club Bases
* **Auth Required:** No
* **Response `(200 OK)`:** `{ "total": n, "bases": [ ... ] }` (bases item shape from section 5).

---

## 8. Migration Notes for Existing App Builds

| Area | Before | Now |
| :--- | :--- | :--- |
| Run status | `in_progress` | `recording` (also `paused`, `completed`) |
| `/runs/{id}/live`, `/path`, `/summary` | Public | Bearer token, run owner only |
| `/api/v1/bases/telemetry/{id}` | Public | Bearer token, run owner only |
| `/users/{id}/achievements` | Public, seeded starter badges on read | Bearer token; badges only from real milestones |
| Login with unknown username | `404 user not found` | `401 invalid username or password` |
| Social login | Trusted `email` from the body | Verifies the Google/Apple ID token; uses the verified email |
| `user_id` in auth responses | String | Integer |
| App config `app_name` | `Run to Claim` | `BASER`; new `current_season`, `min_version`, `maintenance_mode`, `server_time` |
| Claim `area_km2` / `perimeter_km` in body | Used when the server got 0 | Always ignored |
| `/users/{id}/profile` | Exposed email | Email only for the profile owner |
| Errors | `{"detail": ...}` | `{"detail": ..., "code": ...}` |
