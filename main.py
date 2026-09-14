import os
import json
import base64
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from supabase import create_client, Client
from google import genai
from google.genai import types

# Configuration des logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("saas_btp")

# Configuration des variables (Remplacez avec vos vraies valeurs)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://votre-projet.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "ecle_supabase_placeholder")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "cle_gemini_placeholder")

# Initialisation Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialisation du client officiel Google GenAI
ai_client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="SaaS Estimation BTP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Jeton d'authentification manquant"
        )
    token = authorization.split(" ")[1]
    
    try:
        user_res = supabase.auth.get_user(token)
        if user_res and user_res.user:
            return user_res.user
    except Exception as e:
        logger.warning(f"Verification Supabase get_user echouee: {str(e)}")

    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_data = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        user_id = payload_data.get("sub")
        if user_id:
            class MinimalUser:
                def __init__(self, uid):
                    self.id = uid
            return MinimalUser(user_id)
    except Exception as e_jwt:
        logger.error(f"Erreur de décodage JWT: {str(e_jwt)}")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, 
        detail="Session invalide ou expirée"
    )

@app.post("/api/scan")
async def scan_devis(file: UploadFile = File(...), current_user = Depends(get_current_user)):
    try:
        content = await file.read()
        mime_type = file.content_type or "image/jpeg"

        prompt_text = """
        Tu es un expert en métré et devis BTP (Bâtiment et Travaux Publics). 
        Analyse ce document/devis et extrait les éléments sous forme de JSON strict avec cette structure :
        {
            "client_nom": "Nom du client",
            "date": "YYYY-MM-DD",
            "articles": [
                {
                    "designation": "Nom du poste / travaux",
                    "unite": "m², m³, ens, u, etc.",
                    "quantite": 0.0,
                    "prix_unitaire_ht": 0.0,
                    "prix_total_ht": 0.0
                }
            ],
            "total_ht": 0.0,
            "tva": 0.0,
            "total_ttc": 0.0
        }
        Indique tous les prix en DA algerien.
        Réponds uniquement avec le JSON.
        """

        # Utilisation de l'API standard Google GenAI avec gemini-2.5-flash
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(data=content, mime_type=mime_type),
                prompt_text
            ]
        )
        response_text = response.text

        clean_json = response_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)

        devis_db = {
            "user_id": current_user.id,
            "filename": file.filename,
            "total_ht": data.get("total_ht", 0.0),
            "total_ttc": data.get("total_ttc", 0.0),
            "donnees_json": data
        }
        supabase.table("devis_analyses").insert(devis_db).execute()

        return {"status": "success", "data": data}

    except Exception as e:
        logger.error(f"Erreur lors du scan : {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur traitement devis : {str(e)}")

@app.get("/api/historique")
async def get_historique(current_user = Depends(get_current_user)):
    try:
        res = supabase.table("devis_analyses").select("*").eq("user_id", current_user.id).order("created_at", desc=True).execute()
        return res.data
    except Exception as e:
        logger.error(f"Erreur historique : {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération de l'historique")

@app.get("/")
async def read_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"message": "Serveur API BTP opérationnel"}