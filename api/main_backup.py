"""
BCR API - Backend pour scraper Badminton Canada
Utilise FastAPI + BeautifulSoup pour récupérer les rankings
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import asyncio
from functools import lru_cache

app = FastAPI(title="BCR API", version="1.0.0")

# CORS pour permettre les requêtes depuis l'app iOS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================

class RankingEntry(BaseModel):
    rank: int
    player_name: str
    points: float
    province: Optional[str] = None
    previous_rank: Optional[int] = None
    player_id: Optional[str] = None

class RankingResponse(BaseModel):
    category: str
    scope: str
    last_updated: str
    rankings: List[RankingEntry]
    total_count: int

# ============================================================================
# CACHE SIMPLE (éviter de surcharger le site)
# ============================================================================

cache = {}
CACHE_DURATION = timedelta(hours=1)

def get_from_cache(key: str):
    if key in cache:
        data, timestamp = cache[key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {key}")
            return data
    return None

def save_to_cache(key: str, data):
    cache[key] = (data, datetime.now())
    print(f"💾 Données mises en cache pour {key}")

# ============================================================================
# SCRAPING DU SITE BADMINTON CANADA
# ============================================================================

BASE_URL = "https://badmintoncanada.tournamentsoftware.com"

# IDs de ranking par catégorie (à ajuster selon le site)
RANKING_IDS = {
    "national": {
        "MS": 22,  # Men's Singles
        "WS": 22,  # Women's Singles (même page, filtrage HTML)
        "MD": 22,  # Men's Doubles
        "WD": 22,  # Women's Doubles
        "XD": 22,  # Mixed Doubles
    },
    "provincial": {
        "ON": 33,  # Ontario
        "AB": 9,   # Alberta
        "NB": 328, # New Brunswick
    }
}

async def scrape_rankings(category: str, scope: str = "national", province: str = None) -> List[RankingEntry]:
    """
    Scrape les rankings depuis Tournament Software
    
    Args:
        category: MS, WS, MD, WD, XD
        scope: national ou provincial
        province: Code de province (ON, AB, etc.) si scope=provincial
    """
    
    # Vérifier le cache
    cache_key = f"{scope}_{category}_{province or 'all'}"
    cached_data = get_from_cache(cache_key)
    if cached_data:
        return cached_data
    
    # Déterminer le RID (Ranking ID)
    if scope == "national":
        rid = RANKING_IDS["national"].get(category, 22)
    else:
        rid = RANKING_IDS["provincial"].get(province, 33)
    
    url = f"{BASE_URL}/ranking/ranking.aspx?rid={rid}"
    
    print(f"🌐 Scraping: {url} pour {category}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # Stratégie 1: Chercher par titre de section
        rankings = []
        
        # Trouver toutes les tables
        tables = soup.find_all('table')
        print(f"📊 {len(tables)} tables trouvées")
        
        for table_idx, table in enumerate(tables):
            # Chercher le titre/contexte avant la table
            context = ""
            prev = table.find_previous(['h1', 'h2', 'h3', 'h4', 'div', 'span'])
            if prev:
                context = prev.get_text(strip=True).upper()
            
            table_text = str(table).upper()
            combined = (context + " " + table_text).upper()
            
            # Déterminer si cette table correspond à la catégorie
            is_correct = False
            
            if category == "MS":
                is_correct = ("MEN" in combined and "SINGLE" in combined and 
                             "WOMEN" not in combined and "DOUBLE" not in combined)
            elif category == "WS":
                is_correct = ("WOMEN" in combined and "SINGLE" in combined and 
                             "MEN'S" not in combined and "DOUBLE" not in combined)
            elif category == "MD":
                is_correct = ("MEN" in combined and "DOUBLE" in combined and 
                             "WOMEN" not in combined and "MIXED" not in combined)
            elif category == "WD":
                is_correct = ("WOMEN" in combined and "DOUBLE" in combined and 
                             "MEN'S" not in combined and "MIXED" not in combined)
            elif category == "XD":
                is_correct = "MIXED" in combined or "MIXTE" in combined
            
            if not is_correct:
                continue
            
            print(f"✅ Table {table_idx + 1} correspond à {category}")
            
            # Parser les lignes de cette table
            rows = table.find_all('tr')
            
            for row_idx, row in enumerate(rows):
                cells = row.find_all(['td', 'th'])
                
                # Ignorer les lignes vides ou avec trop peu de cellules
                if len(cells) < 2:
                    continue
                
                # Afficher le contenu des cellules pour debug
                if row_idx < 5:  # Afficher les 5 premières lignes pour debug
                    cell_texts = [cell.get_text(strip=True)[:30] for cell in cells[:5]]
                    print(f"      Ligne {row_idx}: {' | '.join(cell_texts)}")
                
                # Ignorer les en-têtes (lignes qui contiennent "Rank", "Name", "Points", etc.)
                first_cell_text = cells[0].get_text(strip=True).upper()
                if any(word in first_cell_text for word in ['RANK', 'POS', 'PLACE', '#']):
                    if len(first_cell_text) < 10:  # C'est probablement un en-tête
                        continue
                
                # Extraire les données
                try:
                    # Essayer différentes configurations de colonnes
                    rank_text = ""
                    name_text = ""
                    points_text = "0"
                    province_text = None
                    
                    # Chercher le rang dans les premières cellules
                    for i in range(min(2, len(cells))):
                        text = cells[i].get_text(strip=True)
                        # Si c'est un nombre pur, c'est probablement le rang
                        if text.isdigit() and 1 <= int(text) <= 500:
                            rank_text = text
                            # Le nom est probablement dans la cellule suivante
                            if i + 1 < len(cells):
                                name_text = cells[i + 1].get_text(strip=True)
                            # Les points dans la suivante
                            if i + 2 < len(cells):
                                points_text = cells[i + 2].get_text(strip=True)
                            # Province éventuellement
                            if i + 3 < len(cells):
                                province_text = cells[i + 3].get_text(strip=True)
                            break
                    
                    # Si pas trouvé, essayer le format standard
                    if not rank_text and len(cells) >= 3:
                        rank_text = cells[0].get_text(strip=True)
                        name_text = cells[1].get_text(strip=True)
                        points_text = cells[2].get_text(strip=True)
                        if len(cells) > 3:
                            province_text = cells[3].get_text(strip=True)
                    
                    # Nettoyer et convertir
                    rank_digits = ''.join(filter(str.isdigit, rank_text))
                    if not rank_digits:
                        continue
                    rank = int(rank_digits)
                    
                    # Nettoyer les points
                    points_clean = points_text.replace(',', '').replace(' ', '')
                    points_digits = ''.join(filter(lambda x: x.isdigit() or x == '.', points_clean))
                    points = float(points_digits) if points_digits else 0.0
                    
                    # Vérifier que le nom est valide
                    if name_text and len(name_text) > 2 and not name_text.isdigit():
                        rankings.append(RankingEntry(
                            rank=rank,
                            player_name=name_text,
                            points=points,
                            province=province_text if province_text and len(province_text) <= 3 else None,
                            previous_rank=None,
                            player_id=None
                        ))
                        
                except (ValueError, IndexError) as e:
                    print(f"      ⚠️  Erreur ligne {row_idx}: {e}")
                    continue
            
            if rankings:
                break  # On a trouvé la bonne table
        
        print(f"✅ {len(rankings)} rankings extraits pour {category}")
        
        # Mettre en cache
        save_to_cache(cache_key, rankings)
        
        return rankings
        
    except Exception as e:
        print(f"❌ Erreur de scraping: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur de scraping: {str(e)}")

# ============================================================================
# ENDPOINTS API
# ============================================================================

@app.get("/")
async def root():
    return {
        "message": "BCR API - Badminton Canada Rankings",
        "version": "1.0.0",
        "endpoints": {
            "/rankings/{category}": "Obtenir les rankings par catégorie (MS, WS, MD, WD, XD)",
            "/rankings/{category}/national": "Rankings nationaux",
            "/rankings/{category}/provincial/{province}": "Rankings provinciaux",
            "/health": "Health check"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/rankings/{category}", response_model=RankingResponse)
async def get_rankings(category: str, scope: str = "national", province: Optional[str] = None):
    """
    Récupérer les rankings pour une catégorie
    
    Args:
        category: MS, WS, MD, WD, XD
        scope: national ou provincial
        province: Code de province (ON, AB, NB) si scope=provincial
    """
    category = category.upper()
    
    if category not in ["MS", "WS", "MD", "WD", "XD"]:
        raise HTTPException(status_code=400, detail="Catégorie invalide. Utilisez: MS, WS, MD, WD, XD")
    
    rankings = await scrape_rankings(category, scope, province)
    
    return RankingResponse(
        category=category,
        scope=scope,
        last_updated=datetime.now().isoformat(),
        rankings=rankings,
        total_count=len(rankings)
    )

@app.get("/rankings/{category}/national", response_model=RankingResponse)
async def get_national_rankings(category: str):
    """Raccourci pour les rankings nationaux"""
    return await get_rankings(category, scope="national")

@app.get("/rankings/{category}/provincial/{province}", response_model=RankingResponse)
async def get_provincial_rankings(category: str, province: str):
    """Raccourci pour les rankings provinciaux"""
    return await get_rankings(category, scope="provincial", province=province)

@app.post("/cache/clear")
async def clear_cache():
    """Vider le cache (pour forcer le rafraîchissement)"""
    cache.clear()
    return {"message": "Cache vidé", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    print("🚀 Démarrage du serveur BCR API sur http://localhost:8000")
    print("📖 Documentation: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
