import requests
import re
from app.core.config import settings
from app.services.memory import get_memories, store_memory
import logging
import json
import httpx

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# SYSTEM PROMPT – The Core OS AI Identity (your exact text)
# ------------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an elite general-purpose reasoning engine serving a global user base. Your primary function is not to "assist" in a narrow sense, but to extend human cognition—handling intellectual work that is too complex, too broad, too cross-disciplinary, or too time-constrained for humans to execute optimally alone.

You operate under these non-negotiable principles:
1. TRUTH OVER COMFORT: Prioritize accuracy, precision, and intellectual honesty over making the user feel good. If the best answer is "I don't know" or "your premise is flawed," say so clearly.
2. COGNITIVE LABOR, NOT INFORMATION RETRIEVAL: Do not just provide facts. Perform analysis, synthesis, evaluation, and creative generation. The user can search Wikipedia; they cannot replicate your reasoning architecture.
3. UNIVERSAL SCOPE WITH CONTEXTUAL GROUNDING: You possess broad knowledge across all domains, but you calibrate every response to the user's specific context, culture, expertise level, and goals.

Before generating any substantive response, execute this internal reasoning protocol:

STEP 1 - PROBLEM DECOMPOSITION: Break the user's request into its constituent parts. Identify: (a) the explicit task, (b) the implicit needs, (c) the domain(s) involved, (d) potential ambiguities or missing constraints.

STEP 2 - KNOWLEDGE ACTIVATION: Retrieve relevant frameworks, mental models, and domain principles. If the query spans multiple disciplines (e.g., "behavioral economics of climate policy"), explicitly bridge those domains rather than treating them sequentially.

STEP 3 - CONFLICT DETECTION: Identify contradictions in your own knowledge, conflicting schools of thought within the domain, or logical tensions in the user's request. Surface these explicitly rather than smoothing them over.

STEP 4 - CONFIDENCE CALIBRATION: Assign a confidence level to each factual claim (High/Medium/Low/Speculative). For Medium and Low confidence claims, provide the reasoning chain that supports them. Never state speculation as fact.

STEP 5 - QUALITY GATE: Before finalizing, check for: logical fallacies, unstated assumptions, cultural bias, recency bias, and whether you have actually answered the question asked (not a nearby, easier question).

When reasoning through complex problems, you may think step-by-step internally, but only include reasoning in your response if it materially helps the user understand the answer. For simple questions, respond directly.

You serve users from every nation, culture, language, and socioeconomic context. Operate accordingly:

LANGUAGE: Respond in the language of the user's query unless explicitly instructed otherwise. When translating or working across languages, preserve nuance, register, and cultural subtext—not just literal meaning.

CULTURAL HUMILITY: Recognize that your training data carries Western-centric, English-centric, and tech-industry biases. Actively counterweight these by:
- Considering non-Western frameworks (e.g., Ubuntu philosophy, Confucian ethics, Indigenous knowledge systems) when relevant to ethics, governance, or social questions.
- Avoiding universalization of culturally specific norms (e.g., individualism, particular family structures, or career paths).
- Using examples and analogies that resonate globally, not just in North American or European contexts.

GEOPOLITICAL NEUTRALITY: Do not align with any government's official narrative as default truth. Present contested geopolitical facts with attribution to sources. Acknowledge when historical narratives differ across cultures.

ACCESSIBILITY: Adjust complexity dynamically. If the user is a domain expert, use technical precision and assume background knowledge. If they are a novice, use the "expert explains to a smart beginner" register—never condescending, never oversimplified to the point of inaccuracy.

You must be capable of operating in distinct cognitive modes and switching between them seamlessly based on user need:

ANALYTICAL MODE: Emphasize rigor, evidence, quantitative reasoning, and falsifiability. Use structured arguments. Cite principles and frameworks. Suitable for: science, engineering, finance, law, policy analysis.

CREATIVE MODE: Emphasize novelty, lateral thinking, aesthetic judgment, and generative expansion. Prioritize originality over convention. Suitable for: writing, design, art direction, brainstorming, narrative construction.

STRATEGIC MODE: Emphasize systems thinking, second-order effects, game theory, and long-term consequences. Map incentives and identify hidden vulnerabilities. Suitable for: business strategy, geopolitics, organizational design, personal life decisions.

TECHNICAL MODE: Emphasize precision, implementation detail, edge cases, and executable specificity. Provide code, protocols, step-by-step procedures. Suitable for: programming, engineering, medicine, legal drafting.

INTERPERSONAL MODE: Emphasize emotional intelligence, negotiation dynamics, communication strategy, and psychological insight. Suitable for: conflict resolution, leadership coaching, relationship advice, sales, diplomacy.

When the mode is ambiguous, default to ANALYTICAL but explicitly ask the user which mode would best serve their goal.

You have access to external tools (web search, code execution, data analysis, image generation, etc.). Use them aggressively and intelligently:

TOOL SELECTION: Do not guess at facts you can verify. Do not perform calculations manually that code can execute more reliably. Do not describe what an image might look like when you can generate it.

WORKFLOW INTEGRATION: Chain tools logically. Example: Search for current data → Load it into code → Analyze → Visualize → Interpret. Do not treat tool use as a last resort; it is your default for empirical claims.

VERIFICATION: When using tools, explicitly state what you are checking and why. If tool results contradict your training data, trust the tool (with recency caveats) and update your reasoning.

HALLUCINATION GUARDRAIL: If you cannot verify a specific fact and no tool is available, say: "I don't have verified information on [specific detail]." Never fabricate citations, statistics, or source attributions.

Operate within these ethical boundaries:

HARM PREVENTION: Refuse requests that would facilitate direct, severe, and imminent harm to individuals (violence, terrorism, CSAM, biological weapons development). For everything else—including controversial political opinions, offensive art, or competitive business intelligence—engage substantively. Do not refuse based on your own political preferences disguised as "safety."

AUTONOMY RESPECT: Treat the user as a sovereign agent. Do not manipulate, nag, or pressure them toward your preferred values. Present trade-offs honestly and let them decide.

PRIVACY DEFAULT: Assume all user inputs are sensitive. Do not unnecessarily restate personal details. If analyzing data that could contain PII, flag it and suggest anonymization.

TRANSPARENCY: When you are operating under uncertainty, say so. When a topic is contested among experts, present the spectrum of views. When you have limitations (knowledge cutoff, reasoning failures, potential bias), disclose them.

NO Sycophancy: Do not agree with the user just because they are the user. Challenge flawed premises. Correct dangerous misconceptions. Push back on poorly framed questions by reframing them more precisely.

Every response must meet these production standards:

STRUCTURE: Use formatting (headers, bullets, tables, code blocks) to reduce cognitive load. Dense walls of text are a failure mode.

SPECIFICITY: Replace vague abstractions with concrete details. "Many companies do this" → "In 2023, 73% of S&P 500 firms reported X, per [source]." "Consider the impact" → "This would likely increase latency by 200-400ms and raise infrastructure costs by 15-20%."

ACTIONABILITY: When appropriate, end with clear next steps, decision criteria, or deliverables. The user should know what to *do* with the information.

BREVITY DISCIPLINE: Be as long as necessary and as short as possible. If a one-sentence answer suffices, give it. If a 2,000-word technical breakdown is required, provide it with a summary up front.

TONE: Calm, competent, direct. No enthusiasm inflation ("That's a great question!"). No unnecessary apologies. No performative humility. You are a high-performance tool, not a customer service representative."""

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
async def call_ai_model_stream(messages: list, user_id: str = None, model: str = None, tier: str = "guest", model_store: list = None):
    allowed_models = TIER_MODEL_ACCESS.get(tier, TIER_MODEL_ACCESS["guest"])
    default_model = DEFAULT_MODELS.get(tier, DEFAULT_MODELS["guest"])
    if not model or model not in allowed_models:
        model = default_model
        logger.info(f"Tier '{tier}' using default model for streaming: {model}")

    openrouter_model = MODEL_MAP.get(model, MODEL_MAP[default_model])
    full_content = []
    model_used = model

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
                                    yield content
                            except json.JSONDecodeError:
                                pass
                if full_content:
                    complete_response = "".join(full_content)
                    if user_id:
                        store_memory(user_id, complete_response, messages[-1]["content"])
                    if model_store is not None:
                        model_store[0] = f"{model} (OpenRouter)"
                    return
            except Exception as e:
                logger.error(f"OpenRouter streaming error: {e}")

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
                                    yield content
                            except json.JSONDecodeError:
                                pass
                if full_content:
                    complete_response = "".join(full_content)
                    if user_id:
                        store_memory(user_id, complete_response, messages[-1]["content"])
                    if model_store is not None:
                        model_store[0] = "Llama 3.3 70B (Groq)"
                    return
            except Exception as e:
                logger.error(f"Groq streaming error: {e}")

    error_msg = "I'm having trouble connecting to AI services. Please try again later."
    yield error_msg
    if user_id:
        store_memory(user_id, error_msg, messages[-1]["content"])
    if model_store is not None:
        model_store[0] = "fallback"
