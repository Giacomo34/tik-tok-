from fastapi import FastAPI, Depends, WebSocket
from auth import require_user
from sessions import start_session, stop_session
from stripe_webhook import handle_webhook

app = FastAPI()

@app.post("/sessions/start")
def start(user=Depends(require_user)):
    return start_session(user)

@app.post("/sessions/stop")
def stop(user=Depends(require_user)):
    return stop_session(user)

@app.post("/stripe/webhook")
def stripe_webhook(payload: dict):
    return handle_webhook(payload)

@app.websocket("/overlay/ws")
async def overlay_ws(ws: WebSocket):
    await ws.accept()
    # invio chunk audio ai client overlay
