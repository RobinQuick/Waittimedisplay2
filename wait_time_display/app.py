import os, asyncio
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="QSR Wait Time", version="0.2.0")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---- ÉTAT EN MÉMOIRE ----
WAIT_TIME = 10                      # minutes
OFFER = "un café"                   # offre par défaut
listeners: set[asyncio.Queue] = set()

async def notify():
    """Diffuse WAIT_TIME et OFFER à tous les clients via SSE."""
    data = f"data: {WAIT_TIME}|{OFFER}\n\n"
    for q in list(listeners):
        await q.put(data)

# ---- PAGES ----
@app.get("/", response_class=HTMLResponse)
@app.get("/display", response_class=HTMLResponse)
async def display(request: Request):
    return templates.TemplateResponse(
        "display.html",
        {"request": request, "initial": WAIT_TIME, "offer": OFFER},
    )

@app.get("/control", response_class=HTMLResponse)
async def control(request: Request):
    return templates.TemplateResponse("control.html", {"request": request})

# ---- API TEMPS ----
@app.get("/wait")
async def get_wait():
    return {"wait": WAIT_TIME}

@app.post("/wait")
async def set_wait(value: int = Form(...), pin: str = Form(None)):
    # PIN FACULTATIF : on ignore la vérification
    global WAIT_TIME
    WAIT_TIME = max(0, int(value))
    await notify()
    return {"ok": True, "wait": WAIT_TIME}

# ---- API OFFRE ----
@app.get("/offer")
async def get_offer():
    return {"offer": OFFER}

@app.post("/offer")
async def set_offer(value: str = Form(...)):
    global OFFER
    OFFER = value.strip()
    await notify()
    return {"ok": True, "offer": OFFER}

# ---- SSE ----
from starlette.background import BackgroundTask

@app.get("/stream")
async def stream():
    async def gen(q: asyncio.Queue):
        # valeur initiale
        yield f"data: {WAIT_TIME}|{OFFER}\n\n"
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
        background=BackgroundTask(lambda: listeners.discard(q))
    )

