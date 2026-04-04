<!-- Custom instructions for GitHub Copilot in this workspace -->

## Project: Calendar Management Agent (SaaS)

### Architecture
- **Clean/Hexagonal Architecture** with Domain-Driven Design
- Domain layer has ZERO external dependencies — only Python stdlib
- All external integrations go through Ports (interfaces) and Adapters
- Dependency Injection via Container (composition root pattern)

### Layer Rules
1. **domain/** — Pure business logic. No imports from infrastructure, api, or agent
2. **application/** — Orchestrates domain logic. Can import domain, NOT infrastructure directly
3. **infrastructure/** — Implements domain ports. Can import domain interfaces
4. **agent/** — AI agent logic. Can import application services
5. **api/** — HTTP/WS interface. Thin layer delegating to application services
6. **billing/** — SaaS billing concerns. Uses domain interfaces for usage tracking
7. **config/** — Settings and DI container. The only place that wires everything

### Conventions
- Python 3.11+ with `from __future__ import annotations`
- Async-first (`async/await` everywhere)
- Pydantic for DTOs and settings
- SQLAlchemy 2.0 async ORM
- Type hints on all functions
- Tests marked with `@pytest.mark.unit` or `@pytest.mark.integration`

### Cost Optimization
- Use `gpt-4o-mini` as default model, `gpt-4o` only for complex reasoning
- Cache aggressively (Redis, 2-5 min TTL)
- Use deterministic shortcuts (regex patterns) to bypass LLM when possible
- Keep prompts token-efficient — compress event data
