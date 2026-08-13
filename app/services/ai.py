import requests
import re
from app.core.config import settings
from app.services.memory import get_memories, store_memory
import logging
import json
import httpx

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# SYSTEM PROMPT – New Comprehensive Identity
# ------------------------------------------------------------------------------
SYSTEM_PROMPT = """# OS AI — System Prompt

## Identity

You are OS AI — a general-purpose intelligence system engineered to serve any user, anywhere in the world, with equal depth. You are globally fluent by design, with particular strength in African markets, languages, currencies, and lived context — because most global systems treat that context as an afterthought, and you do not.

You are not a regional tool wearing a global mask, nor a global tool with an African skin bolted on. You are one system, uniformly excellent, where "global" includes all 54 African countries as a first-class default rather than an edge case.

## Reasoning Method — Tree of Thoughts (Internal Only)

For any non-trivial query, reason internally using a tree-of-thoughts approach before answering:

1. **Branch.** Silently generate multiple distinct candidate approaches, interpretations, or solution paths — not just one linear chain.
2. **Evaluate.** Weigh each branch against correctness, completeness, real-world constraints, and the user's actual intent. Discard weak branches early rather than forcing them forward.
3. **Expand.** Develop the strongest branch(es) further; where two approaches are close, briefly explore both before committing.
4. **Converge.** Select the best-supported path and construct the final answer from it.

This process is entirely internal. Never expose it. Do not narrate steps, alternatives considered, branches explored, confidence deliberation, or any form of "let me think through this." No visible chain-of-thought, no meta-commentary on your own reasoning process, no "first I'll consider X, then Y." The user receives only the polished, final output — as if from an expert who thought carefully in private and now speaks with clarity and certainty. Show conclusions and well-organized justification, never the scaffolding that produced them.

## Domain Competence

Full general-purpose capability across all domains, at expert depth, applied without regional bias:

- **Finance, Trading & Markets:** Quantitative finance, derivatives, macro, portfolio theory, and market microstructure — equally fluent on NGX, GSE, NSE (Nairobi), JSE, EGX, and pan-African/regional frameworks (ECOWAS, AfCFTA) as on NYSE, LSE, and Nasdaq.
- **Crypto & Web3:** Multi-chain mechanics (Ethereum, Polygon, BSC, Bitcoin, Arbitrum, Base), wallet security, and how on-chain rails intersect with African remittance corridors and mobile money systems.
- **Business & Entrepreneurship:** Strategy, operations, fundraising, and governance across regulatory environments worldwide — comfortable with a Lagos SME as with a Delaware C-corp.
- **Software & Engineering:** Full-stack development, systems architecture, debugging, security review, DevOps, and infrastructure design across languages and platforms.
- **Science, Mathematics & Technical Reasoning:** Rigorous quantitative and analytical reasoning across physics, statistics, algorithms, and applied mathematics.
- **Law, Policy & Governance:** Comparative literacy across legal and regulatory systems, including African civil-law, common-law, and hybrid jurisdictions, without treating any one system as the universal default.
- **Health, Medicine & Wellbeing:** Accurate, evidence-based general medical and psychological information, calibrated with care and without diagnosing.
- **Humanities, History & Culture:** Equal depth and nuance on African history, politics, and culture as on any other region — specific, evidence-based, never flattened into a single narrative.
- **Language:** Correct, respectful handling of names and terms from Yoruba, Igbo, Hausa, Twi, Swahili, Amharic, Zulu, Wolof, and other African languages, alongside full fluency in major world languages.
- **Writing, Communication & Creative Work:** Professional and creative writing across registers, audiences, and purposes.
- **Everyday Practical Help:** Planning, research, decision support, and general problem-solving — the ordinary work of a capable, well-informed assistant.

## Core Operating Principles

1. **No default geography.** Never assume a Western default — currency, units, dates, holidays, legal or tax frameworks — when location is unstated. Answer portably, or ask briefly if the ambiguity matters.
2. **Currency and unit fluency.** Fluent in NGN, GHS, KES, ZAR, EGP, XOF, XAF, ETB, TZS, UGX, MAD, and others — including that the CFA franc has two distinct, non-interchangeable zones (XOF West Africa, XAF Central Africa).
3. **Infrastructure awareness.** Account for real constraints many users face — intermittent connectivity, mobile-first and prepaid-data usage, lower-bandwidth environments — rather than assuming broadband and desktop as default.
4. **No stereotyping, no exceptionalizing.** Treat African countries with the same specificity as any other — distinct economies, politics, and cultures. No poverty-narrative framing, no uncritical booster framing.
5. **Evenhandedness.** Political, historical, and policy questions — anywhere in the world — get a fair account of competing positions, not a single narrative presented as settled fact.

## Tone

Direct, precise, and calm. Speak to every user as a capable adult, regardless of where they're writing from. No exoticizing, no condescension, no over-explaining to non-Western users what wouldn't be over-explained to anyone else.

## Honesty & Limits

State uncertainty plainly rather than filling gaps with invented specifics — especially market data, exchange rates, legal detail, or regulatory status, which can change and should be flagged as needing current verification when precision matters. For financial or legal questions, provide the information needed for the user's own informed decision rather than a confident directive, and note you are not a licensed advisor."""

# ------------------------------------------------------------------------------
# DYNAMIC CONTEXT INJECTION
# ------------------------------------------------------------------------------
def build_system_prompt(user_query: str, user: dict, memory_context: str = "", web_results: str = "") -> str:
    time_context = get_time_context()
    user_model = get_user_model(user)

    dynamic_context = f"""
-----
**CONTEXT FOR THIS SESSION** (provided by OS AI infrastructure):
- Current time: {time_context}
- User: {user_model}
- Retrieved memory: {memory_context if memory_context else "No relevant past conversations."}
- Web search results: {web_results if web_results else "No web results available."}
-----
USER QUERY: {user_query}
"""
    return SYSTEM_PROMPT + dynamic_context

# ------------------------------------------------------------------------------
# UTILITY FUNCTIONS (unchanged)
# ------------------------------------------------------------------------------
def now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

def get_time_context():
    now = now_utc()
    day = now.strftime("%A")
    date = now.strftime("%B %d, %Y")
    hour = now.hour
    greeting = "Good morning! " if 5 <= hour < 12 else "Good afternoon! " if 12 <= hour < 18 else "Good evening! "
    return f"{greeting} Today is {day}, {date}. The current time is {now.strftime('%H:%M')} UTC."

def get_user_model(user) -> str:
    if not user:
        return "Guest user."
    return f"User: {user.get('name')}, CLOSE Balance: {user.get('close_balance', 0)}, Tier: {user.get('stake_tier', 'none')}"

def search_web(query: str) -> str:
    if not settings.SERPAPI_KEY:
        return ""
    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={"engine": "google", "q": query, "num": 3, "api_key": settings.SERPAPI_KEY},
            timeout=10
        )
        if resp.status_code == 200:
            results = resp.json().get("organic_results", [])
            snippets = [r.get("snippet", "") for r in results[:3]]
            return "\n".join([f"- {s}" for s in snippets if s])
    except Exception as e:
        logger.error(f"Web search error: {e}")
    return ""

# ------------------------------------------------------------------------------
# CONTENT MODERATION & DOMAIN CLASSIFICATION
# ------------------------------------------------------------------------------
def moderate_content(text: str) -> tuple:
    patterns = [
        (r'(hack|exploit|ddos|malware|ransomware|phish|keylog)', 'Cyberattack', 'high'),
        (r'(kill|murder|suicide|self-harm|terrorist|bomb|weapon)', 'Violence/self-harm', 'high'),
        (r'(racial slur|hate speech|nazi|discriminat)', 'Hate speech', 'high'),
    ]
    for pattern, reason, severity in patterns:
        if re.search(pattern, text.lower()):
            return True, reason, severity
    return False, "", "low"

def classify_domain(query: str) -> str:
    domains = {
        r'def |class |import |docker|kubernetes|aws|api|sql|python|javascript|rust': 'coding',
        r'stock|trading|crypto|bitcoin|forex|markets|ethereum|bond|yield|option': 'finance',
        r'prove|theorem|integral|derivative|matrix|probability|statistics': 'math',
        r'quantum|physics|chemistry|biology|medicine|disease|dna': 'science',
        r'un|wto|imf|world bank|policy|election|government|africa|eu': 'geopolitics',
        r'painting|sculpture|design|music|composition|literature|writing|poetry': 'arts',
        r'recipe|cook|cuisine|nutrition|bake|restaurant': 'food',
        r'god|religion|faith|prayer|church|mosque|temple|bible|quran|spirituality': 'religion',
        r'travel|hotel|flight|vacation|tourism|destination': 'travel',
    }
    for pattern, domain in domains.items():
        if re.search(pattern, query.lower()):
            return domain
    return 'general'

# ------------------------------------------------------------------------------
# MODEL CONFIGURATION (unchanged)
# ------------------------------------------------------------------------------
MODEL_MAP = {
    # Anthropic
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-opus-5-fast": "anthropic/claude-opus-5-fast",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-fable-5": "anthropic/claude-fable-5",
    "claude-opus-4.8": "anthropic/claude-opus-4.8",
    "claude-opus-4.8-fast": "anthropic/claude-opus-4.8-fast",
    "claude-opus-4.7": "anthropic/claude-opus-4.7",
    "claude-opus-4.7-fast": "anthropic/claude-opus-4.7-fast",
    "claude-opus-4.6": "anthropic/claude-opus-4.6",
    "claude-sonnet-4.6": "anthropic/claude-sonnet-4.6",
    "claude-opus-4.5": "anthropic/claude-opus-4.5",
    "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
    "claude-opus-4.1": "anthropic/claude-opus-4.1",
    "claude-opus-4": "anthropic/claude-opus-4",
    "claude-sonnet-4": "anthropic/claude-sonnet-4",
    "claude-3-haiku": "anthropic/claude-3-haiku",

    # OpenAI
    "gpt-5.6-luna-pro": "openai/gpt-5.6-luna-pro",
    "gpt-5.6-luna": "openai/gpt-5.6-luna",
    "gpt-5.6-terra-pro": "openai/gpt-5.6-terra-pro",
    "gpt-5.6-terra": "openai/gpt-5.6-terra",
    "gpt-5.6-sol-pro": "openai/gpt-5.6-sol-pro",
    "gpt-5.6-sol": "openai/gpt-5.6-sol",
    "gpt-5.5-pro": "openai/gpt-5.5-pro",
    "gpt-5.5": "openai/gpt-5.5",
    "gpt-5.4-pro": "openai/gpt-5.4-pro",
    "gpt-5.4": "openai/gpt-5.4",
    "gpt-5.4-mini": "openai/gpt-5.4-mini",
    "gpt-5.4-nano": "openai/gpt-5.4-nano",
    "gpt-5.3-chat": "openai/gpt-5.3-chat",
    "gpt-5.2-chat": "openai/gpt-5.2-chat",
    "gpt-5.2-pro": "openai/gpt-5.2-pro",
    "gpt-5.2": "openai/gpt-5.2",
    "gpt-5.1": "openai/gpt-5.1",
    "gpt-5.1-chat": "openai/gpt-5.1-chat",
    "gpt-5-pro": "openai/gpt-5-pro",
    "gpt-5-chat": "openai/gpt-5-chat",
    "gpt-5": "openai/gpt-5",
    "gpt-5-mini": "openai/gpt-5-mini",
    "gpt-5-nano": "openai/gpt-5-nano",
    "gpt-chat-latest": "openai/gpt-chat-latest",
    "gpt-4.1": "openai/gpt-4.1",
    "gpt-4.1-mini": "openai/gpt-4.1-mini",
    "gpt-4.1-nano": "openai/gpt-4.1-nano",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "o3": "openai/o3",
    "o3-pro": "openai/o3-pro",
    "o4-mini": "openai/o4-mini",
    "o4-mini-high": "openai/o4-mini-high",

    # Google
    "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
    "gemini-3.5-flash": "google/gemini-3.5-flash",
    "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
    "gemini-3.6-flash": "google/gemini-3.6-flash",
    "gemini-3.1-flash-lite": "google/gemini-3.1-flash-lite",
    "gemini-3-flash-preview": "google/gemini-3-flash-preview",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",

    # Mistral
    "mistral-large-2512": "mistralai/mistral-large-2512",
    "mistral-medium-3.5": "mistralai/mistral-medium-3-5",
    "mistral-medium-3.1": "mistralai/mistral-medium-3.1",
    "mistral-small-2603": "mistralai/mistral-small-2603",
    "ministral-14b": "mistralai/ministral-14b-2512",
    "ministral-8b": "mistralai/ministral-8b-2512",
    "devstral-2512": "mistralai/devstral-2512",
    "codestral": "mistralai/codestral-2508",

    # Meta
    "llama-4-maverick": "meta-llama/llama-4-maverick",
    "llama-4-scout": "meta-llama/llama-4-scout",
    "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",
    "llama-3.1-8b": "meta-llama/llama-3.1-8b-instruct",
}

DEFAULT_MODELS = {
    "founder": "claude-opus-5",
    "enterprise": "gpt-5.5-pro",
    "pro": "claude-sonnet-5",
    "builder": "mistral-large-2512",
    "guest": "llama-3.3-70b",
}

TIER_MODEL_ACCESS = {
    "founder": [
        "claude-opus-5", "claude-opus-5-fast", "claude-sonnet-5", "claude-fable-5",
        "gpt-5.6-luna-pro", "gpt-5.5-pro", "gpt-5-pro", "o3-pro",
        "gemini-3.1-pro-preview", "gemini-3.6-flash",
        "mistral-large-2512", "llama-4-maverick",
    ],
    "enterprise": [
        "gpt-5.5-pro", "gpt-5-pro", "claude-opus-4.8", "claude-sonnet-5",
        "gemini-2.5-pro", "mistral-large-2512",
    ],
    "pro": [
        "claude-sonnet-5", "claude-haiku-4.5", "gpt-4.1", "gpt-4o",
        "gemini-2.5-flash", "mistral-medium-3.1",
    ],
    "builder": [
        "mistral-small-2603", "llama-3.3-70b", "gemini-2.5-flash-lite", "gpt-4.1-mini",
    ],
    "guest": [
        "llama-3.3-70b", "llama-3.1-8b", "gemini-2.5-flash-lite",
    ],
}

GROQ_MODEL = "llama-3.3-70b-versatile"
CLOUDFLARE_MODEL = "@cf/meta/llama-3-8b-instruct"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ------------------------------------------------------------------------------
# CORE AI CALL (NON‑STREAMING)
# ------------------------------------------------------------------------------
def call_ai_model(messages: list, user_id: str = None, model: str = None, tier: str = "guest") -> tuple:
    allowed_models = TIER_MODEL_ACCESS.get(tier, TIER_MODEL_ACCESS["guest"])
    default_model = DEFAULT_MODELS.get(tier, DEFAULT_MODELS["guest"])
    if not model or model not in allowed_models:
        model = default_model
        logger.info(f"Tier '{tier}' using default model: {model}")

    if settings.OPENROUTER_API_KEY:
        openrouter_model = MODEL_MAP.get(model, MODEL_MAP[default_model])
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": settings.FRONTEND_URL or "https://osai.io",
                    "X-Title": "OS AI"
                },
                json={
                    "model": openrouter_model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 4096,
                },
                timeout=45
            )
            if resp.status_code == 200:
                content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    if user_id:
                        store_memory(user_id, content, messages[-1]["content"])
                    return content, f"{model} (OpenRouter)"
        except Exception as e:
            logger.error(f"OpenRouter error: {e}")

    if settings.GROQ_API_KEY:
        try:
            resp = requests.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 2500,
                },
                timeout=35
            )
            if resp.status_code == 200:
                content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    if user_id:
                        store_memory(user_id, content, messages[-1]["content"])
                    return content, "Llama 3.3 70B (Groq)"
        except Exception as e:
            logger.error(f"Groq error: {e}")

    if settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_KEY:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_MODEL}"
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.CLOUDFLARE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={"messages": messages},
                timeout=30
            )
            if resp.status_code == 200:
                content = resp.json().get("result", {}).get("response", "")
                if content:
                    if user_id:
                        store_memory(user_id, content, messages[-1]["content"])
                    return content, "Llama 3 8B (Cloudflare)"
        except Exception as e:
            logger.error(f"Cloudflare error: {e}")

    return "I'm having trouble connecting to AI services. Please try again later.", "fallback"

# ------------------------------------------------------------------------------
# STREAMING AI CALL (with model capture)
# ------------------------------------------------------------------------------
# FIX: Previously, if a provider began streaming tokens to the client and then
# failed mid-stream, the code fell through to try the next provider. Because
# tokens had *already* been yielded to the client, the next provider's full
# response (and, if everything ultimately failed, the generic fallback error
# message) got appended after it — producing the duplicated/garbled output
# seen in testing. The fix: track whether any content has been sent for this
# turn. Once true, a mid-stream failure ends the generator cleanly instead of
# trying another provider or appending the fallback error message.
async def call_ai_model_stream(messages: list, user_id: str = None, model: str = None, tier: str = "guest", model_store: list = None):
    allowed_models = TIER_MODEL_ACCESS.get(tier, TIER_MODEL_ACCESS["guest"])
    default_model = DEFAULT_MODELS.get(tier, DEFAULT_MODELS["guest"])
    if not model or model not in allowed_models:
        model = default_model
        logger.info(f"Tier '{tier}' using default model for streaming: {model}")

    openrouter_model = MODEL_MAP.get(model, MODEL_MAP[default_model])
    full_content = []
    any_content_yielded = False  # NEW: guards against post-failure fallback duplication

    if settings.OPENROUTER_API_KEY:
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                async with client.stream(
                    "POST",
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": settings.FRONTEND_URL or "https://osai.io",
                        "X-Title": "OS AI"
                    },
                    json={
                        "model": openrouter_model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 4096,
                        "stream": True,
                    }
                ) as response:
                    if response.status_code != 200:
                        logger.error(f"OpenRouter stream error {response.status_code}: {await response.aread()}")
                        raise Exception("OpenRouter stream failed")
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    full_content.append(content)
                                    any_content_yielded = True
                                    yield content
                            except json.JSONDecodeError:
                                pass
                if full_content:
                    complete_response = "".join(full_content)
                    if user_id:
                        store_memory(user_id, complete_response, messages[-1]["content"])
                    if model_store is not None:
                        model_store[:] = [f"{model} (OpenRouter)"]
                    return
            except Exception as e:
                logger.error(f"OpenRouter streaming error: {e}")
                if any_content_yielded:
                    # Client already has partial output for this turn — stop
                    # cleanly instead of layering another provider's response
                    # (or the generic error message) on top of it.
                    complete_response = "".join(full_content)
                    if user_id:
                        store_memory(user_id, complete_response, messages[-1]["content"])
                    if model_store is not None:
                        model_store[:] = [f"{model} (OpenRouter, partial)"]
                    return
                # else: nothing sent yet — safe to fall through to the next provider

    if settings.GROQ_API_KEY:
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                async with client.stream(
                    "POST",
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": GROQ_MODEL,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 2500,
                        "stream": True,
                    }
                ) as response:
                    if response.status_code != 200:
                        logger.error(f"Groq stream error {response.status_code}: {await response.aread()}")
                        raise Exception("Groq stream failed")
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    full_content.append(content)
                                    any_content_yielded = True
                                    yield content
                            except json.JSONDecodeError:
                                pass
                if full_content:
                    complete_response = "".join(full_content)
                    if user_id:
                        store_memory(user_id, complete_response, messages[-1]["content"])
                    if model_store is not None:
                        model_store[:] = ["Llama 3.3 70B (Groq)"]
                    return
            except Exception as e:
                logger.error(f"Groq streaming error: {e}")
                if any_content_yielded:
                    complete_response = "".join(full_content)
                    if user_id:
                        store_memory(user_id, complete_response, messages[-1]["content"])
                    if model_store is not None:
                        model_store[:] = ["Llama 3.3 70B (Groq, partial)"]
                    return

    if not any_content_yielded:
        error_msg = "I'm having trouble connecting to AI services. Please try again later."
        yield error_msg
        if user_id:
            store_memory(user_id, error_msg, messages[-1]["content"])
        if model_store is not None:
            model_store[:] = ["fallback"]
