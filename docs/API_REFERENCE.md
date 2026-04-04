# API Reference

> Complete REST API and WebSocket documentation for the Calendar Management Agent.

---

## Table of Contents

1. [Base URL](#base-url)
2. [Authentication](#authentication)
3. [Health Endpoints](#health-endpoints)
4. [Auth Endpoints](#auth-endpoints)
5. [Chat Endpoints](#chat-endpoints)
6. [Calendar Endpoints](#calendar-endpoints)
7. [WebSocket](#websocket)
8. [Error Responses](#error-responses)
9. [Rate Limiting](#rate-limiting)

---

## Base URL

```
Development: http://localhost:8000
Production:  https://your-domain.com
```

Interactive API docs (development only):
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## Authentication

Most endpoints require a JWT bearer token obtained via the Google OAuth flow.

```
Authorization: Bearer <jwt_access_token>
```

### Token Lifecycle

1. User starts OAuth: `GET /api/v1/auth/google/login`
2. Google redirects back: `GET /api/v1/auth/google/callback?code=...`
3. Server returns JWT access + refresh tokens
4. Client includes `Authorization: Bearer <token>` on subsequent requests
5. When expired, refresh via standard JWT refresh flow

### JWT Configuration

| Setting | Default | Description |
|---|---|---|
| `JWT_ALGORITHM` | HS256 | Signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 30 | Access token TTL |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | 7 | Refresh token TTL |

---

## Health Endpoints

### `GET /health`

Health check for load balancers and monitoring.

**Auth required:** No

**Response:**

```json
{
  "status": "healthy",
  "service": "calendar-agent"
}
```

---

### `GET /ready`

Readiness probe — verifies database and cache connectivity.

**Auth required:** No

**Response:**

```json
{
  "status": "ready"
}
```

---

## Auth Endpoints

### `GET /api/v1/auth/google/login`

Initiate Google OAuth2 flow. Returns the Google authorization URL for the client to redirect to.

**Auth required:** No

**Response:**

```json
{
  "authorization_url": "https://accounts.google.com/o/oauth2/auth?client_id=...&redirect_uri=...&scope=..."
}
```

---

### `GET /api/v1/auth/google/callback`

Handle Google OAuth2 callback. Exchanges the authorization code for tokens, creates/updates the user, and returns JWT tokens.

**Auth required:** No

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `code` | string | Yes | Authorization code from Google |
| `state` | string | No | CSRF protection state parameter |

**Response: `LoginResponseDTO`**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 1800
}
```

---

### `GET /api/v1/auth/me`

Get the current authenticated user's profile and usage statistics.

**Auth required:** Yes (Bearer token)

**Response: `UserProfileDTO`**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "name": "John Doe",
  "timezone": "America/New_York",
  "plan": "pro",
  "monthly_requests_used": 142,
  "monthly_request_limit": 500
}
```

---

## Chat Endpoints

### `POST /api/v1/chat/`

Send a natural language message to the AI Calendar Agent. The agent interprets the request, manages calendar operations, and returns a response.

**Auth required:** Yes (Bearer token)

**Request: `ChatRequestDTO`**

```json
{
  "message": "Schedule a meeting with Alice tomorrow at 2pm for 1 hour",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440001",
  "user_timezone": "America/New_York"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `message` | string | Yes | Natural language instruction |
| `conversation_id` | UUID | No | Continue existing conversation (omit to start new) |
| `user_timezone` | string | No | IANA timezone (default: UTC) |

**Response: `ChatResponseDTO`**

```json
{
  "message": "I've scheduled a meeting with Alice tomorrow (March 31) from 2:00 PM to 3:00 PM ET. I also set a 15-minute reminder.",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

**Processing Pipeline:**

1. **Quota check** — Is user within monthly request limit?
2. **Cache check** — Has this exact query been answered recently?
3. **Intent routing** — Classify complexity (deterministic / simple / medium / complex)
4. **Model selection** — Route to fast or primary model based on complexity
5. **Agent execution** — LangGraph ReAct loop with tool calling
6. **Usage tracking** — Record request for billing
7. **Response caching** — Cache for future identical queries (5 min TTL)

**Example Requests:**

```bash
# Simple query (uses fast model)
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What meetings do I have today?"}'

# Complex query (uses primary model)
curl -X POST http://localhost:8000/api/v1/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Reorganize my week to avoid all conflicts"}'
```

---

## Calendar Endpoints

Direct CRUD endpoints that bypass the AI agent. Useful for programmatic access.

### `GET /api/v1/calendar/events`

List calendar events within a date range.

**Auth required:** Yes

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `start` | string (ISO 8601) | Yes | Start of date range |
| `end` | string (ISO 8601) | Yes | End of date range |

**Response: `list[EventResponseDTO]`**

```json
[
  {
    "id": "google-event-id-123",
    "title": "Team Standup",
    "start_time": "2026-03-30T09:00:00Z",
    "end_time": "2026-03-30T09:30:00Z",
    "duration_minutes": 30
  },
  {
    "id": "google-event-id-456",
    "title": "1:1 with Manager",
    "start_time": "2026-03-30T14:00:00Z",
    "end_time": "2026-03-30T14:30:00Z",
    "duration_minutes": 30
  }
]
```

**Example:**

```bash
curl "http://localhost:8000/api/v1/calendar/events?start=2026-03-30T00:00:00Z&end=2026-03-31T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN"
```

---

### `POST /api/v1/calendar/events`

Create a new calendar event.

**Auth required:** Yes

**Request: `CreateEventDTO`**

```json
{
  "title": "Product Review",
  "start_time": "2026-03-31T15:00:00Z",
  "end_time": "2026-03-31T16:00:00Z",
  "description": "Q1 product review with stakeholders",
  "location": "Conference Room B",
  "attendees": ["alice@company.com", "bob@company.com"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | string | Yes | Event title |
| `start_time` | ISO 8601 | Yes | Start time |
| `end_time` | ISO 8601 | Yes | End time |
| `description` | string | No | Event description |
| `location` | string | No | Location |
| `attendees` | list[string] | No | Email addresses |

**Response: `EventResponseDTO`** (HTTP 201)

```json
{
  "id": "google-event-id-789",
  "title": "Product Review",
  "start_time": "2026-03-31T15:00:00Z",
  "end_time": "2026-03-31T16:00:00Z",
  "duration_minutes": 60
}
```

---

### `DELETE /api/v1/calendar/events/{event_id}`

Delete a calendar event.

**Auth required:** Yes

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `event_id` | string | Google Calendar event ID |

**Response:** HTTP 204 No Content

**Example:**

```bash
curl -X DELETE http://localhost:8000/api/v1/calendar/events/google-event-id-123 \
  -H "Authorization: Bearer $TOKEN"
```

---

## WebSocket

### `WS /ws/chat`

Real-time streaming chat with the AI agent. Tokens are streamed as they're generated.

**Connection:**

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/chat");

// Send a message
ws.send(JSON.stringify({
  token: "jwt-access-token",
  message: "What's my schedule today?",
  conversation_id: null
}));

// Receive streamed response
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === "token") {
    // Append token to display
    process.stdout.write(data.content);
  } else if (data.type === "done") {
    // Response complete
    console.log("\nConversation ID:", data.conversation_id);
  } else if (data.type === "error") {
    console.error("Error:", data.message);
  }
};
```

**Message Types (server → client):**

| Type | Fields | Description |
|---|---|---|
| `token` | `content` | Single token of the response |
| `tool_call` | `tool`, `args` | Agent is calling a tool |
| `tool_result` | `tool`, `result` | Tool returned a result |
| `done` | `conversation_id`, `tokens_used` | Response complete |
| `error` | `message` | Error occurred |

---

## Error Responses

All errors follow a consistent format:

```json
{
  "detail": "Human-readable error message"
}
```

### HTTP Status Codes

| Code | Meaning | Example Trigger |
|---|---|---|
| 400 | Bad Request | Invalid JSON, missing required fields |
| 401 | Unauthorized | Missing or expired JWT token |
| 403 | Forbidden | Insufficient plan permissions |
| 404 | Not Found | Event ID doesn't exist |
| 409 | Conflict | Scheduling overlap detected |
| 422 | Unprocessable Entity | Invalid time range, past event |
| 429 | Too Many Requests | Monthly quota exceeded or rate limit hit |
| 502 | Bad Gateway | Google Calendar API failure |

### Domain Exception Mapping

| Domain Exception | HTTP Status |
|---|---|
| `EventNotFoundError` | 404 |
| `EventConflictError` | 409 |
| `EventInPastError` | 422 |
| `InvalidTimeRangeError` | 422 |
| `QuotaExceededError` | 429 |
| `AuthenticationError` | 401 |
| `AuthorizationError` | 403 |
| `CalendarProviderError` | 502 |

---

## Rate Limiting

Built-in per-IP rate limiting via `RateLimiterMiddleware`:

| Scope | Limit | Window |
|---|---|---|
| Per IP | 60 requests | 1 minute |
| Per user (chat) | Based on plan | Monthly |

When rate limited, the response is:

```
HTTP 429 Too Many Requests
Retry-After: 30
```
