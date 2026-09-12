import os
import json
import logging
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Header, status, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client, Client
from google import genai
from google.genai import types

# Configuration des logs pour suivre la santé de l'application
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("saas_btp")

app = FastAPI(title="SaaS Estimation BTP Algérie")

# CORS sécurisé
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# 1. CONFIGURATION DES CLÉS (Privilégier les variables d'environnement en prod)
# ==============================================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://votre-projet.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "votre-cle-anon-supabase")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "votre-cle-api-gemini")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

BASE_DIR = Path(__file__).resolve().parent

# ==============================================================================
# 2. MODÈLES DE DONNÉES
# ==============================================================================
class PosteDevis(BaseModel):
    numero: Optional[str] = None
    designation: str
    unite: Optional[str] = None
    quantite: float = 0.0
    prix_unitaire_ht: float = 0.0
    prix_total_ht: float = 0.0

class ExtraireBordereauResponse(BaseModel):
    postes: List[PosteDevis]

# ==============================================================================
# 3. AUTHENTIFICATION SÉCURISÉE
# ==============================================================================
def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Jeton d'authentification manquant"
        )
    token = authorization.split(" ")[1]
    try:
        user_res = supabase.auth.get_user(token)
        if not user_res.user:
            raise HTTPException(status_code=401, detail="Session invalide")
        return user_res.user
    except Exception as e:
        logger.error(f"Erreur Auth: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Session expirée ou invalide"
        )

# ==============================================================================
# 4. ROUTES API RENFORCÉES
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
async def read_index():
    """Route d'accueil."""
    html_file = BASE_DIR / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Erreur : Fichier index.html introuvable</h1>"

@app.post("/api/scan")
async def scan_document(
    file: UploadFile = File(...), 
    taux_tva: float = Form(19.0),
    authorization: str = Header(None),
    user=Depends(get_current_user)
):
    """Scan par IA avec Tolérance aux Pannes, Nettoyage et Fallback 503."""
    
    # 1. Contrôle de taille (Max 10 Mo)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux (Max 10 Mo).")

    mime_type = file.content_type or "image/png"
    token = authorization.split(" ")[1]
    
    user_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    user_supabase.postgrest.auth(token)
    
    prompt_text = (
        "Extrais soigneusement tous les postes du bordereau/devis BTP dans cette image. "
        "Fournis le numéro, la désignation, l'unité, la quantité, le prix unitaire HT et le prix total HT pour chaque ligne."
    )
    gen_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ExtraireBordereauResponse,
    )

    response_text = None

    # 2. Reessais automatiques et Fallback de modèle IA
    try:
        try:
            logger.info("Tentative scan avec gemini-3.6-flash...")
            response = ai_client.models.generate_content(
                model='gemini-3.6-flash',
                contents=[types.Part.from_bytes(data=content, mime_type=mime_type), prompt_text],
                config=gen_config,
            )
            response_text = response.text
        except Exception as e_flash:
            # En cas de 503 / Surcharge, bascule instantanée sur 2.5-flash
            if "503" in str(e_flash) or "UNAVAILABLE" in str(e_flash):
                logger.warning("gemini-3.6-flash indisponible (503). Bascule sur gemini-2.5-flash...")
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[types.Part.from_bytes(data=content, mime_type=mime_type), prompt_text],
                    config=gen_config,
                )
                response_text = response.text
            else:
                raise e_flash

        # 3. Extraction et validation sécurisée du JSON
        if not response_text:
            raise ValueError("L'IA a retourné une réponse vide.")

        result_data = json.loads(response_text)
        postes = result_data.get("postes", [])
        
        # Recalcul du Total HT de sécurité côté serveur
        total_ht = sum(float(p.get("prix_total_ht", 0.0)) for p in postes)
        montant_tva = total_ht * (taux_tva / 100.0)
        total_ttc = total_ht + montant_tva
        
        # 4. Insertion en BDD Supabase
        db_res = user_supabase.table("devis_analyses").insert({
            "user_id": user.id,
            "filename": file.filename,
            "postes": postes,
            "total_ht": round(total_ht, 2),
            "taux_tva": taux_tva,
            "total_ttc": round(total_ttc, 2)
        }).execute()
        
        return {
            "id": db_res.data[0]["id"] if db_res.data else None,
            "filename": file.filename,
            "postes": postes,
            "total_ht": round(total_ht, 2)
        }
        
    except json.JSONDecodeError:
        logger.error("Erreur de formatage JSON retourné par l'IA.")
        raise HTTPException(status_code=502, detail="L'image scannée n'a pas pu être convertie en données chiffrées structurées. Veuillez réorganiser l'image ou réessayer.")
    except Exception as e:
        logger.error(f"Erreur interne lors du scan: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Service temporairement indisponible : {str(e)}")

@app.get("/api/historique")
async def get_historique(
    authorization: str = Header(None),
    user=Depends(get_current_user)
):
    """Récupère l'historique sécurisé par utilisateur."""
    token = authorization.split(" ")[1]
    user_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    user_supabase.postgrest.auth(token)
    
    try:
        res = user_supabase.table("devis_analyses") \
            .select("*") \
            .eq("user_id", user.id) \
            .order("created_at", ascending=False) \
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"Erreur historique: {str(e)}")
        raise HTTPException(status_code=500, detail="Impossible de charger l'historique.")