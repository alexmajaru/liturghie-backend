import os
import io
import asyncio
from typing import Dict, Set, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# STAREA GLOBALĂ MULTI-BISERICĂ (Izolarea parohiilor)
biserici_listeners: Dict[str, Set[WebSocket]] = {}
biserici_broadcasters: Dict[str, WebSocket] = {}
biserici_history: Dict[str, list] = {}
LITURGICAL_PROMPT = "Liturghia Greco-Catolică. Ectenia, Altar, Isus Hristos, Amin, Aleluia, heruvic, ecfonis."

def get_db_connection():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL)
    
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
        "groq_activ": bool(groq_client)
    }

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(biserica_id: str = "default"):
    # Preluăm istoricul specific pentru biserica cerută
    istoric_biserica = biserici_history.get(biserica_id, [])
    history_html = "".join([f"<p style='border-bottom:1px solid #eee; padding:5px; margin:0;'>⏱️ {msg}</p>" for msg in reversed(istoric_biserica[-15:])])
    
    listeners_count = len(biserici_listeners.get(biserica_id, set()))
    broadcaster_active = biserici_broadcasters.get(biserica_id) is not None
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
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
        <h2>⛪ Panou Administrativ: {biserica_id}</h2>
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
    
    if biserica_id not in biserici_listeners:
        biserici_listeners[biserica_id] = set()
    if biserica_id not in biserici_history:
        biserici_history[biserica_id] = []
        
    if is_broadcaster:
        biserici_broadcasters[biserica_id] = websocket
        print(f"⛪ Sursa principala conectata pentru: {biserica_id}")
    else:
        biserici_listeners[biserica_id].add(websocket)
        print(f"📱 Enorias conectat la {biserica_id}. Total: {len(biserici_listeners[biserica_id])}")
        
    audio_buffer = io.BytesIO()
    
    try:
        if not groq_client:
            await websocket.send_json({"error": "GROQ_API_KEY lipseste pe server."})
            await websocket.close()
            return

        while True:
            audio_data = await websocket.receive_bytes()
            
            if not is_broadcaster and biserici_broadcasters.get(biserica_id) is not None:
                continue
                
            audio_buffer.write(audio_data)
            
            if audio_buffer.tell() > 102400:
                audio_buffer.seek(0)
                audio_bytes = audio_buffer.read()
                
                try:
                    loop = asyncio.get_running_loop()
                    transcription = await loop.run_in_executor(
                        None,
                        lambda: groq_client.audio.transcriptions.create(
                            file=("live_audio.wav", audio_bytes),
                            model="whisper-large-v3",
                            language="ro",
                            temperature=0.0,
                            prompt=LITURGICAL_PROMPT
                        )
                    )
                    
                    text_recunoscut = transcription.text.strip()
                    
                    if text_recunoscut:
                        print(f"🎙️ [{biserica_id}] Transcris: {text_recunoscut}")
                        biserici_history[biserica_id].append(text_recunoscut)
                        payload = {"text": text_recunoscut}
                        
                        await websocket.send_json(payload)
                        
                        if is_broadcaster and biserici_listeners[biserica_id]:
                            await asyncio.gather(
                                *[listener.send_json(payload) for listener in biserici_listeners[biserica_id]],
                                return_exceptions=True
                            )
                except Exception as groq_err:
                    print(f"❌ Eroare Groq [{biserica_id}]: {groq_err}")
                    await websocket.send_json({"error": "Eroare procesare audio."})
                    
                audio_buffer = io.BytesIO()
                
    except WebSocketDisconnect:
        if is_broadcaster:
            biserici_broadcasters[biserica_id] = None
            print(f"🔌 Sursa principala s-a deconectat de la {biserica_id}")
        else:
            biserici_listeners[biserica_id].discard(websocket)
            print(f"🔌 Enorias deconectat de la {biserica_id}")
    except Exception as e:
        print(f"⚠️ Eroare generala [{biserica_id}]: {e}")
    finally:
        audio_buffer.close()
