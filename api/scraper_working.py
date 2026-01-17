"""
Scraper qui FONCTIONNE - Utilise les URLs directes des catégories
"""

import httpx
from bs4 import BeautifulSoup
from typing import List, Dict
import re

# URLs directes pour chaque catégorie (à partir de rid=22, id=49797)
CATEGORY_URLS = {
    "MS": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=151",
    "WS": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=152",
    "MD": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=153",
    "WD": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=154",
    "XD": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=155"
}

async def fetch_all_rankings() -> Dict[str, List[Dict]]:
    """Récupère toutes les catégories en utilisant leurs URLs directes"""
    
    all_categories = {}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for category_code, url in CATEGORY_URLS.items():
            print(f"🔍 {category_code}: {url}")
            
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                rankings = parse_ranking_table(soup, category_code)
                
                if rankings:
                    all_categories[category_code] = rankings
                    print(f"   ✅ {len(rankings)} joueurs")
                    for r in rankings[:3]:
                        print(f"      {r['rank']}. {r['name']} ({r['points']} pts)")
                else:
                    print(f"   ❌ Aucune donnée")
            
            except Exception as e:
                print(f"   ❌ Erreur: {e}")
    
    return all_categories

def parse_ranking_table(soup, category_code: str) -> List[Dict]:
    """
    Parse la table de rankings
    Structure: ['1', '', '', 'Victor Lai', '', 'ON13010', '11180', '3', 'Mandarin Badminton']
    """
    
    rankings = []
    
    # Trouver la table
    table = soup.find('table')
    
    if not table:
        print("      ⚠️ Aucune table trouvée")
        return []
    
    rows = table.find_all('tr')
    print(f"      📊 {len(rows)} lignes dans la table")
    
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
        
        # Trouver nom (première chaîne qui ressemble à un nom)
        player_name = None
        for text in cell_texts:
            if (len(text) > 2 and 
                not text.replace('.', '').replace(',', '').isdigit() and 
                not re.match(r'^[A-Z]{2}\d+$', text)):  # Pas un ID
                
                # Vérifier que c'est un nom (contient espace OU caractères alphabétiques > 50%)
                alpha_count = sum(c.isalpha() or c.isspace() for c in text)
                if alpha_count > len(text) * 0.5:
                    player_name = text
                    break
        
        # Trouver points (nombre > 1000)
        points = 0.0
        for text in cell_texts:
            clean = text.replace(',', '').strip()
            if clean.isdigit():
                val = int(clean)
                if val >= 1000:
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
        print("🧪 SCRAPER AVEC URLs DIRECTES\n" + "="*60 + "\n")
        
        all_data = await fetch_all_rankings()
        
        print("\n" + "="*60)
        print("📊 RÉSULTATS FINAUX")
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
                    print(f"   {r['rank']:2d}. {r['name']:35s} {r['points']:,.0f} pts")
            else:
                print(f"\n❌ {cat_names[cat]} ({cat}): AUCUNE DONNÉE")
    
    asyncio.run(test())
