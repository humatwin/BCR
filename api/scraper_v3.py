"""
Scraper V3 - Récupère TOUTES les catégories depuis la même page
"""

import httpx
from bs4 import BeautifulSoup
from typing import List, Dict
import re

async def fetch_all_rankings() -> Dict[str, List[Dict]]:
    """
    Récupère TOUTES les catégories depuis la page principale
    """
    
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22"
    
    print(f"🌐 Récupération de {url}\n")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    html_text = soup.get_text()
    
    # Afficher un échantillon pour voir la structure
    print("📄 Échantillon de la page:")
    lines = html_text.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        if line and any(keyword in line.upper() for keyword in ['SINGLE', 'DOUBLE', 'RANK', 'PLAYER']):
            print(f"   Ligne {i}: {line}")
        if i > 200:
            break
    
    print("\n" + "="*60 + "\n")
    
    # Trouver tous les titres de catégories
    all_categories = {}
    
    # Chercher les sections avec h2, h3, ou div class avec les catégories
    for header in soup.find_all(['h2', 'h3', 'h4', 'div', 'span', 'td', 'th']):
        header_text = header.get_text().strip().upper()
        
        category_code = None
        if "MEN'S SINGLES" in header_text or "MEN SINGLES" in header_text:
            category_code = "MS"
        elif "WOMEN'S SINGLES" in header_text or "LADIES SINGLES" in header_text:
            category_code = "WS"
        elif "MEN'S DOUBLES" in header_text:
            category_code = "MD"
        elif "WOMEN'S DOUBLES" in header_text or "LADIES DOUBLES" in header_text:
            category_code = "WD"
        elif "MIXED DOUBLES" in header_text:
            category_code = "XD"
        
        if category_code:
            print(f"✅ Catégorie trouvée: {category_code} dans <{header.name}>")
            
            # Chercher la table suivante
            table = header.find_next('table')
            
            if table:
                rankings = parse_table(table, category_code)
                if rankings:
                    all_categories[category_code] = rankings
                    print(f"   📊 {len(rankings)} joueurs trouvés")
                    for r in rankings[:3]:
                        print(f"      {r['rank']}. {r['name']}")
    
    return all_categories

def parse_table(table, category_code: str) -> List[Dict]:
    """Parse une table et extrait les rankings"""
    
    rankings = []
    rows = table.find_all('tr')
    
    for row in rows:
        cells = row.find_all(['td', 'th'])
        
        if len(cells) < 2:
            continue
        
        # Extraire texte non-vide
        cell_texts = [cell.get_text(strip=True) for cell in cells if cell.get_text(strip=True)]
        
        if len(cell_texts) < 2:
            continue
        
        # Chercher rang et nom
        rank = None
        name = None
        points = 0.0
        
        for i, text in enumerate(cell_texts):
            if text.isdigit() and 1 <= int(text) <= 500:
                rank = int(text)
                if i + 1 < len(cell_texts):
                    next_text = cell_texts[i + 1]
                    if not next_text.replace('.', '').isdigit() and len(next_text) > 2:
                        name = next_text
                        # Points souvent dans la cellule d'après
                        if i + 2 < len(cell_texts):
                            try:
                                points_text = cell_texts[i + 2].replace(',', '')
                                points = float(points_text)
                            except:
                                pass
                break
        
        if rank and name:
            rankings.append({
                "rank": rank,
                "name": name,
                "points": points,
                "category": category_code
            })
    
    return rankings

# Test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        print("🧪 TEST DU SCRAPER V3 - TOUTES CATÉGORIES\n")
        print("="*60 + "\n")
        
        all_data = await fetch_all_rankings()
        
        print("\n" + "="*60)
        print("📊 RÉSULTATS FINAUX")
        print("="*60 + "\n")
        
        for cat, rankings in all_data.items():
            cat_names = {
                "MS": "Simple Hommes",
                "WS": "Simple Femmes",
                "MD": "Double Hommes",
                "WD": "Double Femmes",
                "XD": "Double Mixte"
            }
            print(f"\n🏆 {cat_names.get(cat, cat)} ({cat}): {len(rankings)} joueurs")
            for r in rankings[:5]:
                print(f"   {r['rank']}. {r['name']} ({r['points']} pts)")
    
    asyncio.run(test())
