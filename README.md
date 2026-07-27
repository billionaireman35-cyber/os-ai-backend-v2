# OS AI Backend

FastAPI backend for OS AI – The Operating System for Intelligence.

## Features
- User auth (JWT, email verification, fingerprint)
- AI chat with memory (pgvector)
- Multi‑chain wallet (Polygon, Ethereum, BSC, Arbitrum, Base)
- Swap (1inch), Bridge (Socket), Send, Burn
- Gnosis Safe multisig
- Hustle Hub (collaborative workspaces with CLOSE burn)
- Developer tools (API keys, webhooks)
- Admin dashboard (founder only)

## Setup
1. Copy `.env.example` to `.env` and fill in your keys.
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Docs
Visit `/docs` after running for Swagger UI.
