# BCR API - Backend pour l'app Badminton Canada Rankings

## 🎯 Description

API backend qui scrape le site officiel de Badminton Canada (Tournament Software) pour récupérer les rankings en temps réel.

## 🚀 Installation

### 1. Créer un environnement virtuel Python

```bash
cd /Users/jeen.nico/Desktop/BCRapp/api
python3 -m venv venv
source venv/bin/activate
```

### 2. Installer les dépendances

```bash
pip install -r requirements.txt
```

### 3. Lancer le serveur

```bash
python main.py
```

Le serveur démarre sur **http://localhost:8000**

## 📖 Documentation

Une fois le serveur lancé, accédez à la documentation interactive :

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 🔗 Endpoints disponibles

### Obtenir les rankings

```
GET /rankings/{category}?scope=national
GET /rankings/{category}?scope=provincial&province=ON
```

**Catégories** : `MS`, `WS`, `MD`, `WD`, `XD`

**Exemples** :

```bash
# Simple Hommes National
curl http://localhost:8000/rankings/MS

# Simple Femmes National
curl http://localhost:8000/rankings/WS

# Double Hommes Provincial (Ontario)
curl http://localhost:8000/rankings/MD?scope=provincial&province=ON
```

### Raccourcis

```
GET /rankings/MS/national          # Simple Hommes National
GET /rankings/WS/national          # Simple Femmes National
GET /rankings/MD/provincial/ON     # Double Hommes Ontario
```

### Utilitaires

```
GET /health                        # Vérifier que l'API fonctionne
POST /cache/clear                  # Vider le cache (forcer le refresh)
```

## 💾 Cache

L'API met en cache les résultats pendant **1 heure** pour éviter de surcharger le site de Badminton Canada.

Pour forcer un rafraîchissement :
```bash
curl -X POST http://localhost:8000/cache/clear
```

## 📱 Intégration avec l'app iOS

L'app iOS doit maintenant faire des requêtes HTTP vers cette API au lieu de scraper directement.

Voir le fichier `APIService.swift` pour l'implémentation côté iOS.

## 🔧 Configuration

- **Port** : 8000 (en prod, utilisez la variable d'env `PORT` si votre hébergeur l'impose)
- **Timeout** : 30 secondes
- **Cache** : 1 heure

### Variables d'environnement (prod)

- `PORT`: port d'écoute (ex: `8000`)
- `BCR_MEDIA_ROOT`: dossier de stockage des médias (ex: `/data/media`) **doit être persistant en prod**
- `BCR_MEDIA_BACKEND`: `local` (défaut) ou `s3`
- `BCR_CORS_ORIGINS`: liste CSV d'origines autorisées (ex: `https://bcrapp.com,https://admin.bcrapp.com` ou `*`)
- `BCR_CORS_ALLOW_CREDENTIALS`: `true/false` (par défaut `false`)

#### Stockage S3/R2 (recommandé sur Render gratuit)

Quand `BCR_MEDIA_BACKEND=s3`, les uploads (photos/avatars) sont stockés dans un bucket S3-compatible:

- `BCR_S3_BUCKET`
- `BCR_S3_ACCESS_KEY_ID`
- `BCR_S3_SECRET_ACCESS_KEY`
- `BCR_S3_REGION` (optionnel, défaut `auto`)
- `BCR_S3_ENDPOINT_URL` (optionnel, requis pour R2)
- `BCR_S3_PUBLIC_BASE_URL` (recommandé): base URL publique pour servir les objets
  - Exemple: `https://media.bcrapp.com` → l’API renverra `https://media.bcrapp.com/photos/<player_id>/<file>.jpg`
- `BCR_S3_USE_ACL_PUBLIC_READ` (optionnel, défaut `false`): mettre `true` seulement si votre provider supporte les ACLs (AWS S3). **R2 n'aime pas ça**.

## 🐳 Déploiement (Docker)

Depuis le dossier `api/`:

```bash
docker build -t bcr-api .
docker run --rm -p 8000:8000 -e PORT=8000 -v $(pwd)/media:/data/media bcr-api
```

Ensuite:
- API: `http://localhost:8000/health`
- Docs: `http://localhost:8000/docs`

## 📝 Logs

Le serveur affiche des logs détaillés :

```
🌐 Scraping: https://... pour MS
📊 5 tables trouvées
✅ Table 2 correspond à MS
✅ 20 rankings extraits pour MS
💾 Données mises en cache pour national_MS_all
```

## ⚠️ Notes importantes

1. Le serveur doit tourner en permanence pour que l'app iOS fonctionne
2. Les données sont rafraîchies toutes les heures automatiquement
3. En cas d'erreur de scraping, l'API retourne une erreur 500 avec détails

## 🐛 Débogage

Si le scraping ne fonctionne pas :

1. Vérifiez que le site est accessible : https://badmintoncanada.tournamentsoftware.com
2. Regardez les logs du serveur pour voir les erreurs
3. Testez manuellement avec curl
4. Videz le cache : `curl -X POST http://localhost:8000/cache/clear`
