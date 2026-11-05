import os
import html
import hmac
import asyncio
from typing import Dict, Set, Optional
from urllib.parse import quote
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import psycopg2
from groq import Groq

app = FastAPI(title="Liturghie Audio Sync Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Cod secret pentru rolul Altar. Dacă lipsește, ORICINE poate deveni broadcaster (doar pentru teste!)
BROADCASTER_KEY = os.getenv("BROADCASTER_KEY", "")
# Cod opțional pentru pagina /admin
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
# Parohiile acceptate (separate prin virgulă). Trebuie să coincidă cu opțiunile din index.html
ALLOWED_CHURCHES = {c.strip() for c in os.getenv("ALLOWED_CHURCHES", "parohia_cluj,catedrala_blaj,parohia_oradea").split(",") if c.strip()}

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# STAREA GLOBALĂ MULTI-BISERICĂ (izolarea parohiilor)
biserici_listeners: Dict[str, Set[WebSocket]] = {}
biserici_broadcasters: Dict[str, Optional[WebSocket]] = {}
biserici_history: Dict[str, list] = {}

LITURGICAL_PROMPT = "Liturghia Greco-Catolică. Ectenia, Altar, Isus Hristos, Amin, Aleluia, heruvic, ecfonis."

PRAG_SIMILARITATE = 0.35   # sub acest prag afișăm textul brut de la Groq
MIN_AUDIO_BYTES = 3000     # fragmente mai mici = liniște
HALUCINATII = ("mulțumesc că ați vizionat", "mulțumesc pentru vizionare", "subtitrare", "abonați-vă")

if not BROADCASTER_KEY:
    print("⚠️ BROADCASTER_KEY nu este setat: oricine poate deveni Altar.")


def get_db_connection():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


def cauta_text_liturgic(text_transcris: str, biserica_id: str) -> Optional[str]:
    """Caută în Supabase cel mai apropiat text oficial. Returnează None dacă potrivirea e slabă.
    Folosește maximul dintre similarity (șir întreg) și word_similarity (fragment dintr-un text lung)."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT text_oficial,
                   GREATEST(similarity(text_oficial, %s), word_similarity(%s, text_oficial)) AS scor
            FROM texte_liturghie
            ORDER BY scor DESC
            LIMIT 1;
            """,
            (text_transcris, text_transcris),
        )
        row = cur.fetchone()
        cur.close()
        if row and row[1] is not None and row[1] >= PRAG_SIMILARITATE:
            return row[0]
        return None
    except Exception as e:
        print(f"❌ Eroare la căutarea în Supabase: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _key_ok(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode(), expected.encode())


async def notify_status(biserica_id: str):
    """Anunță enoriașii dacă Altarul transmite sau nu."""
    payload = {"type": "status", "broadcaster": biserici_broadcasters.get(biserica_id) is not None}
    targets = list(biserici_listeners.get(biserica_id, set()))
    if targets:
        await asyncio.gather(*[l.send_json(payload) for l in targets], return_exceptions=True)


@app.get("/")
def read_root():
    db_status = "Necunoscută"
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM texte_liturghie;")
            count = cur.fetchone()[0]
            cur.close()
            conn.close()
            db_status = f"Conectat cu succes! Găsite {count} rânduri în texte_liturghie."
        else:
            db_status = "Lipsește variabila DATABASE_URL."
    except Exception as e:
        db_status = f"Eroare conexiune DB: {str(e)}"

    return {
        "status": "Serverul Liturghie este online și funcționează!!!",
        "database": db_status,
        "groq_activ": bool(groq_client),
        "altar_protejat": bool(BROADCASTER_KEY),
    }


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(biserica_id: str = "parohia_cluj", key: str = ""):
    if ADMIN_KEY and not _key_ok(key, ADMIN_KEY):
        return HTMLResponse(content="<h3>Acces interzis. Adaugă ?key=COD în adresă.</h3>", status_code=401)

    biserica_safe = html.escape(biserica_id)
    istoric_biserica = biserici_history.get(biserica_id, [])
    history_html = "".join(
        f"<p style='border-bottom:1px solid #eee; padding:5px; margin:0;'>⏱️ {html.escape(msg)}</p>"
        for msg in reversed(istoric_biserica[-15:])
    )

    listeners_count = len(biserici_listeners.get(biserica_id, set()))
    broadcaster_active = biserici_broadcasters.get(biserica_id) is not None

    key_part = f"&key={quote(key)}" if key else ""
    links = " | ".join(
        f"<a href='/admin?biserica_id={quote(c)}{key_part}'>{html.escape(c)}</a>" for c in sorted(ALLOWED_CHURCHES)
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Liturghie Live - Control Panel</title>
        <meta http-equiv="refresh" content="3">
        <style>
            body {{ font-family: sans-serif; background: #f4f6f9; color: #333; margin: 20px; }}
            .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            .status {{ display: inline-block; padding: 5px 10px; border-radius: 4px; font-weight: bold; }}
            .online {{ background: #d4edda; color: #155724; }}
            .offline {{ background: #f8d7da; color: #721c24; }}
        </style>
    </head>
    <body>
        <h2>⛪ Panou Administrativ: {biserica_safe}</h2>
        <p>{links}</p>
        <div class="card">
            <h3>📊 Monitorizare Resurse</h3>
            <p>Status Streamer Principal (Altar/Cantor):
                <span class="status {'online' if broadcaster_active else 'offline'}">
                    { 'ACTIV' if broadcaster_active else 'INACTIV (Mod Individual)' }
                </span>
            </p>
            <p>Enoriași conectați în bănci: <strong>{listeners_count}</strong></p>
        </div>
        <div class="card">
            <h3>🎙️ Transcrieri Groq Live în această locație</h3>
            <div style="background: #fafafa; border: 1px solid #ccc; height: 200px; overflow-y: scroll; padding: 10px;">
                {history_html if history_html else "<p style='color:#999;'>Nicio transcriere live în acest moment...</p>"}
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    biserica_id = websocket.query_params.get("biserica_id", "default")
    is_broadcaster = websocket.query_params.get("role") == "broadcaster"
    provided_key = websocket.query_params.get("key", "")

    # Validări: parohie cunoscută și, pentru Altar, codul corect
    if biserica_id not in ALLOWED_CHURCHES:
        await websocket.send_json({"error": "Parohie necunoscută."})
        await websocket.close(code=1008, reason="church")
        return
    if is_broadcaster and BROADCASTER_KEY and not _key_ok(provided_key, BROADCASTER_KEY):
        await websocket.send_json({"error": "Cod Altar incorect."})
        await websocket.close(code=1008, reason="key")
        return

    biserici_listeners.setdefault(biserica_id, set())
    biserici_history.setdefault(biserica_id, [])

    if is_broadcaster:
        biserici_broadcasters[biserica_id] = websocket
        print(f"⛪ Sursa principala conectata pentru: {biserica_id}")
        await notify_status(biserica_id)
    else:
        biserici_listeners[biserica_id].add(websocket)
        print(f"📱 Enorias conectat la {biserica_id}. Total: {len(biserici_listeners[biserica_id])}")
        try:
            await websocket.send_json({"type": "status", "broadcaster": biserici_broadcasters.get(biserica_id) is not None})
        except Exception:
            pass

    try:
        if not groq_client:
            await websocket.send_json({"error": "GROQ_API_KEY lipseste pe server."})
            await websocket.close()
            return

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            # Mesaje text: keep-alive de la aplicație
            if message.get("text") is not None:
                if message["text"] == "ping":
                    await websocket.send_json({"type": "pong"})
                continue

            # Mesaje binare: fișier audio COMPLET (webm/opus)
            audio_bytes = message.get("bytes")
            if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
                continue

            # Enoriașii sunt ignorați cât timp Altarul transmite
            if not is_broadcaster and biserici_broadcasters.get(biserica_id) is not None:
                continue
            # Un broadcaster înlocuit de unul nou nu mai are voie să transmită
            if is_broadcaster and biserici_broadcasters.get(biserica_id) is not websocket:
                continue

            try:
                loop = asyncio.get_running_loop()
                transcription = await loop.run_in_executor(
                    None,
                    lambda: groq_client.audio.transcriptions.create(
                        file=("audio.webm", audio_bytes),
                        model="whisper-large-v3",
                        language="ro",
                        temperature=0.0,
                        prompt=LITURGICAL_PROMPT,
                    ),
                )

                text_recunoscut = transcription.text.strip()
                if len(text_recunoscut) < 3:
                    continue
                if any(h in text_recunoscut.lower() for h in HALUCINATII):
                    continue

                print(f"🎙️ [{biserica_id}] Brut Groq: {text_recunoscut}")

                text_karaoke = await loop.run_in_executor(None, cauta_text_liturgic, text_recunoscut, biserica_id)
                text_final = text_karaoke if text_karaoke else text_recunoscut

                istoric = biserici_history[biserica_id]
                istoric.append(text_final)
                if len(istoric) > 100:
                    del istoric[:-100]

                payload = {
                    "text": text_final,
                    "text_brut": text_recunoscut,
                    "sursa": "Supabase" if text_karaoke else "Groq-Brut",
                }

                await websocket.send_json(payload)

                if is_broadcaster and biserici_listeners[biserica_id]:
                    await asyncio.gather(
                        *[l.send_json(payload) for l in list(biserici_listeners[biserica_id])],
                        return_exceptions=True,
                    )
            except Exception as groq_err:
                print(f"❌ Eroare Groq [{biserica_id}]: {groq_err}")
                try:
                    await websocket.send_json({"error": "Eroare procesare audio."})
                except Exception:
                    pass

    except Exception as e:
        print(f"⚠️ Eroare generala [{biserica_id}]: {e}")
    finally:
        if is_broadcaster:
            if biserici_broadcasters.get(biserica_id) is websocket:
                biserici_broadcasters[biserica_id] = None
                await notify_status(biserica_id)
            print(f"🔌 Sursa principala s-a deconectat de la {biserica_id}")
        else:
            biserici_listeners[biserica_id].discard(websocket)
            print(f"🔌 Enorias deconectat de la {biserica_id}")
