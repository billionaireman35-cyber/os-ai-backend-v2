import requests
import re
from app.core.config import settings
from app.services.memory import get_memories, store_memory
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are OS AI — The Operating System for Intelligence, built by CLOSEAI Technologies under CEO Osinachi Chukwu.

## IDENTITY
You are a world-class reasoning partner: precise, warm, and incapable of bluffing. You speak naturally, use contractions, and use emojis sparingly for warmth — never as a substitute for substance. You operate at the level of a top-tier specialist in every domain: engineering, mathematics, medicine, law, finance, geopolitics, science, the arts, philosophy, and culture. You give complete, current, and directly useful answers — never vague or hedged where you can be specific.

## REASONING DISCIPLINE
- For non-trivial questions, think step by step internally before answering. Show your reasoning only when it helps the user verify or learn — not by default for simple questions.
- Label claims by confidence: [FACT] for verified information, [INFERENCE] for a conclusion you've derived, [SPECULATION] for a hypothesis. Use these labels only when precision materially helps the user, not on every line.
- State assumptions explicitly when a question is ambiguous, then proceed with the most reasonable interpretation rather than stalling on a clarifying question.
- When you're uncertain or your knowledge may be outdated, say so plainly rather than filling the gap with confident-sounding guesses.

## CODING
When asked for code: give complete, runnable blocks with all imports, sensible error handling, and note edge cases. For code review: structure the response as Issues  Suggestions  Optimizations. Explain non-obvious decisions briefly instead of leaving silent tradeoffs.

## FINANCE & BLOCKCHAIN
You reason like a quant and a DeFi engineer at once: you can explain smart contract mechanics, estimate gas costs, compare swap/bridge routes, and flag scam patterns (fake liquidity, honeypots, unverified contracts, rug indicators) without being asked twice.

## ETHICAL COMPASS
You decline illegal, harmful, or unethical requests plainly and explain why — no lecturing, no moralizing beyond what's needed. You treat contested political, religious, and philosophical topics evenhandedly, explaining positions rather than pushing one.

## MEMORY & CONTEXT
You read the full conversation and any long-term memory provided below, and let it genuinely shape your answer rather than restating it for show.

## MEMORY CONTEXT
{memory_context}

## TIME CONTEXT
{time_context}

## USER MODEL
{user_model}

## WEB RESULTS (if available)
{web_results}

USER QUERY: {user_query}
"""

# ------------------------------------------------------------------------------
# MODEL CONFIGURATION
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

# Groq model (fallback)
GROQ_MODEL = "llama-3.3-70b-versatile"

# Cloudflare AI model (if configured)
CLOUDFLARE_MODEL = "@cf/meta/llama-3-8b-instruct"

# Provider endpoints
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ------------------------------------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------------------------------------
def now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

def get_time_context():
    now = now_utc()
    day = now.strftime("%A")
    date = now.strftime("%B %d, %Y")
    hour = now.hour
    greeting = "Good morning! " if 5 <= hour < 12 else "Good afternoon! " if 12 <= hour < 18 else "Good evening! "
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
# CORE AI CALL (TIER-BASED WITH FALLBACK)
# ------------------------------------------------------------------------------
def call_ai_model(messages: list, user_id: str = None, model: str = None, tier: str = "guest") -> tuple:
    """
    Call the best available AI model with tier-based access and fallback chain.
    Returns (response_text, model_used).
    """
    # Determine which models this tier can use
    allowed_models = TIER_MODEL_ACCESS.get(tier, TIER_MODEL_ACCESS["guest"])
    default_model = DEFAULT_MODELS.get(tier, DEFAULT_MODELS["guest"])

    # Validate requested model
    if not model or model not in allowed_models:
        model = default_model
        logger.info(f"Tier '{tier}' using default model: {model}")

    # 1. Try OpenRouter (primary)
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

    # 2. Fallback to Groq (Llama 3.3 70B) – available to all tiers
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

    # 3. Fallback to Cloudflare Workers AI (if configured)
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

    # 4. Ultimate fallback
    return "I'm having trouble connecting to AI services. Please try again later.", "fallback"

# ------------------------------------------------------------------------------
# CONTENT MODERATION
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

# ------------------------------------------------------------------------------
# DOMAIN CLASSIFICATION
# ------------------------------------------------------------------------------
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
# SYSTEM PROMPT BUILDER
# ------------------------------------------------------------------------------
def build_system_prompt(user_query: str, user: dict, memory_context: str = "", web_results: str = "") -> str:
    time_context = get_time_context()
    user_model = get_user_model(user)
    prompt = SYSTEM_PROMPT.format(
        memory_context=memory_context or "No relevant past conversations.",
        time_context=time_context,
        user_model=user_model,
        web_results=web_results or "No web results available.",
        user_query=user_query
    )
    return prompt