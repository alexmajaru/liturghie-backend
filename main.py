import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import psycopg2
from groq import Groq

app = FastAPI()

# Preluăm variabilele de mediu setate pe Render
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Inițializăm clientul Groq (dacă cheia există)
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Initializam clientul Groq folosind cheia secreta din mediul cloud
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.get("/")
def home():
    return {"status": "Serverul Liturghie pe Koyeb este online si functioneaza!"}

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

# Endpoint WebSocket pregătit pentru viitoarea conexiune din aplicația Android

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Aplicația Android s-a conectat prin WebSocket.")
    try:
        while True:
            # Serverul primeste bucati audio binare de la telefon
            audio_data = await websocket.receive_bytes()
            
            # Salvam temporar bucata audio primita pentru a o trimite la IA
            temp_filename = "temp_chunk.wav"
            with open(temp_filename, "wb") as f:
                f.write(audio_data)
            
            # Trimitem fisierul audio catre Groq API (Whisper) pentru transcriere gratuita
            with open(temp_filename, "rb") as file:
                transcription = groq_client.audio.transcriptions.create(
                    file=(temp_filename, file.read()),
                    model="whisper-large-v3",
                    language="ro", # Fortam modelul sa asculte in limba romana
                    temperature=0.0
                )
            
            # Eliminam fisierul temporar
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
                
            # Trimitem textul recunoscut inapoi la telefon in timp real
            text_recunoscut = transcription.text
            await websocket.send_json({"text": text_recunoscut})
            
    except WebSocketDisconnect:
        print("Telefon deconectat.")
    except Exception as e:
        print(f"Eroare: {e}")
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
