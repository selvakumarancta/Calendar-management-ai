# LLM Providers Guide

> Multi-provider LLM support: Anthropic Claude, OpenAI GPT, and extensible for future providers.

---

## Table of Contents

1. [Overview](#overview)
2. [Supported Providers](#supported-providers)
3. [Configuration](#configuration)
4. [Architecture: Factory + Strategy Pattern](#architecture-factory--strategy-pattern)
5. [How Model Selection Works](#how-model-selection-works)
6. [Adding a New Provider](#adding-a-new-provider)
7. [Model Pricing Reference](#model-pricing-reference)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The Calendar Agent supports **multiple LLM providers** through a **Factory + Strategy** pattern. You only need one API key to get started — set the provider in `.env` and the entire stack (agent graph, cost routing, billing, token tracking) adapts automatically.

**Key design decisions:**
- Provider selection is a **deployment-time configuration**, not a code change
- All providers implement the same `LLMPort` interface (domain port)
- The LangGraph agent dynamically creates the correct LangChain model at runtime
- Token pricing is tracked per-provider for accurate cost reporting

---

## Supported Providers

### Anthropic (Claude)

| Model | Tier | Use Case | Cost (per 1M tokens) |
|---|---|---|---|
| `claude-haiku-3-20250414` | Fast | Simple queries, CRUD operations | $0.25 input / $1.25 output |
| `claude-sonnet-4-20250514` | Primary | Complex reasoning, scheduling optimization | $3.00 input / $15.00 output |

### OpenAI (GPT)

| Model | Tier | Use Case | Cost (per 1M tokens) |
|---|---|---|---|
| `gpt-4o-mini` | Fast | Simple queries, CRUD operations | $0.15 input / $0.60 output |
| `gpt-4o` | Primary | Complex reasoning, scheduling optimization | $2.50 input / $10.00 output |

---

## Configuration

### Basic Setup

Edit `.env`:

```bash
# Choose your provider: anthropic | openai
LLM_PROVIDER=anthropic

# --- Anthropic (if LLM_PROVIDER=anthropic) ---
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_MODEL_PRIMARY=claude-sonnet-4-20250514
ANTHROPIC_MODEL_FAST=claude-haiku-3-20250414

# --- OpenAI (if LLM_PROVIDER=openai) ---
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL_PRIMARY=gpt-4o
OPENAI_MODEL_FAST=gpt-4o-mini

# --- Shared settings (apply to all providers) ---
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=4096
```

### Switching Providers

To switch from Anthropic to OpenAI (or vice versa), change **one variable**:

```bash
# Before (Claude)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# After (GPT)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

No code changes needed. The entire stack adapts:
- ✅ LLM adapter (HTTP client + API format)
- ✅ LangGraph agent (correct LangChain model class)
- ✅ Cost-tier routing (correct model names for fast/primary)
- ✅ Token usage tracking (correct pricing)
- ✅ Billing plan model lists

### Settings Properties

The `Settings` class provides `active_*` properties that resolve based on `LLM_PROVIDER`:

```python
settings.llm_provider        # → "anthropic"
settings.active_api_key       # → ANTHROPIC_API_KEY value
settings.active_model_primary # → "claude-sonnet-4-20250514"
settings.active_model_fast    # → "claude-haiku-3-20250414"
```

These properties are used throughout the stack so individual components never need to know which provider is active.

---

## Architecture: Factory + Strategy Pattern

### Files Involved

```
src/
├── domain/interfaces/llm.py           # LLMPort (abstract interface)
├── infrastructure/llm/
│   ├── factory.py                     # Factory: create_llm_adapter(), create_langchain_chat_model()
│   ├── openai_adapter.py              # OpenAIAdapter (implements LLMPort)
│   └── anthropic_adapter.py           # AnthropicAdapter (implements LLMPort)
├── config/
│   ├── settings.py                    # LLM provider config + active_* properties
│   └── container.py                   # Wires factory into DI
└── agent/graph.py                     # Uses factory for LangChain models
```

### Flow Diagram

```
.env (LLM_PROVIDER=anthropic)
   │
   ▼
Settings.llm_provider → "anthropic"
Settings.active_api_key → sk-ant-...
   │
   ▼
Container.llm_adapter()
   │
   ▼
factory.create_llm_adapter(provider="anthropic", api_key=...)
   │
   ▼
AnthropicAdapter(api_key=..., model=...)   ← implements LLMPort
   │
   ▼
Injected into ChatService, CalendarService, etc.
```

For the LangGraph agent (which needs LangChain models):

```
CalendarAgentGraph._reason_node()
   │
   ▼
factory.create_langchain_chat_model(provider="anthropic", ...)
   │
   ▼
ChatAnthropic(model="claude-sonnet-4-20250514", ...)   ← LangChain model
   │
   ▼
llm.bind_tools(tools) → Agent reasoning
```

### LLMPort Interface

Every provider adapter must implement:

```python
class LLMPort(abc.ABC):
    @abc.abstractmethod
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> dict: ...

    @abc.abstractmethod
    async def generate_embedding(self, text: str) -> list[float]: ...

    @abc.abstractmethod
    def get_last_token_usage(self) -> TokenUsage | None: ...
```

### Factory Functions

```python
# Create a raw LLM adapter (for direct API calls)
adapter = create_llm_adapter(
    provider="anthropic",      # or "openai"
    api_key="sk-ant-...",
    default_model="claude-haiku-3-20250414",
    temperature=0.1,
    max_tokens=4096,
)

# Create a LangChain chat model (for the LangGraph agent)
chat_model = create_langchain_chat_model(
    provider="anthropic",
    api_key="sk-ant-...",
    model="claude-sonnet-4-20250514",
    temperature=0.1,
)
```

---

## How Model Selection Works

The system uses **tiered model routing** to minimize costs:

```
User Message
   │
   ▼
IntentRouter.classify(message)
   │
   ├── DETERMINISTIC  →  No LLM call (regex matched, direct API)
   ├── SIMPLE         →  Fast model (claude-haiku / gpt-4o-mini)
   ├── MEDIUM         →  Fast model (claude-haiku / gpt-4o-mini)
   └── COMPLEX        →  Primary model (claude-sonnet / gpt-4o)
```

Model names are resolved from `Settings.active_model_fast` and `Settings.active_model_primary`, which are provider-aware:

```python
# In ChatService._select_model():
model_map = {
    RequestComplexity.SIMPLE:  self._model_fast,     # e.g. "claude-haiku-3-20250414"
    RequestComplexity.MEDIUM:  self._model_fast,
    RequestComplexity.COMPLEX: self._model_primary,   # e.g. "claude-sonnet-4-20250514"
}
```

---

## Adding a New Provider

Adding a new LLM provider (e.g., Google Gemini) requires changes in **4 files** with **zero changes to domain logic**:

### Step 1: Create the adapter

Create `src/infrastructure/llm/gemini_adapter.py`:

```python
"""Gemini LLM Adapter — implements LLMPort for Google AI."""

from __future__ import annotations
from src.domain.interfaces.llm import LLMPort
from src.domain.value_objects import TokenUsage


class GeminiAdapter(LLMPort):
    def __init__(self, api_key: str, default_model: str = "gemini-2.0-flash", ...) -> None:
        self._api_key = api_key
        self._default_model = default_model
        # ... initialize Google AI client

    async def chat_completion(self, messages, model=None, **kwargs) -> dict:
        # Convert messages to Gemini format, call API, return response
        ...

    async def generate_embedding(self, text: str) -> list[float]:
        # Use Gemini embedding model
        ...

    def get_last_token_usage(self) -> TokenUsage | None:
        return self._last_usage
```

### Step 2: Register in factory

Edit `src/infrastructure/llm/factory.py`:

```python
class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"          # ← Add enum value

MODEL_TIERS["gemini"] = {       # ← Add model tier mapping
    "fast": "gemini-2.0-flash",
    "primary": "gemini-2.5-pro",
}


def create_llm_adapter(provider, api_key, ...):
    ...
    elif provider_lower == LLMProvider.GEMINI:         # ← Add branch
        from src.infrastructure.llm.gemini_adapter import GeminiAdapter
        return GeminiAdapter(api_key=api_key, ...)


def create_langchain_chat_model(provider, api_key, model, ...):
    ...
    elif provider_lower == LLMProvider.GEMINI:         # ← Add branch
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, ...)
```

### Step 3: Add settings

Edit `src/config/settings.py`:

```python
# --- Gemini ---
gemini_api_key: str = ""
gemini_model_primary: str = "gemini-2.5-pro"
gemini_model_fast: str = "gemini-2.0-flash"

@property
def active_api_key(self) -> str:
    if self.llm_provider == "gemini":
        return self.gemini_api_key
    ...

@property
def active_model_primary(self) -> str:
    if self.llm_provider == "gemini":
        return self.gemini_model_primary
    ...
```

### Step 4: Add pricing

Edit `src/domain/value_objects/__init__.py` in `TokenUsage.estimated_cost_usd`:

```python
rates = {
    ...
    "gemini-2.5-pro": (1.25 / 1_000_000, 10.00 / 1_000_000),
    "gemini-2.0-flash": (0.10 / 1_000_000, 0.40 / 1_000_000),
}
```

### Step 5: Install dependency

Add to `pyproject.toml`:
```toml
"langchain-google-genai>=2.0,<3.0",
```

### That's it!

Set `LLM_PROVIDER=gemini` in `.env` and the entire stack works. No changes needed in:
- ❌ Domain layer
- ❌ Application services
- ❌ Agent graph logic
- ❌ API endpoints
- ❌ DI container (auto-resolves via factory)

---

## Model Pricing Reference

### Current Pricing (as of March 2026)

| Provider | Model | Input (per 1M tokens) | Output (per 1M tokens) |
|---|---|---|---|
| Anthropic | claude-haiku-3-20250414 | $0.25 | $1.25 |
| Anthropic | claude-sonnet-4-20250514 | $3.00 | $15.00 |
| OpenAI | gpt-4o-mini | $0.15 | $0.60 |
| OpenAI | gpt-4o | $2.50 | $10.00 |

### Cost Comparison for Typical Usage

Assuming ~500 requests/month, average 1,000 input + 500 output tokens per request:

| Scenario | Anthropic (Haiku) | OpenAI (Mini) | Anthropic (Sonnet) | OpenAI (4o) |
|---|---|---|---|---|
| Input cost | $0.125 | $0.075 | $1.50 | $1.25 |
| Output cost | $0.313 | $0.150 | $3.75 | $2.50 |
| **Total/month** | **$0.44** | **$0.23** | **$5.25** | **$3.75** |

With tiered routing (80% fast / 20% primary):

| Mix | Anthropic | OpenAI |
|---|---|---|
| 400 fast + 100 primary | $1.40/mo | $0.93/mo |

---

## Troubleshooting

### "Unsupported LLM provider" error

```
ValueError: Unsupported LLM provider: 'claude'. Supported: anthropic, openai
```

**Fix:** Use the provider name, not the model name. Set `LLM_PROVIDER=anthropic` (not `LLM_PROVIDER=claude`).

### Empty API key

```
Settings.active_api_key returns ""
```

**Fix:** Ensure the correct key is set for your provider:
- `LLM_PROVIDER=anthropic` → requires `ANTHROPIC_API_KEY`
- `LLM_PROVIDER=openai` → requires `OPENAI_API_KEY`

### Model not allowed by plan

If a user on the Free plan tries to use a primary model, the `ChatService` will downgrade to the fast model automatically. Primary models (`claude-sonnet-4-20250514`, `gpt-4o`) are only available on Pro+ plans.

### Testing without a real API key

Unit tests mock the LLM layer and don't need real API keys. For integration tests, set test keys in your CI environment secrets.
