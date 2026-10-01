import os
import json
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_keys = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_KEY_2")
]
valid_keys = [key for key in api_keys if key is not None and key != ""]
current_key_index = 0

if valid_keys:
    genai.configure(api_key=valid_keys[current_key_index])
    print(f"✅ {len(valid_keys)} clé(s) API chargée(s).")

def switch_api_key():
    global current_key_index
    if len(valid_keys) > 1:
        current_key_index = (current_key_index + 1) % len(valid_keys)
        genai.configure(api_key=valid_keys[current_key_index])
        print(f"🔄 Basculement d'urgence sur la clé API n°{current_key_index + 1}")

@app.get("/")
def read_root():
    return {"status": "✅ Moteur d'estimation BTP en ligne et prêt !"}

# LES 3 PORTES D'ENTRÉE SONT ICI (dont /chiffrer-page)
@app.post("/upload")
@app.post("/analyze")
@app.post("/chiffrer-page")
async def analyze_image(file: UploadFile = File(...)):
    if not valid_keys:
        raise HTTPException(status_code=500, detail="Aucune clé API configurée sur le serveur.")
    
    try:
        image_data = await file.read()
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = """
        Analyse cette image de devis de construction (BTP). 
        Extrais les articles, désignations, unités, quantités, prix unitaires et prix totaux.
        Renvoie UNIQUEMENT un tableau au format JSON valide, sous la forme d'une liste d'objets exacte :
        [{"designation": "...", "unite": "...", "quantite": ..., "prix_unitaire": ..., "prix_total": ...}]
        Ne mets pas de texte avant ni après, juste le JSON.
        """
        
        part = {"mime_type": file.content_type, "data": image_data}
        
        try:
            response = model.generate_content([prompt, part])
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                switch_api_key()
                response = model.generate_content([prompt, part])
            else:
                raise e
        
        texte_brut = response.text.replace('```json', '').replace('```', '').strip()
        return {"data": json.loads(texte_brut)}
        
    except Exception as e:
        print(f"Erreur serveur : {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'analyse : {str(e)}")
