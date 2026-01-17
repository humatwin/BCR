"""
BCR API - Version simplifiée qui FONCTIONNE
Parse les données du site Badminton Canada correctement
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = FastAPI(title="BCR API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Modèles
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

# Cache simple
cache = {}
CACHE_DURATION = timedelta(hours=1)

async def scrape_rankings_simple(category: str) -> List[RankingEntry]:
    """Scraping SIMPLIFIÉ qui fonctionne"""
    
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22"
    
    print(f"🌐 Scraping: {url} pour {category}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table')
        
        print(f"📊 {len(tables)} tables trouvées")
        
        rankings = []
        
        for table_idx, table in enumerate(tables):
            # Chercher le titre avant la table
            context_elements = []
            for elem in table.find_all_previous(['h1', 'h2', 'h3', 'div', 'span'])[:10]:
                text = elem.get_text(strip=True)
                if text:
                    context_elements.append(text.upper())
            
            context = " ".join(context_elements)
            table_text = table.get_text().upper()
            
            # Détection de catégorie SIMPLE
            is_correct_category = False
            
            if category == "MS":
                if "MEN" in context and "SINGLE" in context and "DOUBLE" not in context:
                    is_correct_category = True
            elif category == "WS":
                if "WOMEN" in context and "SINGLE" in context and "DOUBLE" not in context:
                    is_correct_category = True
            elif category == "MD":
                if "MEN" in context and "DOUBLE" in context and "MIXED" not in context:
                    is_correct_category = True
            elif category == "WD":
                if "WOMEN" in context and "DOUBLE" in context and "MIXED" not in context:
                    is_correct_category = True
            elif category == "XD":
                if "MIXED" in context:
                    is_correct_category = True
            
            if not is_correct_category:
                continue
            
            print(f"✅ Table {table_idx + 1} correspond à {category}")
            
            # Parser les lignes de la table
            rows = table.find_all('tr')
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                
                # Extraire TOUTES les cellules et filtrer les vides
                cell_texts = [cell.get_text(strip=True) for cell in cells]
                cell_texts = [text for text in cell_texts if text]  # Supprimer les vides
                
                if len(cell_texts) < 2:
                    continue
                
                try:
                    # Format: [Rank, Name, Points, ...]
                    # Trouver le rang (premier nombre)
                    rank = None
                    name = None
                    points = 0.0
                    
                    for i, text in enumerate(cell_texts):
                        # Si c'est un nombre 1-500, c'est probablement le rang
                        if text.isdigit() and 1 <= int(text) <= 500:
                            rank = int(text)
                            # Le nom est probablement après
                            if i + 1 < len(cell_texts):
                                next_text = cell_texts[i + 1]
                                if not next_text.isdigit() and len(next_text) > 2:
                                    name = next_text
                            # Les points probablement encore après
                            if i + 2 < len(cell_texts):
                                points_text = cell_texts[i + 2]
                                try:
                                    points = float(points_text.replace(',', ''))
                                except:
                                    pass
                            break
                    
                    if rank and name:
                        rankings.append(RankingEntry(
                            rank=rank,
                            player_name=name,
                            points=points,
                            province=None,
                            previous_rank=None,
                            player_id=None
                        ))
                        
                except Exception as e:
                    continue
            
            if rankings:
                break  # On a trouvé la bonne table
        
        print(f"✅ {len(rankings)} rankings extraits pour {category}")
        return rankings
        
    except Exception as e:
        print(f"❌ Erreur: {str(e)}")
        return []

@app.get("/")
async def root():
    return {
        "message": "BCR API v2 - Simplified & Working",
        "version": "2.0.0"
    }

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/rankings/{category}", response_model=RankingResponse)
async def get_rankings(category: str, scope: str = "national"):
    category = category.upper()
    
    if category not in ["MS", "WS", "MD", "WD", "XD"]:
        raise HTTPException(status_code=400, detail="Catégorie invalide")
    
    # Vérifier le cache
    cache_key = f"{scope}_{category}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data
    
    # Scraper les données
    rankings = await scrape_rankings_simple(category)
    
    response = RankingResponse(
        category=category,
        scope=scope,
        last_updated=datetime.now().isoformat(),
        rankings=rankings,
        total_count=len(rankings)
    )
    
    # Mettre en cache
    cache[cache_key] = (response, datetime.now())
    
    return response

@app.get("/rankings/{category}/national", response_model=RankingResponse)
async def get_national(category: str):
    return await get_rankings(category, scope="national")

@app.post("/cache/clear")
async def clear_cache():
    cache.clear()
    return {"message": "Cache vidé"}

if __name__ == "__main__":
    import uvicorn
    print("🚀 BCR API v2 sur http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
