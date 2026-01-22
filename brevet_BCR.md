# DOSSIER DE BREVET - BCR (Badminton Canada Ranking)

**Document confidentiel — Usage interne uniquement**  
**Date de création :** 20 janvier 2026  
**Version :** 1.0  

---

## 1. SYNTHÈSE DE L'INVENTION

### Titre Technique
**« Procédé de synchronisation, d'agrégation et de visualisation en temps réel de données sportives multi-sources pour le badminton avec système de gestion média collaborative »**

### Résumé (3 lignes)
**Problème :** Les joueurs de badminton au Canada n'ont aucun moyen centralisé d'accéder à leurs classements nationaux, provinciaux (ABC) et aux informations de tournois en temps réel sur mobile, obligeant une navigation manuelle sur plusieurs sites web non optimisés.

**Solution :** BCR propose une application mobile native qui agrège automatiquement les données de classement depuis TournamentSoftware et Badminton Québec via un backend API propriétaire, avec un système de cache intelligent, une gestion des photos collaboratives à trois niveaux d'accès (joueur, visiteur, média), et une synchronisation temps réel des tournois en cours.

---

## 2. LE PROBLÈME TECHNIQUE (ÉTAT DE L'ART)

### 2.1 Limites des Solutions Existantes

| Aspect | Limitation Actuelle |
|--------|---------------------|
| **Accessibilité** | Les classements sont uniquement disponibles sur le site web TournamentSoftware, non optimisé pour mobile |
| **Fragmentation** | Les données ABC (Badminton Québec) et nationales sont sur des plateformes séparées sans lien entre elles |
| **Temps réel** | Aucune notification ou affichage des tournois en cours |
| **Identité visuelle** | Les profils de joueurs sont anonymes, sans photos ni personnalisation |
| **Hors-ligne** | Aucune consultation possible sans connexion internet |
| **Médias** | Les photographes sportifs n'ont aucun moyen officiel de partager leurs photos avec les joueurs concernés |

### 2.2 Besoin Identifié (Le "Vide" Comblé)

1. **Centralisation** : Un point d'accès unique pour toutes les données de badminton canadien
2. **Mobilité** : Interface native iOS optimisée pour consultation rapide
3. **Communauté** : Système de partage de photos entre médias accrédités et joueurs
4. **Performance** : Réduction du temps de chargement via cache intelligent côté serveur
5. **Engagement** : Suivi des tournois en direct et tableau de score intégré

---

## 3. LA SOLUTION TECHNIQUE (LE "CŒUR")

### 3.1 Architecture Globale

```
┌─────────────────────────────────────────────────────────────────────┐
│                        APPLICATION iOS (SwiftUI)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐│
│  │ Rankings │  │ Tournois │  │ Actualité│  │ Profils  │  │Calendri││
│  │   View   │  │   View   │  │   View   │  │   View   │  │er View ││
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘│
│       │             │             │             │             │     │
│  ┌────▼─────────────▼─────────────▼─────────────▼─────────────▼────┐│
│  │                     DataManager (State)                          ││
│  │  • Rankings cache    • News items    • Saved tournaments        ││
│  │  • Favorite players  • My photos     • Live tournaments         ││
│  └────────────────────────────┬─────────────────────────────────────┘│
│                               │                                      │
│  ┌────────────────────────────▼─────────────────────────────────────┐│
│  │                     APIService (Network Layer)                    ││
│  │  • Health check      • Rankings fetch    • Media upload          ││
│  │  • Tournament search • Player search     • News fetch            ││
│  └────────────────────────────┬─────────────────────────────────────┘│
└───────────────────────────────┼──────────────────────────────────────┘
                                │ HTTPS
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        BACKEND API (FastAPI/Python)                  │
│  ┌──────────────────────────────────────────────────────────────────┐│
│  │                     Endpoints REST                                ││
│  │  /rankings/{category}        /tournaments/search                  ││
│  │  /rankings/{category}/abc    /tournaments/live                    ││
│  │  /player/{id}                /media/photos/{player_id}            ││
│  │  /news                       /abc/calendar                        ││
│  └────────────────────────────┬─────────────────────────────────────┘│
│                               │                                      │
│  ┌────────────────────────────▼─────────────────────────────────────┐│
│  │                     Cache Intelligent                             ││
│  │  • TTL 1 heure par défaut                                        ││
│  │  • Clés composites (catégorie + scope + date)                    ││
│  │  • Invalidation automatique                                       ││
│  └────────────────────────────┬─────────────────────────────────────┘│
│                               │                                      │
│  ┌────────────────────────────▼─────────────────────────────────────┐│
│  │                     Scraping Engine (BeautifulSoup + HTTPX)       ││
│  │  • Parser HTML TournamentSoftware                                ││
│  │  • Parser HTML Badminton Québec (ABC)                            ││
│  │  • Extraction intelligente des noms doubles                       ││
│  └──────────────────────────────────────────────────────────────────┘│
└───────────────────────────────┬──────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐    ┌───────────────────┐    ┌───────────────────┐
│TournamentSoft │    │  Badminton Québec │    │  Google Sheets    │
│   ware.com    │    │    (ABC Rankings) │    │   (News Custom)   │
└───────────────┘    └───────────────────┘    └───────────────────┘
```

### 3.2 Algorithmes Propriétaires

#### 3.2.1 Normalisation des Noms en Double (Patent-worthy)

**Problème :** Les classements doubles de TournamentSoftware retournent les noms concaténés sans séparateur (ex: "Daniel LeungTimothy Lock").

**Solution algorithmique :**

```
FONCTION normaliser_noms_doubles(nom_brut):
    SI nom_brut contient "/" ou " et " ou " - ":
        RETOURNER séparer_par_délimiteur(nom_brut)
    
    # Détection de jonction CamelCase (minuscule suivie de majuscule)
    POUR CHAQUE position i DANS nom_brut:
        SI caractère[i] est minuscule ET caractère[i+1] est majuscule:
            SI ce n'est pas un prénom composé connu (ex: "McDonald"):
                INSÉRER " / " à la position i+1
    
    RETOURNER nom_normalisé
```

#### 3.2.2 Système d'Authentification Tri-Mode

**Architecture à trois modes d'accès :**

```
ÉNUMÉRATION LoginMode:
    - standard   → Joueur authentifié (accès complet + profil BC)
    - visitor    → Invité (lecture seule)
    - media      → Photographe accrédité (upload photos sur tous profils)

FONCTION déterminer_mode(utilisateur):
    SI utilisateur.memberId == "MEDIA":
        RETOURNER .media
    SI utilisateur.memberId == "VISITOR":
        RETOURNER .visitor
    RETOURNER .standard
```

#### 3.2.3 Gestion des Photos Collaborative

**Flux de données photos :**

```
UPLOAD PHOTO:
    1. Vérifier mode d'authentification
    2. SI mode == media:
        - Exiger clé API média valide
        - Générer fingerprint SHA-256 de la clé
        - Taguer photo avec addedBy="media", addedById=fingerprint
    3. SI mode == standard ET userId == profilCible:
        - Générer signature HMAC du userId
        - Taguer photo avec addedBy="self", addedById=signature
    4. Uploader vers stockage (local ou S3/R2)
    5. Mettre à jour métadonnées JSON

SUPPRESSION PHOTO:
    1. Vérifier que addedById correspond à l'acteur actuel
    2. SI correspondance: supprimer
    3. SINON: refuser (403)
```

#### 3.2.4 Détection des Tournois en Cours (Live)

```
FONCTION get_tournois_live():
    date_aujourdhui = DATE_ACTUELLE()
    
    # Scraper les tournois de la saison
    tous_tournois = scraper_tournamentsoftware(saison_courante)
    
    tournois_live = []
    POUR CHAQUE tournoi DANS tous_tournois:
        SI tournoi.date_debut <= date_aujourdhui <= tournoi.date_fin:
            tournois_live.AJOUTER(tournoi)
    
    RETOURNER tournois_live (trié par date_fin)
```

### 3.3 Flux de Données Principal

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Utilisateur│     │    Cache    │     │   Scraper   │     │   Source    │
│   (iPhone)  │     │  (Backend)  │     │   Engine    │     │   Externe   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │                   │
       │  GET /rankings/MS │                   │                   │
       │──────────────────►│                   │                   │
       │                   │                   │                   │
       │                   │ Cache hit?        │                   │
       │                   │───────┐           │                   │
       │                   │       │ OUI       │                   │
       │◄──────────────────│◄──────┘           │                   │
       │   Données JSON    │                   │                   │
       │                   │                   │                   │
       │                   │ Cache miss?       │                   │
       │                   │────────────────►  │                   │
       │                   │                   │  HTTP GET         │
       │                   │                   │──────────────────►│
       │                   │                   │                   │
       │                   │                   │◄──────────────────│
       │                   │                   │   HTML brut       │
       │                   │                   │                   │
       │                   │◄──────────────────│                   │
       │                   │  Données parsées  │                   │
       │                   │                   │                   │
       │                   │ Mise en cache     │                   │
       │                   │ (TTL 1h)          │                   │
       │                   │                   │                   │
       │◄──────────────────│                   │                   │
       │   Données JSON    │                   │                   │
       │                   │                   │                   │
```

---

## 4. LES REVENDICATIONS (CE QUE VOUS PROTÉGEZ)

### 4.1 Revendication Principale (Indépendante)

**Revendication 1 :**  
Procédé informatique de synchronisation et d'agrégation de données de classement sportif pour le badminton, caractérisé par :
- (a) la collecte automatisée de données depuis au moins deux sources web distinctes (TournamentSoftware pour les classements nationaux, Badminton Québec pour les classements provinciaux ABC) ;
- (b) le parsing et la normalisation desdites données dans un format unifié ;
- (c) la mise en cache côté serveur avec une durée de vie configurable ;
- (d) la transmission desdites données normalisées vers une application mobile native via une API REST ;
- (e) l'affichage desdites données dans une interface utilisateur permettant la navigation entre les différentes catégories (MS, WS, MD, WD, XD) et niveaux (National, ABC A/B/C).

### 4.2 Revendications Secondaires (Dépendantes)

**Revendication 2 :** (dépend de 1)  
Procédé selon la revendication 1, caractérisé en ce que l'étape (b) comprend un algorithme de normalisation des noms de joueurs en double, détectant les jonctions de type CamelCase (minuscule suivie de majuscule) pour insérer automatiquement un séparateur entre deux noms concaténés.

**Revendication 3 :** (dépend de 1)  
Procédé selon la revendication 1, comprenant en outre un système d'authentification à trois modes (standard, visiteur, média), chaque mode déterminant les droits d'accès aux fonctionnalités de l'application, notamment l'upload de photos sur les profils de joueurs.

**Revendication 4 :** (dépend de 3)  
Procédé selon la revendication 3, dans lequel le mode « média » requiert une clé d'accès API dont l'empreinte cryptographique (SHA-256) est utilisée pour identifier de manière unique l'auteur des photos uploadées, permettant ainsi la suppression sélective par l'auteur original uniquement.

**Revendication 5 :** (dépend de 1)  
Procédé selon la revendication 1, comprenant en outre un module de détection des tournois en cours (« live »), comparant la date système aux plages de dates des tournois extraits, et affichant automatiquement lesdits tournois dans une section dédiée de l'interface.

**Revendication 6 :** (dépend de 1)  
Procédé selon la revendication 1, comprenant en outre un module de tableau de score interactif permettant le suivi manuel d'un match de badminton, avec comptage des points et des sets pour deux équipes/joueurs.

**Revendication 7 :** (dépend de 1)  
Système informatique mettant en œuvre le procédé selon l'une des revendications 1 à 6, comprenant :
- un serveur backend déployé sur une plateforme cloud (Render.com) ;
- une application mobile native iOS développée en SwiftUI ;
- un stockage de médias compatible S3/R2 pour les photos de joueurs.

**Revendication 8 :**  
Application mobile de consultation de classements sportifs de badminton, caractérisée par :
- une animation d'écran de chargement représentant un volant se déplaçant horizontalement ;
- un système de sélection de type de classement via menu déroulant (National vs ABC) ;
- l'affichage de deux avatars distincts pour les catégories de double avec navigation vers les profils individuels de chaque joueur.

---

## 5. PREUVES ET ILLUSTRATIONS

### 5.1 Logigramme - Flux d'Authentification

```
                    ┌─────────────────┐
                    │   Lancement App │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Données Keychain│
                    │   existantes ?  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │ NON          │              │ OUI
              ▼              │              ▼
     ┌─────────────────┐     │     ┌─────────────────┐
     │   LoginView     │     │     │ Restaurer user  │
     └────────┬────────┘     │     └────────┬────────┘
              │              │              │
    ┌─────────┼─────────┐    │              │
    │         │         │    │              │
    ▼         ▼         ▼    │              │
┌───────┐ ┌───────┐ ┌───────┐│              │
│Standard│ │Visiteur│ │ Média ││              │
└───┬───┘ └───┬───┘ └───┬───┘│              │
    │         │         │    │              │
    ▼         ▼         ▼    │              │
┌───────┐ ┌───────┐ ┌───────┐│              │
│Auth BC│ │Créer  │ │Clé API││              │
│Profile│ │Guest  │ │Requise││              │
└───┬───┘ └───┬───┘ └───┬───┘│              │
    │         │         │    │              │
    └─────────┴─────────┴────┴──────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  MainTabView    │
                    │ (5 onglets)     │
                    └─────────────────┘
```

### 5.2 Logigramme - Chargement des Rankings

```
┌─────────────────┐
│ RankingsView    │
│   onAppear      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ selectedScope   │
│ == .national ?  │
└────────┬────────┘
         │
    ┌────┴────┐
    │ OUI     │ NON
    ▼         ▼
┌───────┐ ┌───────────┐
│fetchR │ │fetchABC   │
│ankings│ │Rankings   │
└───┬───┘ └─────┬─────┘
    │           │
    └─────┬─────┘
          │
          ▼
┌─────────────────┐
│ APIService      │
│ .shared         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Cache backend   │
│ hit ?           │
└────────┬────────┘
         │
    ┌────┴────┐
    │ OUI     │ NON
    ▼         ▼
┌───────┐ ┌───────────┐
│Return │ │Scrape     │
│cached │ │source     │
└───┬───┘ └─────┬─────┘
    │           │
    │           ▼
    │    ┌───────────┐
    │    │Normaliser │
    │    │& Cacher   │
    │    └─────┬─────┘
    │          │
    └────┬─────┘
         │
         ▼
┌─────────────────┐
│ Decode JSON     │
│ → [Ranking]     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Update UI       │
│ (LazyVStack)    │
└─────────────────┘
```

### 5.3 Structure des Écrans (Wireframes Fonctionnels)

```
┌─────────────────────────────────────────┐
│ ◀ Logo BCR        RANKINGS ▼      🔍   │
│          National Rankings • 01/20/26   │
├─────────────────────────────────────────┤
│ [MS] [WS] [MD] [WD] [XD]                │
├─────────────────────────────────────────┤
│ SEED / PLAYER                    POINTS │
├─────────────────────────────────────────┤
│ ┌───────────────────────────────────┐   │
│ │ #1  ○ Victor Lai            11180 │   │
│ │     ON                            │   │
│ └───────────────────────────────────┘   │
│ ┌───────────────────────────────────┐   │
│ │ #2  ○ Brian Yang             9450 │   │
│ │     BC                            │   │
│ └───────────────────────────────────┘   │
│                  ...                    │
├─────────────────────────────────────────┤
│ [Actualité] [Rankings] [Tournois] [...] │
└─────────────────────────────────────────┘

         ↓ Mode Double (MD/WD/XD) ↓

┌─────────────────────────────────────────┐
│ ┌───────────────────────────────────┐   │
│ │ #1  ○ ○  Nyl Yakura        12500  │   │
│ │         Adam Dong                 │   │
│ │     ON                            │   │
│ └───────────────────────────────────┘   │
└─────────────────────────────────────────┘
    ↑ ↑
    │ └─ Tap → PlayerProfileView (Adam)
    └─── Tap → PlayerProfileView (Nyl)
```

### 5.4 Modèle de Données Principal

```
Ranking {
    id: String (UUID composite)
    rank: Int
    playerName: String
    points: Double
    province: String
    previousRank: Int?
    playerId: String
    category: RankingCategory
    scope: RankingScope
    lastUpdated: Date
    partnerName: String?      // Pour doubles
    partnerPlayerId: String?  // Pour doubles
}

User {
    id: String
    email: String
    firstName: String
    lastName: String
    memberId: String
    province: String?
    handedness: Handedness?
    playStyle: PlayStyle?
    officialPlayerId: String?
    profileImageURL: String?
}

UserPhoto {
    id: String
    userId: String
    fileName: String
    createdAt: Date
    addedBy: String ("self" | "media")
    addedById: String? (fingerprint)
    imageURL: String?
}

Tournament {
    id: String (GUID TournamentSoftware)
    name: String
    location: String?
    startDate: String?
    endDate: String?
    imageURL: String?
    tags: [String]
    tournamentURL: String?
    drawsURL: String?
}
```

---

## 6. ANNEXES TECHNIQUES

### 6.1 Stack Technologique

| Composant | Technologie | Version |
|-----------|-------------|---------|
| Frontend | SwiftUI (iOS) | iOS 16+ |
| Backend | FastAPI (Python) | 0.109 |
| Scraping | BeautifulSoup + HTTPX | 4.12 / 0.26 |
| Cache | In-memory (dict) | - |
| Stockage | Local / S3-compatible | - |
| Déploiement | Render.com | Starter |
| CI/CD | GitHub | - |

### 6.2 Endpoints API Principaux

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | /rankings/{category} | Classements nationaux par catégorie |
| GET | /rankings/{category}/abc/{tier} | Classements ABC (A/B/C) |
| GET | /player/{id} | Profil détaillé d'un joueur |
| GET | /player/search | Recherche de joueurs par nom |
| GET | /tournaments/search | Recherche de tournois |
| GET | /tournaments/live | Tournois en cours |
| GET | /news | Actualités (source: Google Sheet ou web) |
| POST | /media/photos/{player_id} | Upload photo (auth requise) |
| DELETE | /media/photos/{player_id}/{photo_id} | Suppression photo |

### 6.3 Sécurité et Authentification

- **Clé média** : Stockée localement (UserDefaults), hashée SHA-256 côté client pour identification
- **HMAC** : Signature des uploads "self" pour preuve d'origine
- **Rate limiting** : Protection anti-abus sur les endpoints d'upload/delete
- **HTTPS** : Communication chiffrée obligatoire en production

---

## 7. DÉCLARATION D'ANTÉRIORITÉ

À notre connaissance, à la date de dépôt de ce document, aucune solution existante ne propose l'ensemble des fonctionnalités décrites ci-dessus de manière intégrée pour le marché canadien du badminton.

Les sites TournamentSoftware et Badminton Québec proposent des données brutes mais :
- Sans application mobile native
- Sans agrégation multi-sources
- Sans système de photos collaboratives
- Sans détection automatique des tournois en cours

---

**Document préparé pour dépôt de brevet**  
**© 2026 BCR - Badminton Canada Ranking**  
**Tous droits réservés**
