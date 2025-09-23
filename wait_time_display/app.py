import os, asyncio
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="QSR Wait Time", version="1.0.0")

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
    for q in list(listeners):
        await q.put(msg)

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
    # (on ne calcule pas ici de logique horaire – à implémenter si besoin)
    await notify()
    return {"ok": True, "auto_offer": AUTO_OFFER}

# --- SSE ---
@app.get("/stream")
async def stream():
    async def gen(q: asyncio.Queue):
        # push initial
        yield _payload()
        try:
            while True:
                data = await q.get()
                yield data
        except asyncio.CancelledError:
            pass

    q: asyncio.Queue = asyncio.Queue()
    listeners.add(q)

    return StreamingResponse(
        gen(q),
        media_type="text/event-stream",
        background=BackgroundTask(lambda: listeners.discard(q)),
    )
