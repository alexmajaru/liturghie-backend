import os
import io
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from groq import Groq

app = FastAPI(title="Liturghie Audio Sync Server")

# Adăugăm CORS pentru a permite conexiunile din aplicația mobilă Android
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Preluăm variabilele de mediu setate pe Render
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Inițializăm clientul Groq în siguranță
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

def get_db_connection():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL)

@app.get("/")
def read_root():
    # Testăm conexiunea la baza de date Supabase
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
        "status": "Serverul Liturghie este online și funcționează!",
        "database": db_status,
        "groq_activ": bool(groq_client)
    }

# Endpoint WebSocket optimizat
@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🚀 Aplicația Android s-a conectat prin WebSocket.")
    
    # Buffer în memorie pentru a acumula pachetele audio primite
    audio_buffer = io.BytesIO()
    
    try:
        if not groq_client:
            await websocket.send_json({"error": "GROQ_API_KEY lipsește de pe server."})
            await websocket.close()
            return

        while True:
            # Serverul primește bucăți audio binare direct de la telefon
            audio_data = await websocket.receive_bytes()
            audio_buffer.write(audio_data)
            
            # Trimitem la Groq când acumulăm o bucată suficientă (~100 KB sau aprox 2-3 secunde de vorbire)
            if audio_buffer.tell() > 102400:
                audio_buffer.seek(0)
                audio_bytes = audio_buffer.read()
                
                try:
                    # Trimitem direct din memorie către Groq API (Whisper), fără fișiere pe disc
                    transcription = groq_client.audio.transcriptions.create(
                        file=("live_audio.wav", audio_bytes),
                        model="whisper-large-v3",
                        language="ro",  # Forțăm limba română pentru termeni liturgici
                        temperature=0.0
                    )
                    
                    text_recunoscut = transcription.text.strip()
                    
                    if text_recunoscut:
                        print(f"🎙️ Transcris: {text_recunoscut}")
                        # Trimitem textul recunoscut înapoi la telefon în timp real
                        await websocket.send_json({"text": text_recunoscut})
                        
                except Exception as groq_err:
                    print(f"❌ Eroare API Groq: {groq_err}")
                    await websocket.send_json({"error": "Eroare la procesarea audio."})
                
                # Resetăm buffer-ul din memorie pentru următorul segment
                audio_buffer = io.BytesIO()
            
    except WebSocketDisconnect:
        print("🔌 Telefon deconectat de la WebSocket.")
    except Exception as e:
        print(f"⚠️ Eroare generală: {e}")
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
    finally:
        audio_buffer.close()
     
