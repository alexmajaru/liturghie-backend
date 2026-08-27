import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from groq import Groq

app = FastAPI()

# Initializam clientul Groq folosind cheia secreta din mediul cloud
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.get("/")
def home():
    return {"status": "Serverul Liturghie pe Koyeb este online si functioneaza!"}

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Telefon conectat prin WebSocket.")
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
