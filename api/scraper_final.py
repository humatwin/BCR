"""
Scraper FINAL - Récupère VRAIMENT toutes les catégories
"""

import httpx
from bs4 import BeautifulSoup
from typing import List, Dict

async def fetch_all_rankings() -> Dict[str, List[Dict]]:
    """
    Récupère toutes les catégories depuis https://badmintoncanada.tournamentsoftware.com
    """
    
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22"
    
    print(f"🌐 Récupération de {url}\n")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Chercher TOUS les <th> qui contiennent les catégories
    all_categories = {}
    
    category_mapping = {
        "MEN'S SINGLES": "MS",
        "WOMEN'S SINGLES": "WS",
        "MEN'S DOUBLES": "MD",
        "WOMEN'S DOUBLES": "WD",
        "MIXED DOUBLES": "XD"
    }
    
    for category_name, category_code in category_mapping.items():
        print(f"🔍 Recherche de: {category_name}")
        
        # Chercher l'en-tête avec ce nom
        for element in soup.find_all(['th', 'div', 'a']):
            if category_name in element.get_text().upper():
                print(f"   ✅ Trouvé dans <{element.name}>")
                
                # Chercher la table SUIVANTE (pas la parente)
                table = element.find_next('table')
                
                if table:
                    rankings = parse_ranking_table(table, category_code)
                    
                    if rankings and category_code not in all_categories:
                        all_categories[category_code] = rankings
                        print(f"   📊 {len(rankings)} joueurs extraits")
                        for r in rankings[:3]:
                            print(f"      {r['rank']}. {r['name']} ({r['points']} pts)")
                        break
    
    return all_categories

def parse_ranking_table(table, category_code: str) -> List[Dict]:
    """
    Parse une table de rankings
    Structure attendue:
    Ligne 0: Men's singles | More
    Ligne 1: Rank | Player | Member ID | Points | Tournaments
    Ligne 2: 1    | Victor Lai | ON13010 | 11180 | 3
    """
    
    rankings = []
    rows = table.find_all('tr')
    
    print(f"      📊 Table a {len(rows)} lignes")
    
    # Afficher les 3 premières lignes pour debug
    for i in range(min(3, len(rows))):
        cells = rows[i].find_all(['th', 'td'])
        cell_texts = [c.get_text(strip=True) for c in cells if c.get_text(strip=True)]
        print(f"         Ligne {i}: {cell_texts}")
    
    # Identifier les indices des colonnes
    rank_idx = None
    player_idx = None
    points_idx = None
    
    # Chercher la ligne d'en-têtes (celle avec "Rank", "Player", "Points")
    header_row_idx = None
    for i, row in enumerate(rows):
        headers = row.find_all(['th', 'td'])
        
        # IGNORER les cellules vides et garder les indices originaux
        header_map = {}  # {text: original_index}
        for j, h in enumerate(headers):
            text = h.get_text().strip().upper()
            if text:  # Seulement les non-vides
                header_map[text] = j
        
        if i <= 2:
            print(f"         DEBUG Ligne {i}: {list(header_map.keys())}")
        
        if 'RANK' in header_map:
            header_row_idx = i
            rank_idx = header_map.get('RANK', 0)
            
            # Chercher "PLAYER" ou "NAME"
            player_idx = None
            for key in header_map:
                if 'PLAYER' in key or 'NAME' in key:
                    player_idx = header_map[key]
                    break
            
            # Chercher "POINT"
            points_idx = None
            for key in header_map:
                if 'POINT' in key:
                    points_idx = header_map[key]
                    break
            
            print(f"         >>> En-têtes: rank={rank_idx}, player={player_idx}, points={points_idx}")
            break
    
    # Si pas trouvé, deviner (colonnes typiques: Rank | Player | MemberID | Points)
    if rank_idx is None: rank_idx = 0
    if player_idx is None: player_idx = 1
    if points_idx is None: points_idx = 3
    if header_row_idx is None: header_row_idx = 1  # Habituellement ligne 1
    
    print(f"      Colonnes: Rank={rank_idx}, Player={player_idx}, Points={points_idx}, Header={header_row_idx}")
    
    # Parser les lignes de données (commencer après la ligne d'en-têtes)
    start_idx = header_row_idx + 1 if header_row_idx is not None else 2
    for row in rows[start_idx:]:
        cells = row.find_all(['td', 'th'])
        
        if len(cells) <= max(rank_idx, player_idx):
            continue
        
        try:
            # Extraire les valeurs brutes
            rank_cell = cells[rank_idx].get_text(strip=True) if rank_idx < len(cells) else ""
            player_cell = cells[player_idx].get_text(strip=True) if player_idx < len(cells) else ""
            points_cell = cells[points_idx].get_text(strip=True) if points_idx < len(cells) else "0"
            
            # Debug première ligne
            if len(rankings) == 0:
                all_texts = [c.get_text(strip=True) for c in cells]
                print(f"         PREMIÈRE LIGNE ({len(cells)} cellules): {all_texts}")
                print(f"         Indices: rank={rank_idx}, player={player_idx}, points={points_idx}")
                print(f"         Valeurs: rank='{rank_cell}', player='{player_cell}', points='{points_cell}'")
            
            # Convertir rang
            rank = int(''.join(filter(str.isdigit, rank_cell)))
            
            # Extraire nom (ne devrait contenir que des lettres/espaces, pas de chiffres purs)
            player_name = player_cell.strip()
            
            # Extraire points (ne garder que chiffres et point)
            points = 0.0
            points_clean = points_cell.replace(',', '').strip()
            if points_clean:
                points = float(''.join(c for c in points_clean if c.isdigit() or c == '.'))
            
            # Vérifier validité
            if rank > 0 and rank < 1000 and len(player_name) > 2 and not player_name.upper() in ['RANK', 'PLAYER', 'NAME']:
                # S'assurer que le nom ne soit pas un nombre pur
                if not player_name.replace(' ', '').replace('.', '').isdigit():
                    rankings.append({
                        "rank": rank,
                        "name": player_name,
                        "points": points,
                        "category": category_code
                    })
        
        except (ValueError, IndexError) as e:
            continue
    
    return rankings

# Test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        print("🧪 TEST DU SCRAPER FINAL - VRAIES DONNÉES\n")
        print("="*60 + "\n")
        
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
                for r in rankings[:5]:
                    print(f"   {r['rank']}. {r['name']} ({r['points']} pts)")
            else:
                print(f"\n❌ {cat_names[cat]} ({cat}): AUCUNE DONNÉE")
    
    asyncio.run(test())
