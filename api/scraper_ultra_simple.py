"""
Scraper ULTRA SIMPLE - Ne se fie PAS aux en-têtes, juste aux patterns
"""

import httpx
from bs4 import BeautifulSoup
from typing import List, Dict
import re

async def fetch_all_rankings() -> Dict[str, List[Dict]]:
    """Récupère toutes les catégories"""
    
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22"
    
    print(f"🌐 Récupération de {url}\n")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    all_categories = {}
    
    category_mapping = {
        "MEN'S SINGLES": "MS",
        "WOMEN'S SINGLES": "WS",
        "MEN'S DOUBLES": "MD",
        "WOMEN'S DOUBLES": "WD",
        "MIXED DOUBLES": "XD"
    }
    
    for category_name, category_code in category_mapping.items():
        print(f"🔍 {category_name}")
        
        # Chercher l'en-tête
        for element in soup.find_all(['th', 'div', 'a']):
            if category_name in element.get_text().upper():
                table = element.find_next('table')
                
                if table:
                    rankings = parse_simple(table, category_code)
                    
                    if rankings and category_code not in all_categories:
                        all_categories[category_code] = rankings
                        print(f"   ✅ {len(rankings)} joueurs")
                        for r in rankings[:3]:
                            print(f"      {r['rank']}. {r['name']} ({r['points']} pts)")
                        break
    
    return all_categories

def parse_simple(table, category_code: str) -> List[Dict]:
    """
    Parse SIMPLE: détecte automatiquement les colonnes sur la première ligne de données
    
    Structure attendue:
    ['1', '', '', 'Victor Lai', '', 'ON13010', '11180', '3', 'Mandarin Badminton']
    
    Règles:
    - Rank = premier nombre entre 1 et 999
    - Player = première chaîne non-numérique de >2 caractères
    - Points = nombre > 1000 (les points sont souvent > 1000)
    """
    
    rankings = []
    rows = table.find_all('tr')
    
    # Skipper les 2 premières lignes (titre + en-têtes)
    for row in rows[2:]:
        cells = row.find_all(['td', 'th'])
        
        if len(cells) < 3:
            continue
        
        cell_texts = [c.get_text(strip=True) for c in cells]
        
        # Trouver rang (premier nombre < 1000)
        rank = None
        for text in cell_texts:
            if text.isdigit() and 1 <= int(text) < 1000:
                rank = int(text)
                break
        
        # Trouver nom (première chaîne > 2 caractères qui n'est PAS un nombre)
        player_name = None
        for text in cell_texts:
            # Vérifier que ce n'est pas un nombre, un ID, ou trop court
            if (len(text) > 2 and 
                not text.replace('.', '').isdigit() and 
                not re.match(r'^[A-Z]{2}\d+$', text) and  # Pas un ID comme "ON13010"
                ' ' in text or len(text) > 10):  # Contient un espace (prénom nom) OU est long
                player_name = text
                break
        
        # Trouver points (nombre > 1000)
        points = 0.0
        for text in cell_texts:
            clean = text.replace(',', '').strip()
            if clean.isdigit():
                val = int(clean)
                if val >= 1000:  # Les points sont habituellement >= 1000
                    points = float(val)
                    break
        
        # Ajouter si valide
        if rank and player_name:
            rankings.append({
                "rank": rank,
                "name": player_name,
                "points": points,
                "category": category_code
            })
    
    return rankings

# Test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        print("🧪 SCRAPER ULTRA SIMPLE\n" + "="*60 + "\n")
        
        all_data = await fetch_all_rankings()
        
        print("\n" + "="*60)
        print("📊 RÉSULTATS")
        print("="*60 + "\n")
        
        cat_names = {
            "MS": "Simple Hommes",
            "WS": "Simple Femmes",
            "MD": "Double Hommes",
            "WD": "Double Femmes",
            "XD": "Double Mixte"
        }
        
        for cat in ["MS", "WS", "MD", "WD", "XD"]:
            if cat in all_data:
                rankings = all_data[cat]
                print(f"\n🏆 {cat_names[cat]} ({cat}): {len(rankings)} joueurs")
                for r in rankings[:10]:
                    print(f"   {r['rank']:2d}. {r['name']:30s} {r['points']:,.0f} pts")
            else:
                print(f"\n❌ {cat_names[cat]} ({cat}): AUCUNE DONNÉE")
    
    asyncio.run(test())
