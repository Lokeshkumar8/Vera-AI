# Vera Message Engine

A deterministic, context-grounded message engine for the magicpin AI challenge.

## Approach

The engine uses a trigger router with category, merchant, customer and conversation context. High-intent triggers switch directly to artifact/action mode; performance triggers use the changed metric and merchant state; customer triggers respect consent and relationship state. Category data supplies voice, offers and peer benchmarks. No external calls or randomness are used, so identical inputs produce identical outputs.

The HTTP service stores versioned contexts and supports `/v1/context`, `/v1/tick`, `/v1/reply`, `/v1/healthz`, and `/v1/metadata`. Reply handling explicitly detects opt-out, repeated auto-replies, intent transitions, time requests and off-topic questions.

## Tradeoffs

The first version favors deterministic grounding and operational reliability over free-form generation. This avoids fabricated facts and keeps latency predictable. The main area for further improvement is expanding category-specific composition patterns for less richly populated future trigger payloads.

## Run

```bash
python3 server.py
```

Then point the supplied judge simulator at `http://localhost:8080`.
