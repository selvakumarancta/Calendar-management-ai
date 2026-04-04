# Billing & SaaS Guide

> Subscription plans, Stripe integration, usage tracking, and quota management.

---

## Table of Contents

1. [Plan Tiers](#plan-tiers)
2. [Plan Definitions](#plan-definitions)
3. [Usage Tracking](#usage-tracking)
4. [Stripe Integration](#stripe-integration)
5. [Quota Enforcement](#quota-enforcement)
6. [Revenue Projections](#revenue-projections)

---

## Plan Tiers

Four subscription tiers, designed for progressive upsell:

| Plan | Price/mo | Requests/mo | Calendars | LLM Models | Target User |
|---|---|---|---|---|---|
| **Free** | $0 | 50 | 1 | Fast only | Individual tryout |
| **Pro** | $9.99 | 500 | 5 | Fast + Primary | Power user |
| **Business** | $29.99 | 2,000 | 20 | Fast + Primary | Teams |
| **Enterprise** | Custom | 100,000 | Unlimited | All | Organizations |

### Model Access by Plan

| Plan | Anthropic Models | OpenAI Models |
|---|---|---|
| Free | claude-haiku-3-20250414 | gpt-4o-mini |
| Pro | claude-haiku + **claude-sonnet-4-20250514** | gpt-4o-mini + **gpt-4o** |
| Business | claude-haiku + **claude-sonnet-4-20250514** | gpt-4o-mini + **gpt-4o** |
| Enterprise | All models | All models |

Free users are restricted to fast (cheap) models. This is enforced in `ChatService._select_model()` — if a user's plan doesn't allow the primary model, the request is automatically downgraded.

---

## Plan Definitions

Plans are defined as immutable dataclasses in `src/billing/plans.py`:

```python
@dataclass(frozen=True)
class PlanDefinition:
    tier: PlanTier            # FREE, PRO, BUSINESS, ENTERPRISE
    name: str                 # Display name
    monthly_price_usd: float  # Stripe price
    monthly_request_limit: int
    max_calendars: int
    model_access: list[str]   # Which LLM models are allowed
    features: list[str]       # Feature flags

    @property
    def allows_primary_model(self) -> bool:
        """Check if plan allows expensive models (any provider)."""
        primary_models = {"gpt-4o", "claude-sonnet-4-20250514"}
        return bool(primary_models & set(self.model_access))
```

### Feature Flags

| Feature | Free | Pro | Business | Enterprise |
|---|---|---|---|---|
| `basic_crud` | ✅ | ✅ | ✅ | ✅ |
| `list_events` | ✅ | ✅ | ✅ | ✅ |
| `single_calendar` | ✅ | ✅ | ✅ | ✅ |
| `smart_scheduling` | ❌ | ✅ | ✅ | ✅ |
| `conflict_detection` | ❌ | ✅ | ✅ | ✅ |
| `multi_calendar` | ❌ | ✅ | ✅ | ✅ |
| `free_slot_finder` | ❌ | ✅ | ✅ | ✅ |
| `team_calendars` | ❌ | ❌ | ✅ | ✅ |
| `api_access` | ❌ | ❌ | ✅ | ✅ |
| `priority_routing` | ❌ | ❌ | ✅ | ✅ |
| `webhook_notifications` | ❌ | ❌ | ✅ | ✅ |
| `dedicated_instance` | ❌ | ❌ | ❌ | ✅ |
| `sla` | ❌ | ❌ | ❌ | ✅ |

---

## Usage Tracking

### Architecture

Usage is tracked per-tenant using Redis counters with monthly auto-reset.

```
User sends message
   │
   ▼
ChatService.handle_message()
   │
   ├── 1. Check quota: usage_tracker.is_within_quota(user_id, limit)
   │      └── Redis GET → monthly_requests:{user_id}:{YYYY-MM}
   │
   ├── ... (process request) ...
   │
   └── 7. Record usage: usage_tracker.record_request(user_id)
          └── Redis INCR → monthly_requests:{user_id}:{YYYY-MM}
```

### Implementation

`src/billing/usage_tracker.py` — `RedisUsageTracker`:

| Method | Description |
|---|---|
| `record_request(user_id)` | Increment monthly counter (auto-expiring key) |
| `get_usage(user_id)` | Get current month's request count |
| `is_within_quota(user_id, limit)` | Check if under monthly limit |

Redis key format: `monthly_requests:{user_id}:{YYYY-MM}`
- Auto-expires at end of month
- No cron jobs needed for reset

### Viewing Usage

Users can see their usage via `GET /api/v1/auth/me`:

```json
{
  "plan": "pro",
  "monthly_requests_used": 142,
  "monthly_request_limit": 500
}
```

---

## Stripe Integration

### Setup

`src/billing/stripe_service.py` provides:

| Method | Description |
|---|---|
| `create_customer(user)` | Create Stripe customer for new user |
| `create_subscription(user, plan)` | Start a subscription |
| `cancel_subscription(user)` | Cancel current subscription |
| `change_plan(user, new_plan)` | Upgrade/downgrade |
| `handle_webhook(payload, signature)` | Process Stripe webhooks |

### Configuration

```bash
# .env
STRIPE_SECRET_KEY=sk_test_...           # Stripe API key
STRIPE_WEBHOOK_SECRET=whsec_...         # Webhook signature verification
STRIPE_PRICE_PRO=price_...              # Stripe Price ID for Pro plan
STRIPE_PRICE_BUSINESS=price_...         # Stripe Price ID for Business plan
```

### Setting Up Stripe Products

1. Create products in Stripe Dashboard (or via API):

```bash
# Pro Plan
stripe products create --name="Calendar Agent Pro" --description="500 requests/month"
stripe prices create --product=prod_xxx --unit-amount=999 --currency=usd --recurring[interval]=month

# Business Plan
stripe products create --name="Calendar Agent Business" --description="2000 requests/month"
stripe prices create --product=prod_yyy --unit-amount=2999 --currency=usd --recurring[interval]=month
```

2. Copy price IDs to `.env`:
```bash
STRIPE_PRICE_PRO=price_xxx
STRIPE_PRICE_BUSINESS=price_yyy
```

3. Set up webhook endpoint in Stripe Dashboard:
   - URL: `https://your-domain.com/api/v1/billing/webhook`
   - Events: `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`

### Webhook Events

| Event | Action |
|---|---|
| `customer.subscription.created` | Activate plan for user |
| `customer.subscription.updated` | Update plan tier |
| `customer.subscription.deleted` | Downgrade to Free |
| `invoice.paid` | Record successful payment |
| `invoice.payment_failed` | Send notification, retry or downgrade |

### Subscription Flow

```
1. User signs up → Free plan (no Stripe needed)
2. User clicks "Upgrade to Pro"
   → Frontend calls Stripe Checkout / Elements
   → Stripe creates subscription
   → Webhook fires: customer.subscription.created
   → Backend updates user.plan = PRO
3. Monthly renewal
   → Stripe charges automatically
   → invoice.paid webhook
4. User cancels
   → customer.subscription.deleted webhook
   → Backend downgrades to Free at end of billing period
```

---

## Quota Enforcement

### Where Quotas Are Checked

```
ChatService.handle_message()
   │
   ├── Step 1: Check quota
   │   └── is_within_quota(user_id, user.get_request_limit())
   │       ├── True  → Continue processing
   │       └── False → Raise QuotaExceededError (HTTP 429)
   │
   └── Step 7: Record usage (after successful response)
       └── record_request(user_id)
```

### Quota Exceeded Response

```json
HTTP 429 Too Many Requests

{
  "detail": "Monthly request quota exceeded. Limit: 50. Please upgrade your plan."
}
```

### Plan-Based Model Gating

Even if a user has quota remaining, the model they can use depends on their plan:

```python
# User entity
def can_use_primary_model(self) -> bool:
    return self.plan in (PRO, BUSINESS, ENTERPRISE)
```

If a Free user's request is classified as COMPLEX (which normally routes to the primary model), the system falls back to the fast model instead of rejecting the request.

---

## Revenue Projections

### Unit Economics

**Cost per request (with optimizations):**

| Request Type | % of Traffic | LLM Cost |
|---|---|---|
| Deterministic | 35% | $0.000 |
| Cache hit | 20% | $0.000 |
| Fast model | 36% | ~$0.0003 |
| Primary model | 9% | ~$0.0053 |
| **Weighted average** | 100% | **~$0.0006/request** |

### Revenue vs. Cost

| Plan | Revenue/user/mo | Avg requests/mo | LLM cost/user/mo | Margin |
|---|---|---|---|---|
| Free | $0.00 | ~20 | $0.01 | -$0.01 |
| Pro | $9.99 | ~200 | $0.12 | $9.87 (98.8%) |
| Business | $29.99 | ~800 | $0.48 | $29.51 (98.4%) |
| Enterprise | Custom | ~5,000 | $3.00 | Depends |

### Breakeven Analysis

Infrastructure costs (estimated):

| Item | Monthly Cost |
|---|---|
| Cloud hosting (small) | ~$30 |
| PostgreSQL (managed) | ~$15 |
| Redis (managed) | ~$10 |
| Domain + SSL | ~$5 |
| **Total infrastructure** | **~$60/mo** |

**Breakeven:** ~7 Pro users or ~3 Business users cover infrastructure.

### Growth Scenarios

| Scenario | Users | MRR | LLM Cost | Infra | Profit |
|---|---|---|---|---|---|
| Launch (3 mo) | 100 Free + 10 Pro | $100 | $2 | $60 | $38 |
| Growth (6 mo) | 500 Free + 50 Pro + 5 Biz | $650 | $15 | $100 | $535 |
| Scale (12 mo) | 2K Free + 200 Pro + 30 Biz | $2,900 | $80 | $200 | $2,620 |
