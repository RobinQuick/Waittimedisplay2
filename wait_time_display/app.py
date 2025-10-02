import os, asyncio
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.background import BackgroundTask
from dotenv import load_dotenv
import time

load_dotenv()

app = FastAPI(title="QSR Wait Time", version="1.0.0")

# --- MIDDLEWARE CRITIQUE POUR SAMSUNG TIZEN ---
# CORS pour Tizen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Trusted hosts pour Render
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # Accepte tous les hosts
)

# Middleware custom pour headers Samsung
class TizenHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Headers critiques pour Samsung Tizen
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *;"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        # Pour SSE
        if request.url.path == "/stream":
            response.headers["X-Accel-Buffering"] = "no"
            response.headers["Connection"] = "keep-alive"
            
        return response

app.add_middleware(TizenHeaderMiddleware)

# --- TEMPLATES & STATIC ---
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- STATE (mémoire) ---
WAIT_TIME: int = int(os.getenv("WAIT_TIME", "5"))   # minutes
OFFER: str = os.getenv("OFFER", "un expresso")
MODE: str = os.getenv("MODE", "time")               # "time" | "logo" | "dual"
SHOW_OFFER: bool = os.getenv("SHOW_OFFER", "1") != "0"
AUTO_OFFER: bool = False

listeners: set[asyncio.Queue] = set()

def _payload() -> str:
    """Format SSE partagé par tout le front."""
    return f"data: {WAIT_TIME}|{OFFER}|{MODE}|{1 if SHOW_OFFER else 0}\n\n"

async def notify():
    """Diffuse l'état courant à tous les clients."""
    msg = _payload()
    dead_queues = []
    for q in list(listeners):
        try:
            await q.put(msg)
        except:
            dead_queues.append(q)
    # Nettoyer les queues mortes
    for q in dead_queues:
        listeners.discard(q)

# --- HEALTH CHECKS POUR RENDER ---
@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/ping")
async def ping():
    return Response(content="pong", media_type="text/plain")

# --- PAGE DE TEST POUR SAMSUNG ---
@app.get("/test", response_class=HTMLResponse)
async def test_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Samsung Test</title>
        <style>
            body {
                margin: 0;
                background: #E31E24;
                color: white;
                font-family: Arial, sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100vh;
                font-size: 3em;
            }
            #status { 
                margin-top: 20px; 
                font-size: 0.5em;
                padding: 20px;
                background: rgba(0,0,0,0.3);
                border-radius: 10px;
            }
            .ok { color: #4CAF50; }
            .error { color: #f44336; }
        </style>
    </head>
    <body>
        <h1>Quick Display Test ✓</h1>
        <div id="status">
            <div>Time: <span id="time"></span></div>
            <div>Connection: <span id="connection">Testing...</span></div>
            <div>SSE: <span id="sse">Waiting...</span></div>
            <div>URL: <span id="url" style="font-size: 0.6em;"></span></div>
        </div>
        <script>
            // Afficher l'URL
            document.getElementById('url').textContent = window.location.href;
            
            // Afficher l'heure
            setInterval(() => {
                document.getElementById('time').textContent = new Date().toLocaleTimeString();
            }, 1000);
            
            // Test de connexion basique
            fetch('/ping')
                .then(() => {
                    document.getElementById('connection').textContent = 'OK ✓';
                    document.getElementById('connection').className = 'ok';
                })
                .catch(e => {
                    document.getElementById('connection').textContent = 'Failed ✗';
                    document.getElementById('connection').className = 'error';
                });
            
            // Test SSE
            try {
                const evt = new EventSource('/stream');
                evt.onopen = () => {
                    document.getElementById('sse').textContent = 'Connected ✓';
                    document.getElementById('sse').className = 'ok';
                };
                evt.onmessage = (e) => {
                    document.getElementById('sse').textContent = 'Receiving data ✓';
                    document.getElementById('sse').className = 'ok';
                };
                evt.onerror = () => {
                    document.getElementById('sse').textContent = 'Error ✗';
                    document.getElementById('sse').className = 'error';
                };
            } catch(e) {
                document.getElementById('sse').textContent = 'Not supported';
                document.getElementById('sse').className = 'error';
            }
        </script>
    </body>
    </html>
    """

# --- PAGES ---
@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/screen")

@app.get("/screen", response_class=HTMLResponse)
async def screen(request: Request):
    """Une seule URL pour l'écran : choisit le template selon MODE."""
    ctx = {"request": request, "initial": WAIT_TIME, "offer": OFFER, "show_offer": SHOW_OFFER}
    if MODE == "logo":
        return templates.TemplateResponse("logo_only.html", ctx)
    if MODE == "dual":
        return templates.TemplateResponse("dual.html", ctx)
    return templates.TemplateResponse("display.html", ctx)

@app.get("/display", response_class=HTMLResponse)
async def display(request: Request):
    """Accès direct au mode 'time' (utile en dev)."""
    return templates.TemplateResponse(
        "display.html",
        {"request": request, "initial": WAIT_TIME, "offer": OFFER, "show_offer": SHOW_OFFER},
    )

@app.get("/control", response_class=HTMLResponse)
async def control(request: Request):
    return templates.TemplateResponse(
        "control.html",
        {
            "request": request,
            "initial": WAIT_TIME,
            "offer": OFFER,
            "auto_offer": AUTO_OFFER,
            "show_offer": SHOW_OFFER,
        },
    )

# --- API: temps d'attente ---
@app.get("/wait")
async def get_wait():
    return {"wait": WAIT_TIME}

@app.post("/wait")
async def set_wait(value: int = Form(...), pin: str = Form(None)):
    global WAIT_TIME
    WAIT_TIME = max(0, min(10, int(value)))  # cap côté serveur à 10 minutes
    await notify()
    return {"ok": True, "wait": WAIT_TIME}

# --- API: offre ---
@app.get("/offer")
async def get_offer():
    return {"offer": OFFER, "show": SHOW_OFFER}

@app.post("/offer")
async def set_offer(value: str = Form(""), pin: str = Form(None)):
    """Si value est vide -> on masque l'offre."""
    global OFFER, SHOW_OFFER
    OFFER = (value or "").strip()
    if OFFER == "":
        SHOW_OFFER = False
    await notify()
    return {"ok": True, "offer": OFFER, "show": SHOW_OFFER}

@app.post("/offer_visibility")
async def offer_visibility(enabled: str = Form(None), pin: str = Form(None)):
    global SHOW_OFFER
    SHOW_OFFER = bool(enabled)
    await notify()
    return {"ok": True, "show": SHOW_OFFER}

# --- API: mode d'affichage ---
@app.get("/mode")
async def get_mode():
    return {"mode": MODE}

@app.post("/mode")
async def set_mode(value: str = Form(...), pin: str = Form(None)):
    """value in {'time','logo','dual'}"""
    global MODE
    value = (value or "").strip().lower()
    if value in {"time", "logo", "dual"}:
        MODE = value
        await notify()
        return {"ok": True, "mode": MODE}
    return {"ok": False, "detail": "mode invalide"}

# --- API: auto offer (facultatif) ---
@app.post("/auto_offer")
async def set_auto(enabled: str = Form(None), pin: str = Form(None)):
    global AUTO_OFFER
    AUTO_OFFER = bool(enabled)
    await notify()
    return {"ok": True, "auto_offer": AUTO_OFFER}

# --- SSE OPTIMISÉ POUR RENDER + TIZEN ---
@app.get("/stream")
async def stream():
    async def generate():
        q = asyncio.Queue()
        listeners.add(q)
        
        try:
            # Envoyer l'état initial immédiatement
            yield _payload()
            # Envoyer un retry pour la reconnexion
            yield f"retry: 3000\n\n"
            # Heartbeat initial
            yield f": heartbeat\n\n"
            
            while True:
                try:
                    # Attendre un message avec timeout pour heartbeat
                    data = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield data
                except asyncio.TimeoutError:
                    # Envoyer un heartbeat si pas de données
                    yield f": heartbeat\n\n"
                    
        except asyncio.CancelledError:
            listeners.discard(q)
        finally:
            listeners.discard(q)
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )

# Point d'entrée pour Render
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    # Configuration pour production
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        # Désactiver le reload en production
        reload=False,
        # Workers = 1 pour SSE
        workers=1,
        # Timeout plus long pour SSE
        timeout_keep_alive=75
    )