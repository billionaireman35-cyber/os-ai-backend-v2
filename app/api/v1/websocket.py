from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import websockets
import json
import logging

logger = logging.getLogger(__name__)

async def token_feed_ws(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket client connected to token feed")
    try:
        # Connect to DexScreener's WebSocket
        dex_url = "wss://api.dexscreener.com/token-profiles/latest/v1"
        async with websockets.connect(dex_url) as dex_ws:
            logger.info("Connected to DexScreener WebSocket")
            # Relay messages between client and DexScreener
            while True:
                # Wait for message from DexScreener
                msg = await dex_ws.recv()
                # Forward to client
                await websocket.send_text(msg)
    except websockets.exceptions.ConnectionClosed:
        logger.info("DexScreener connection closed")
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()