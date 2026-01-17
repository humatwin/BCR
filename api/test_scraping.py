"""
Test rapide du scraping pour vérifier que les données sont bien récupérées
"""

import asyncio
import httpx
from bs4 import BeautifulSoup

async def test_scraping():
    print("🧪 TEST DE SCRAPING BADMINTON CANADA")
    print("=" * 60)
    
    # URL du site
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?rid=22"
    
    print(f"🌐 URL: {url}")
    print("")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            print("📡 Envoi de la requête...")
            response = await client.get(url, headers=headers)
            
            print(f"✅ Réponse reçue: {response.status_code}")
            print(f"📦 Taille: {len(response.text)} caractères")
            print("")
            
            # Parser avec BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Chercher tous les titres
            print("📋 TITRES TROUVÉS (h1, h2, h3):")
            print("-" * 60)
            for i, header in enumerate(soup.find_all(['h1', 'h2', 'h3'])[:10]):
                text = header.get_text(strip=True)
                if text:
                    print(f"   {i+1}. {text}")
            print("")
            
            # Chercher toutes les tables
            tables = soup.find_all('table')
            print(f"📊 {len(tables)} TABLES TROUVÉES")
            print("-" * 60)
            
            for table_idx, table in enumerate(tables[:5]):  # Analyser les 5 premières
                print(f"\n🔍 TABLE {table_idx + 1}:")
                
                # Chercher le titre avant cette table
                prev = table.find_previous(['h1', 'h2', 'h3', 'h4', 'div'])
                if prev:
                    context = prev.get_text(strip=True)
                    if context:
                        print(f"   📝 Contexte avant: {context[:100]}")
                
                # Analyser les premières lignes
                rows = table.find_all('tr')[:5]
                print(f"   📋 {len(rows)} lignes (affichage des 5 premières)")
                
                for row_idx, row in enumerate(rows):
                    cells = row.find_all(['td', 'th'])
                    if cells:
                        cell_texts = [cell.get_text(strip=True) for cell in cells[:5]]
                        print(f"      Ligne {row_idx + 1}: {' | '.join(cell_texts)}")
                
                # Détection de catégorie
                table_text = str(table).upper()
                categories = []
                if "MEN" in table_text and "SINGLE" in table_text and "DOUBLE" not in table_text:
                    categories.append("MS (Men's Singles)")
                if "WOMEN" in table_text and "SINGLE" in table_text and "DOUBLE" not in table_text:
                    categories.append("WS (Women's Singles)")
                if "MEN" in table_text and "DOUBLE" in table_text and "MIXED" not in table_text:
                    categories.append("MD (Men's Doubles)")
                if "WOMEN" in table_text and "DOUBLE" in table_text and "MIXED" not in table_text:
                    categories.append("WD (Women's Doubles)")
                if "MIXED" in table_text:
                    categories.append("XD (Mixed Doubles)")
                
                if categories:
                    print(f"   ✅ Catégorie détectée: {', '.join(categories)}")
                else:
                    print(f"   ⚠️  Catégorie non détectée")
            
            print("")
            print("=" * 60)
            print("✅ TEST TERMINÉ")
            
    except Exception as e:
        print(f"❌ ERREUR: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_scraping())
