"""
Scraper qui FONCTIONNE VRAIMENT pour Badminton Canada
Basé sur les tests réels du site
"""

import httpx
from bs4 import BeautifulSoup
from typing import List, Dict

async def fetch_real_rankings(category: str = "MS") -> List[Dict]:
    """
    Récupère les VRAIES données du site
    URL: https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22
    """
    
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22"
    
    print(f"🌐 Récupération de {url}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Trouver TOUTES les tables
    tables = soup.find_all('table')
    print(f"📊 {len(tables)} tables trouvées")
    
    rankings = []
    
    # La structure du site d'après les tests:
    # - La table contient "Men's singles" / "Women's singles" etc. dans la première ligne
    # - Puis "Rank | Player | Member ID" comme en-tête
    # - Puis les données : "1 | Victor Lai | ..."
    
    for table_idx, table in enumerate(tables):
        # Récupérer TOUT le texte de la table
        table_text = table.get_text().upper()
        
        # Vérifier quelle catégorie cette table contient
        category_match = None
        
        if "MEN'S SINGLES" in table_text or "MEN SINGLES" in table_text:
            category_match = "MS"
        elif "WOMEN'S SINGLES" in table_text or "WOMEN SINGLES" in table_text or "LADIES SINGLES" in table_text:
            category_match = "WS"
        elif "MEN'S DOUBLES" in table_text or "MEN DOUBLES" in table_text:
            category_match = "MD"
        elif "WOMEN'S DOUBLES" in table_text or "WOMEN DOUBLES" in table_text or "LADIES DOUBLES" in table_text:
            category_match = "WD"
        elif "MIXED DOUBLES" in table_text:
            category_match = "XD"
        
        # Si ce n'est pas la catégorie qu'on cherche, continuer
        if category_match != category:
            continue
        
        print(f"✅ Table {table_idx + 1} contient {category}")
        
        # Parser toutes les lignes
        rows = table.find_all('tr')
        
        for row_idx, row in enumerate(rows):
            # Récupérer toutes les cellules
            cells = row.find_all(['td', 'th'])
            
            if len(cells) < 2:
                continue
            
            # Extraire le texte de chaque cellule
            cell_texts = []
            for cell in cells:
                text = cell.get_text(strip=True)
                if text:  # Ignorer les cellules vides
                    cell_texts.append(text)
            
            if len(cell_texts) < 2:
                continue
            
            # Afficher pour debug
            if row_idx < 10:
                print(f"   Ligne {row_idx}: {' | '.join(cell_texts[:5])}")
            
            # Chercher le rang et le nom
            rank = None
            name = None
            
            for i, text in enumerate(cell_texts):
                # Si c'est un nombre entre 1 et 500, c'est probablement un rang
                if text.isdigit() and 1 <= int(text) <= 500:
                    rank = int(text)
                    # Le nom est probablement la cellule suivante
                    if i + 1 < len(cell_texts):
                        next_text = cell_texts[i + 1]
                        # Vérifier que ce n'est pas un nombre
                        if not next_text.isdigit() and len(next_text) > 2:
                            name = next_text
                    break
            
            # Si on a trouvé un rang et un nom, ajouter
            if rank and name:
                rankings.append({
                    "rank": rank,
                    "name": name,
                    "category": category
                })
        
        # On a trouvé la bonne table, on s'arrête
        if rankings:
            break
    
    print(f"✅ {len(rankings)} joueurs trouvés pour {category}")
    
    return rankings


# Test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        print("🧪 TEST DU SCRAPER RÉEL\n")
        
        for cat in ["MS", "WS", "MD"]:
            print(f"\n{'='*60}")
            print(f"📊 CATÉGORIE: {cat}")
            print(f"{'='*60}")
            
            rankings = await fetch_real_rankings(cat)
            
            if rankings:
                print(f"\n✅ {len(rankings)} joueurs trouvés:")
                for r in rankings[:5]:
                    print(f"   {r['rank']}. {r['name']}")
            else:
                print("❌ Aucune donnée trouvée")
    
    asyncio.run(test())
