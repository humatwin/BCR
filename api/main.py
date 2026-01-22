"""
BCR API - Version qui FONCTIONNE avec les vraies données
Utilise les URLs directes pour chaque catégorie
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import urllib.parse
import os
import json
import csv
import io
import uuid
import time
import logging
import hmac
import hashlib
import unicodedata
from media_storage import build_media_storage

app = FastAPI(title="BCR API", version="3.0.0")

# --- Logging / Monitoring ---
LOG_LEVEL = (os.getenv("BCR_LOG_LEVEL") or "INFO").strip().upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bcr.api")

_sentry_dsn = (os.getenv("SENTRY_DSN") or "").strip()
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        def _parse_float_env(name: str, default: float) -> float:
            raw = (os.getenv(name) or "").strip()
            try:
                return float(raw) if raw else default
            except ValueError:
                return default

        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[FastApiIntegration()],
            traces_sample_rate=_parse_float_env("SENTRY_TRACES_SAMPLE_RATE", 0.0),
            environment=(os.getenv("BCR_ENV") or "production").strip(),
            release=(os.getenv("BCR_RELEASE") or None),
        )
        logger.info("sentry enabled")
    except Exception:
        logger.exception("failed to initialize sentry")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "request failed method=%s path=%s duration_ms=%s",
            request.method,
            request.url.path,
            duration_ms,
        )
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "request completed method=%s path=%s status=%s duration_ms=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

# --- Basic auth / abuse protection for write endpoints ---
MEDIA_API_KEY = (os.getenv("BCR_MEDIA_API_KEY") or "").strip()
SELF_HMAC_SECRET = (os.getenv("BCR_SELF_HMAC_SECRET") or "").strip()
RATE_LIMIT_WRITE_PER_MIN = int((os.getenv("BCR_RATE_LIMIT_WRITE_PER_MIN") or "30").strip() or "30")

_rate_limit_state: dict[tuple[str, str], list[float]] = {}


def _client_ip(request: Request) -> str:
    # Trust proxy headers only if you control the proxy; Render sets X-Forwarded-For.
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "unknown")


def _enforce_rate_limit(request: Request, bucket: str, limit: int = RATE_LIMIT_WRITE_PER_MIN, window_sec: int = 60) -> None:
    if limit <= 0:
        return
    ip = _client_ip(request)
    key = (bucket, ip)
    now = time.time()
    recent = [ts for ts in _rate_limit_state.get(key, []) if now - ts < window_sec]
    if len(recent) >= limit:
        raise HTTPException(status_code=429, detail="Trop de requêtes, réessayez plus tard.")
    recent.append(now)
    _rate_limit_state[key] = recent


def _require_media_key(request: Request) -> None:
    if not MEDIA_API_KEY:
        raise HTTPException(status_code=503, detail="Clé média non configurée.")
    key = (request.headers.get("x-api-key") or "").strip()
    if not key or key != MEDIA_API_KEY:
        raise HTTPException(status_code=401, detail="Clé média invalide.")


def _compute_self_signature(player_id: str, actor_id: str, action: str) -> str:
    msg = f"{player_id}:{actor_id}:{action}".encode("utf-8")
    return hmac.new(SELF_HMAC_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _require_self_signature(request: Request, player_id: str, actor_id: str, action: str) -> None:
    if not SELF_HMAC_SECRET:
        raise HTTPException(status_code=503, detail="Secret self non configuré.")
    provided_actor = (request.headers.get("x-self-id") or "").strip()
    provided_sig = (request.headers.get("x-self-signature") or "").strip()
    if not provided_actor or not provided_sig:
        raise HTTPException(status_code=401, detail="Signature manquante.")
    if provided_actor != actor_id:
        raise HTTPException(status_code=401, detail="Identité self invalide.")
    expected = _compute_self_signature(player_id=player_id, actor_id=actor_id, action=action)
    if not hmac.compare_digest(provided_sig, expected):
        raise HTTPException(status_code=401, detail="Signature invalide.")

# --- Production-friendly config (via env vars) ---
# Platforms like Render/Fly/Railway inject PORT; we use it in Docker CMD.
# CORS is mostly useful for web frontends; iOS is not affected by CORS.
def _parse_csv_env(name: str, default: str = "") -> List[str]:
    raw = (os.getenv(name) or default).strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]

_cors_origins = _parse_csv_env("BCR_CORS_ORIGINS", "*") or ["*"]
_cors_allow_credentials = (os.getenv("BCR_CORS_ALLOW_CREDENTIALS") or "false").strip().lower() == "true"
# Starlette disallows allow_credentials=True with wildcard origins; keep it safe by forcing false.
if _cors_origins == ["*"] and _cors_allow_credentials:
    _cors_allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Media storage (for "media" account uploads)
BASE_DIR = os.path.dirname(__file__)
MEDIA_ROOT = os.getenv("BCR_MEDIA_ROOT") or os.path.join(BASE_DIR, "media")
MEDIA_PHOTOS_DIR = os.path.join(MEDIA_ROOT, "photos")
MEDIA_AVATARS_DIR = os.path.join(MEDIA_ROOT, "avatars")

os.makedirs(MEDIA_PHOTOS_DIR, exist_ok=True)
os.makedirs(MEDIA_AVATARS_DIR, exist_ok=True)

MEDIA_STORAGE = build_media_storage(MEDIA_ROOT)
# Only mount static files when using local storage. On Render free, disk is ephemeral anyway.
if (os.getenv("BCR_MEDIA_BACKEND") or "local").strip().lower() != "s3":
    app.mount("/media-files", StaticFiles(directory=MEDIA_ROOT), name="media-files")

# Modèles
class RankingEntry(BaseModel):
    rank: int
    player_name: str
    points: float
    province: Optional[str] = None
    previous_rank: Optional[int] = None
    player_id: Optional[str] = None
    partner_name: Optional[str] = None
    partner_player_id: Optional[str] = None

class RankingResponse(BaseModel):
    category: str
    scope: str
    last_updated: str
    rankings: List[RankingEntry]
    total_count: int

class NewsItem(BaseModel):
    id: str
    title: str
    url: str
    image_url: Optional[str] = None
    excerpt: Optional[str] = None
    published: Optional[str] = None

class NewsResponse(BaseModel):
    source: str
    last_updated: str
    items: List[NewsItem]
    total_count: int

class MediaPhoto(BaseModel):
    id: str
    user_id: str
    file_name: str
    created_at: str
    added_by: str
    added_by_id: Optional[str] = None
    image_url: Optional[str] = None

# Cache simple
cache = {}
CACHE_DURATION = timedelta(hours=1)
ABC_URL = "https://www.badmintonquebec.com/classement-elite-abc-2025-2026"
BADMINTON_CANADA_HOME = "https://www.badminton.ca"
BADMINTON_CANADA_NEWS_FEED = "https://www.badminton.ca/newsfeed/0/"
NEWS_SHEET_CSV_URL = (os.getenv("BCR_NEWS_SHEET_CSV_URL") or "").strip()

# Simple FR/EN scoring to keep only French news in /news
_FR_TOKENS = {
    "demande", "proposition", "dp", "défi", "defi", "conclusion",
    "remporte", "étoile", "etoile", "montante", "année", "annee",
    "intronisée", "intronisee", "temple", "renommée", "renommee",
    "résultats", "resultats", "championnats", "panaméricains", "panamericains",
    "para-badminton", "para"
}
_EN_TOKENS = {
    "request", "proposal", "rfp", "wins", "wrap", "results", "canadians",
    "championships", "inducted", "into", "hall", "fame"
}

def _media_player_dir(player_id: str) -> str:
    pid = str(player_id).strip()
    return os.path.join(MEDIA_PHOTOS_DIR, pid)

def _media_photos_meta_path(player_id: str) -> str:
    return os.path.join(_media_player_dir(player_id), "photos.json")

def _load_media_photos(player_id: str) -> List[dict]:
    meta_path = _media_photos_meta_path(player_id)
    if not os.path.exists(meta_path):
        return []
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []

def _save_media_photos(player_id: str, items: List[dict]) -> None:
    pdir = _media_player_dir(player_id)
    os.makedirs(pdir, exist_ok=True)
    meta_path = _media_photos_meta_path(player_id)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)

def _lang_score(text: str) -> tuple[int, int, bool]:
    """Return (fr_score, en_score, has_accents)."""
    if not text:
        return (0, 0, False)
    t = text.lower().strip()
    has_accents = bool(re.search(r"[àâäçéèêëîïôöùûüÿœæ]", t))
    tokens = re.findall(r"[a-zà-ÿ]+(?:'[a-zà-ÿ]+)?", t, flags=re.IGNORECASE)
    token_set = set(tokens)

    fr = sum(1 for w in _FR_TOKENS if w.lower().replace("é", "e") in token_set or w in token_set)
    en = sum(1 for w in _EN_TOKENS if w in token_set)

    # Boost for strong multi-word English phrase
    if "hall of fame" in t:
        en += 3
    if "has been" in t:
        en += 1

    return (fr, en, has_accents)

def _is_likely_french(text: str) -> bool:
    fr, en, accents = _lang_score(text)
    return accents or (fr > en)


def _normalize_doubles_player_name(raw: str) -> str:
    """
    Try to split concatenated doubles names like "Daniel LeungTimothy Lock"
    into "Daniel Leung / Timothy Lock".
    """
    name = (raw or "").strip()
    if not name:
        return name
    if "/" in name:
        return name

    # Insert spaces between lowercase->uppercase transitions (supports accents).
    name = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    parts = name.split(" ")
    if len(parts) < 2:
        return name

    mid = max(1, len(parts) // 2)
    left = " ".join(parts[:mid]).strip()
    right = " ".join(parts[mid:]).strip()
    if left and right:
        return f"{left} / {right}"
    return name

# URLs directes pour chaque catégorie
CATEGORY_URLS = {
    "MS": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=151",
    "WS": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=152",
    "MD": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=153",
    "WD": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=154",
    "XD": "https://badmintoncanada.tournamentsoftware.com/ranking/category.aspx?id=49797&category=155"
}

RANKING_LIST_ID = "49797"

async def scrape_rankings(category: str) -> List[RankingEntry]:
    """Scrape les rankings pour une catégorie"""
    
    url = CATEGORY_URLS.get(category, CATEGORY_URLS["MS"])
    
    print(f"🌐 Scraping: {url}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table')
        
        if not table:
            print("❌ Aucune table trouvée")
            return []
        
        rows = table.find_all('tr')
        print(f"📊 {len(rows)} lignes dans la table")
        
        rankings = []
        
        # Skipper les 2 premières lignes (titre + en-têtes)
        name_to_id_cache: dict[str, str] = {}

        async def _resolve_player_id_by_name(name: Optional[str]) -> Optional[str]:
            q = (name or "").strip()
            if len(q) < 2:
                return None
            if q in name_to_id_cache:
                return name_to_id_cache[q]
            try:
                matches = await search_players(q, limit=1)
                if matches:
                    pid = matches[0].player_id
                    if pid:
                        name_to_id_cache[q] = pid
                        return pid
            except Exception:
                return None
            return None

        for row in rows[2:]:
            cells = row.find_all(['td', 'th'])
            
            if len(cells) < 3:
                continue
            
            cell_texts = [c.get_text(strip=True) for c in cells]

            # Extract player ids + names from anchors (for doubles)
            player_id = None
            partner_player_id = None
            partner_name = None
            anchor_infos = []
            try:
                for a in row.find_all("a", href=True):
                    href = a.get("href", "")
                    m = re.search(r"[?&]player=(\d+)", href)
                    if not m:
                        continue
                    pid = m.group(1)
                    name_txt = a.get_text(" ", strip=True)
                    if not name_txt:
                        continue
                    anchor_infos.append((pid, name_txt))
            except Exception:
                anchor_infos = []
            
            # Trouver rang (premier nombre < 1000)
            rank = None
            for text in cell_texts:
                if text.isdigit() and 1 <= int(text) < 1000:
                    rank = int(text)
                    break
            
            # Trouver nom (meilleur effort). Pour doubles, extraire les 2 joueurs si possible.
            player_name = None
            if category in ["MD", "WD", "XD"]:
                # Use anchor infos when possible to get partner ids
                if anchor_infos:
                    seen = set()
                    uniq = []
                    for pid, n in anchor_infos:
                        key = (pid, n)
                        if key in seen:
                            continue
                        seen.add(key)
                        uniq.append((pid, n))
                    if len(uniq) >= 2:
                        player_id = uniq[0][0]
                        partner_player_id = uniq[1][0]
                        player_name = " / ".join([uniq[0][1], uniq[1][1]])
                        partner_name = uniq[1][1]
                    elif len(uniq) == 1:
                        player_id = uniq[0][0]

            # Fallback: première chaîne qui ressemble à un nom
            if not player_name:
                for text in cell_texts:
                    if (len(text) > 2 and 
                        not text.replace('.', '').replace(',', '').isdigit() and 
                        not re.match(r'^[A-Z]{2}\d+$', text)):  # Pas un ID comme "ON13010"
                        
                        # Vérifier que c'est un nom (contient espace OU caractères alphabétiques > 50%)
                        alpha_count = sum(c.isalpha() or c.isspace() for c in text)
                        if alpha_count > len(text) * 0.5:
                            player_name = text
                            break

            if player_name and category in ["MD", "WD", "XD"]:
                player_name = _normalize_doubles_player_name(player_name)
                if "/" in player_name and not partner_name:
                    parts = [p.strip() for p in player_name.split("/") if p.strip()]
                    if len(parts) >= 2:
                        partner_name = parts[1]
            
            # Trouver points (nombre >= 1000)
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
                # Best-effort partner id resolution if missing (doubles)
                if category in ["MD", "WD", "XD"]:
                    if not player_id:
                        # Try resolve from first name part
                        name_part = player_name.split("/")[0].strip() if "/" in player_name else player_name
                        player_id = await _resolve_player_id_by_name(name_part)
                    if partner_name and not partner_player_id:
                        partner_player_id = await _resolve_player_id_by_name(partner_name)
                rankings.append(RankingEntry(
                    rank=rank,
                    player_name=player_name,
                    points=points,
                    province="ON",  # TODO: extraire de la table
                    previous_rank=None,
                    player_id=player_id,
                    partner_name=partner_name,
                    partner_player_id=partner_player_id
                ))
        
        print(f"✅ {len(rankings)} joueurs extraits")
        if rankings:
            print(f"   1er: {rankings[0].player_name}")
        
        return rankings
    
    except Exception as e:
        print(f"❌ Erreur de scraping: {e}")
        return []


class PlayerRankingItem(BaseModel):
    category_code: str
    category_name: str
    rank: int
    points: float
    partner_name: Optional[str] = None
    partner_player_id: Optional[str] = None

class PlayerProfileResponse(BaseModel):
    player_id: str
    full_name: str
    member_id: Optional[str] = None
    province: Optional[str] = None
    profile_url: str
    rankings: List[PlayerRankingItem]
    last_updated: str


class PlayerSearchResult(BaseModel):
    player_id: str
    full_name: str
    member_id: Optional[str] = None
    province: Optional[str] = None

class ABCCalendarEvent(BaseModel):
    id: str
    title: str
    subtitle: Optional[str] = None
    start_ts: int
    end_ts: int
    start: str
    end: str
    url: Optional[str] = None
    image_url: Optional[str] = None

class ABCCalendarResponse(BaseModel):
    source: str
    last_updated: str
    events: List[ABCCalendarEvent]
    total_count: int

class TournamentSearchItem(BaseModel):
    tournament_id: str
    name: str
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = []
    tournament_url: Optional[str] = None
    draws_url: Optional[str] = None

class TournamentSearchResponse(BaseModel):
    query: str
    source: str
    last_updated: str
    items: List[TournamentSearchItem]
    total_count: int

class TournamentDrawItem(BaseModel):
    name: str
    size: Optional[str] = None
    type: Optional[str] = None
    stage: Optional[str] = None
    consolation: Optional[str] = None
    url: str

class TournamentDrawsResponse(BaseModel):
    tournament_id: str
    source: str
    last_updated: str
    draws: List[TournamentDrawItem]
    total_count: int


class EloRankingEntry(BaseModel):
    rank: int
    player_id: str
    player_name: str
    rating: float
    avg_points_per_match: float
    matches: int
    tournaments: int
    active: bool


class EloRankingResponse(BaseModel):
    category: str
    scope: str
    last_updated: str
    items: List[EloRankingEntry]
    total_count: int
    debug: Optional[dict] = None


class PredictionMatchup(BaseModel):
    player_a_id: Optional[str] = None
    player_a_name: str
    player_b_id: Optional[str] = None
    player_b_name: str
    expected_a: float
    expected_b: float
    delta_a_win: float
    delta_a_loss: float
    delta_b_win: float
    delta_b_loss: float
    k: float
    source: str
    tournament_name: Optional[str] = None


class PredictionResponse(BaseModel):
    tournament_id: str
    category: str
    last_updated: str
    matchups: List[PredictionMatchup]
    total_count: int
    debug: Optional[dict] = None


def _map_category_code_from_text(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if "men" in t and "single" in t:
        return "MS"
    if ("women" in t or "ladies" in t) and "single" in t:
        return "WS"
    if "men" in t and "double" in t:
        return "MD"
    if ("women" in t or "ladies" in t) and "double" in t:
        return "WD"
    if "mixed" in t:
        return "XD"
    return None


async def scrape_player_profile(player_id: str) -> PlayerProfileResponse:
    player_id = str(player_id).strip()
    if not re.match(r"^\d+$", player_id):
        raise ValueError("player_id invalide")

    url = f"https://badmintoncanada.tournamentsoftware.com/ranking/player.aspx?id={RANKING_LIST_ID}&player={player_id}"
    print(f"🌐 Scraping Player: {url}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(url, headers=headers, follow_redirects=True)
        response.raise_for_status()

    html = response.text
    # Example: "Ranking of Victor Lai (ON13010)"
    full_name = f"Player {player_id}"
    member_id = None
    province = None
    m = re.search(r"Ranking of\s+(.+?)\s*\(([^)]+)\)", html, flags=re.IGNORECASE)
    if m:
        full_name = m.group(1).strip()
        member_id = m.group(2).strip()
        if member_id and re.match(r"^[A-Z]{2}\d+", member_id):
            province = member_id[:2]

    soup = BeautifulSoup(html, "html.parser")

    # Find ranking table: has "Category" and "Points"
    target_table = None
    for table in soup.find_all("table"):
        txt = table.get_text(" ", strip=True).lower()
        if "category" in txt and "points" in txt:
            target_table = table
            break

    rankings: List[PlayerRankingItem] = []
    if target_table:
        for tr in target_table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            # Category cell
            cat_link = tds[0].find("a")
            category_name = tds[0].get_text(" ", strip=True)
            if cat_link:
                category_name = cat_link.get_text(" ", strip=True) or category_name
            category_code = _map_category_code_from_text(category_name) or ""
            if not category_code:
                continue

            # Partner cell (if any)
            partner_name = None
            partner_player_id = None
            partner_link = tds[1].find("a", href=True)
            if partner_link:
                partner_name = partner_link.get_text(" ", strip=True)
                href = partner_link.get("href", "")
                pm = re.search(r"[?&]player=(\d+)", href)
                if pm:
                    partner_player_id = pm.group(1)

            # Rank cell: first td with class 'rank' inside row
            rank_val = None
            for td in tr.find_all("td", class_=re.compile(r"\brank\b")):
                candidate = td.get_text(" ", strip=True)
                rm = re.search(r"(\d+)", candidate)
                if rm:
                    rank_val = int(rm.group(1))
                    break
            if rank_val is None:
                continue

            # Points cell
            points_val = 0.0
            td_points = tr.find("td", class_=re.compile(r"rankingpoints"))
            if td_points:
                raw = td_points.get_text(strip=True).replace(",", "")
                try:
                    points_val = float(raw) if raw else 0.0
                except ValueError:
                    points_val = 0.0

            rankings.append(PlayerRankingItem(
                category_code=category_code,
                category_name=category_name,
                rank=rank_val,
                points=points_val,
                partner_name=partner_name,
                partner_player_id=partner_player_id
            ))

    return PlayerProfileResponse(
        player_id=player_id,
        full_name=full_name,
        member_id=member_id,
        province=province,
        profile_url=url,
        rankings=rankings,
        last_updated=datetime.now().isoformat()
    )


async def search_players(query: str, limit: int = 20) -> List[PlayerSearchResult]:
    q = (query or "").strip()
    if len(q) < 2:
        return []

    # This endpoint requires a session cookie. We must GET the page first.
    find_url = f"https://badmintoncanada.tournamentsoftware.com/ranking/find.aspx?id={RANKING_LIST_ID}"
    webmethod_url = "https://badmintoncanada.tournamentsoftware.com/ranking/find.aspx/GetRankingPlayer"

    payload = {
        "LCID": 4105,
        "RankingID": int(RANKING_LIST_ID),
        # TS expects JS encodeURIComponent output
        "Value": urllib.parse.quote(q, safe="")
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        # seed cookies
        await client.get(find_url, headers=headers)
        r = await client.post(
            webmethod_url,
            headers={
                **headers,
                "Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": find_url
            },
            json=payload
        )
        r.raise_for_status()
        data = r.json()

    items = data.get("d") or []
    out: List[PlayerSearchResult] = []
    for it in items[:limit]:
        pid = str(it.get("ID") or "").strip()
        name = str(it.get("Value") or "").strip()
        member = it.get("ExtraInfo")
        member_id = str(member).strip() if member else None
        province = None
        if member_id and re.match(r"^[A-Z]{2}\d+", member_id):
            province = member_id[:2]
        if pid and name:
            out.append(PlayerSearchResult(
                player_id=pid,
                full_name=name,
                member_id=member_id,
                province=province
            ))
    return out


ABC_CALENDAR_URL = "https://www.badmintonquebec.com/circuit-elite-abc-yonex-2-2449"
TS_BASE = "https://badmintoncanada.tournamentsoftware.com"

def _current_season_range() -> tuple[str, str]:
    """
    Returns (season_start, season_end) as YYYY-MM-DD strings for the current season.
    We treat the badminton season as July 1 -> June 30 (ex: 2025-07-01 to 2026-06-30).
    """
    now = datetime.now()
    start_year = now.year if now.month >= 7 else (now.year - 1)
    end_year = start_year + 1
    return (f"{start_year}-07-01", f"{end_year}-06-30")

def _overlaps_season(start_date: Optional[str], end_date: Optional[str], season_start: str, season_end: str) -> bool:
    """
    Dates are expected as YYYY-MM-DD. Uses lexicographic compare which is valid for this format.
    """
    if not start_date:
        return False
    sd = start_date[:10]
    ed = (end_date or start_date)[:10]
    return (sd <= season_end) and (ed >= season_start)

async def scrape_abc_calendar(limit: int = 200) -> List[ABCCalendarEvent]:
    print(f"🌐 Scraping ABC Calendar: {ABC_CALENDAR_URL}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = await client.get(ABC_CALENDAR_URL, headers=headers, follow_redirects=True)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    events: List[ABCCalendarEvent] = []
    for div in soup.select("div.eventon_list_event"):
        eid = div.get("data-event_id") or div.get("id") or ""
        if not eid:
            continue

        data_time = div.get("data-time") or ""
        m = re.match(r"^(\d+)-(\d+)$", data_time)
        if not m:
            continue
        start_ts = int(m.group(1))
        end_ts = int(m.group(2))

        title_el = div.select_one(".evoet_title")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            continue

        subtitle_el = div.select_one(".evcal_event_subtitle")
        subtitle = subtitle_el.get_text(" ", strip=True) if subtitle_el else None

        # Event URL: schema link (most reliable)
        url = None
        url_el = div.select_one(".evo_event_schema a[itemprop='url']")
        if url_el and url_el.get("href"):
            url = url_el.get("href")

        # Image URL
        image_url = None
        ft = div.select_one(".ev_ftImg")
        if ft:
            image_url = ft.get("data-img") or ft.get("data-thumb")
            if not image_url:
                style = ft.get("style") or ""
                sm = re.search(r'url\\(\"([^\"]+)\"\\)', style)
                if sm:
                    image_url = sm.group(1)

        start_iso = datetime.fromtimestamp(start_ts).isoformat()
        end_iso = datetime.fromtimestamp(end_ts).isoformat()

        events.append(ABCCalendarEvent(
            id=str(eid),
            title=title,
            subtitle=subtitle,
            start_ts=start_ts,
            end_ts=end_ts,
            start=start_iso,
            end=end_iso,
            url=url,
            image_url=image_url
        ))

        if len(events) >= limit:
            break

    # Dedup by (url or id)
    seen = set()
    out: List[ABCCalendarEvent] = []
    for e in events:
        k = e.url or e.id
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out

async def search_tournaments_ts(query: str, page: int = 1, limit: int = 25) -> List[TournamentSearchItem]:
    q = (query or "").strip()
    if len(q) < 1:
        return []

    url = f"{TS_BASE}/find/tournament/DoSearch"
    print(f"🌐 TS tournament search: {url} q={q}")
    season_start, season_end = _current_season_range()

    # If the user enters a single letter, TS returns lots of historical results first.
    # We scan multiple pages until we collect enough tournaments in the current season.
    max_pages = 10 if len(q) == 1 else 1
    prefix_mode = (len(q) == 1)
    letter = q.upper() if prefix_mode else None
    # For 1-letter search, fetch the season list and filter locally by prefix.
    ts_query = "" if prefix_mode else q

    items: List[TournamentSearchItem] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "Mozilla/5.0"}

        for p in range(1, max_pages + 1):
            data = {
                "Page": str(p),
                "TournamentExtendedFilter.SportID": "2",  # badminton
                "TournamentFilter.Q": ts_query,
                "TournamentFilter.StartDate": f"{season_start}T00:00",
                "TournamentFilter.EndDate": f"{season_end}T00:00",
            }
            resp = await client.post(url, headers=headers, data=data)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            lis = soup.select("li.list__item")
            if not lis:
                break

            for li in lis:
                a = li.select_one("a.media__link[href*='/sport/tournament?id=']")
                if not a or not a.get("href"):
                    continue

                href = a.get("href")
                m = re.search(r"[?&]id=([0-9A-Fa-f\\-]{36})", href)
                if not m:
                    continue
                tid = m.group(1).upper()
                if tid in seen_ids:
                    continue

                name = a.get_text(" ", strip=True)
                if not name:
                    continue

                # Single-letter mode: only keep tournaments starting with that letter
                if prefix_mode and letter and not name.strip().upper().startswith(letter):
                    continue

                # location text
                location = None
                loc = li.select_one(".media__subheading .icon-marker")
                if loc:
                    location = loc.find_parent(class_=re.compile(r"media__subheading"))\
                        .get_text(" ", strip=True).replace("  ", " ")
                    location = re.sub(r"^\\s*\\S+\\s*", "", location) if location else location

                # dates
                times = li.find_all("time")
                start_date = times[0].get("datetime") if len(times) >= 1 else None
                end_date = times[1].get("datetime") if len(times) >= 2 else None
                if start_date:
                    start_date = start_date.split(" ")[0]
                if end_date:
                    end_date = end_date.split(" ")[0]

                img = li.select_one("img.media__img-element")
                image_url = None
                if img and img.get("src"):
                    src = img.get("src")
                    image_url = src if src.startswith("http") else f"https:{src}"

                tags = [t.get_text(" ", strip=True) for t in li.select(".tag") if t.get_text(strip=True)]

                tournament_url = f"{TS_BASE}{href}"
                draws_url = f"{TS_BASE}/sport/draws.aspx?id={tid}"

                item = TournamentSearchItem(
                    tournament_id=tid,
                    name=name,
                    location=location,
                    start_date=start_date,
                    end_date=end_date,
                    image_url=image_url,
                    tags=tags,
                    tournament_url=tournament_url,
                    draws_url=draws_url,
                )

                # Safety check: keep only tournaments overlapping season
                if not _overlaps_season(item.start_date, item.end_date, season_start, season_end):
                    continue

                seen_ids.add(tid)
                items.append(item)

                if len(items) >= limit:
                    return items

    return items


def _is_live_tournament(start_date: Optional[str], end_date: Optional[str], today: str) -> bool:
    if not start_date:
        return False
    sd = start_date.split(" ")[0] if start_date else ""
    ed = end_date.split(" ")[0] if end_date else sd
    if not sd:
        return False
    return sd <= today <= (ed or sd)


async def fetch_live_tournaments_ts(limit: int = 30) -> List[TournamentSearchItem]:
    today = datetime.now().date().isoformat()
    items: List[TournamentSearchItem] = []
    seen_ids: set[str] = set()

    # Scan letters until we collect enough live tournaments.
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        found = await search_tournaments_ts(query=letter, page=1, limit=25)
        for it in found:
            if it.tournament_id in seen_ids:
                continue
            if _is_live_tournament(it.start_date, it.end_date, today):
                seen_ids.add(it.tournament_id)
                items.append(it)
                if len(items) >= limit:
                    return items
    return items

async def scrape_tournament_draws_ts(tournament_id: str) -> List[TournamentDrawItem]:
    tid = (tournament_id or "").strip()
    if not re.match(r"^[0-9A-Fa-f\\-]{36}$", tid):
        raise ValueError("tournament_id invalide")
    tid = tid.upper()

    url = f"{TS_BASE}/sport/draws.aspx?id={tid}"
    print(f"🌐 TS draws: {url}")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    out: List[TournamentDrawItem] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 1:
            continue
        # First cell contains draw link
        a = tds[0].find("a", href=True)
        if not a:
            continue
        name = a.get_text(" ", strip=True)
        href = a.get("href")
        full_url = href if href.startswith("http") else f"{TS_BASE}/sport/{href.lstrip('/')}"

        size = tds[1].get_text(" ", strip=True) if len(tds) > 1 else None
        dtype = tds[2].get_text(" ", strip=True) if len(tds) > 2 else None
        stage = tds[3].get_text(" ", strip=True) if len(tds) > 3 else None
        consolation = tds[4].get_text(" ", strip=True) if len(tds) > 4 else None

        out.append(TournamentDrawItem(
            name=name,
            size=size,
            type=dtype,
            stage=stage,
            consolation=consolation,
            url=full_url
        ))

    return out

async def scrape_abc_rankings(tier: str, category: str, limit: int = 20) -> List[RankingEntry]:
    """
    Scrape Classement Élite ABC (Badminton Québec) and return top N for:
      - tier: A/B/C
      - category: MS/WS/MD/WD/XD (mapped to CoteS/CoteD/CoteDX and gender filter)
    """
    tier = tier.upper().strip()
    category = category.upper().strip()

    if tier not in ["A", "B", "C"]:
        raise ValueError("Tier invalide (A, B, C)")
    if category not in ["MS", "WS", "MD", "WD", "XD"]:
        raise ValueError("Catégorie invalide (MS, WS, MD, WD, XD)")

    # Map category to column + gender filter (table stores individuals with 'Classe' like 'A mas' / 'B fem')
    if category in ["MS", "WS"]:
        cote_key = "COTES"
    elif category in ["MD", "WD"]:
        cote_key = "COTED"
    else:
        cote_key = "COTEDX"

    gender_needed = None
    if category in ["MS", "MD"]:
        gender_needed = "MAS"
    elif category in ["WS", "WD"]:
        gender_needed = "FEM"
    # XD: include both genders (individual mixed rating)

    print(f"🌐 Scraping ABC: {ABC_URL} | Tier={tier} | Category={category} | limit={limit}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(ABC_URL, headers=headers, follow_redirects=True)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Find the first large table (the ABC page contains a big sortable table)
    table = soup.find("table")
    if not table:
        print("❌ ABC: aucune table trouvée")
        return []

    rows = table.find_all("tr")
    if not rows:
        print("❌ ABC: table vide")
        return []

    # Header detection
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True).upper() for c in header_cells]

    def find_col(name: str) -> int:
        try:
            return headers.index(name)
        except ValueError:
            return -1

    idx_no = find_col("NO")
    idx_nom = find_col("NOM")
    idx_classe = find_col("CLASSE")
    idx_club = find_col("CLUB")
    idx_cotes = find_col("COTES")
    idx_coted = find_col("COTED")
    idx_cotedx = find_col("COTEDX")

    # Fallback: try partial matches if needed
    if idx_no == -1:
        for i, h in enumerate(headers):
            if h.startswith("NO"):
                idx_no = i
                break
    if idx_nom == -1:
        for i, h in enumerate(headers):
            if "NOM" in h:
                idx_nom = i
                break
    if idx_classe == -1:
        for i, h in enumerate(headers):
            if "CLASSE" in h:
                idx_classe = i
                break

    cote_idx_map = {"COTES": idx_cotes, "COTED": idx_coted, "COTEDX": idx_cotedx}
    idx_cote = cote_idx_map.get(cote_key, -1)

    if idx_no == -1 or idx_nom == -1 or idx_classe == -1 or idx_cote == -1:
        print(f"❌ ABC: colonnes manquantes. headers={headers}")
        return []

    candidates = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(idx_no, idx_nom, idx_classe, idx_cote):
            continue

        no = cells[idx_no].get_text(strip=True)
        nom = cells[idx_nom].get_text(strip=True)
        classe = cells[idx_classe].get_text(strip=True).upper()
        club = cells[idx_club].get_text(strip=True) if idx_club != -1 and len(cells) > idx_club else None
        cote_raw = cells[idx_cote].get_text(strip=True)

        # Filter tier + gender
        if not classe.startswith(tier):
            continue
        if gender_needed and gender_needed not in classe:
            continue

        # Parse cote
        # can be '', '0', '-1', '3,200' etc
        cote_clean = cote_raw.replace(",", "").strip()
        try:
            cote_val = float(cote_clean) if cote_clean else 0.0
        except ValueError:
            cote_val = 0.0

        if cote_val <= 0:
            continue
        if not nom:
            continue

        province = no[:2] if len(no) >= 2 and no[:2].isalpha() else None

        candidates.append(
            {
                "player_id": no or None,
                "player_name": nom,
                "points": cote_val,
                "province": province,
                "club": club,
            }
        )

    # Sort by points desc
    candidates.sort(key=lambda x: x["points"], reverse=True)

    is_doubles_category = category in ["MD", "WD", "XD"]
    out: List[RankingEntry] = []

    if is_doubles_category:
        # Group by identical points (partners share same points)
        grouped = {}
        for entry in candidates:
            grouped.setdefault(entry["points"], []).append(entry)

        # Sort point groups desc and take top N groups as "positions"
        point_values = sorted(grouped.keys(), reverse=True)
        point_values = point_values[:limit]

        for rank_idx, pts in enumerate(point_values, start=1):
            group = grouped[pts]
            # Deterministic order inside group
            group.sort(key=lambda x: (x.get("player_name") or ""))

            # Usually 2 partners. If more exist, keep first 2.
            names = [g["player_name"] for g in group if g.get("player_name")]
            display_name = "/".join(names[:2]) if names else ""

            player_ids = [g["player_id"] for g in group if g.get("player_id")]
            display_id = "/".join(player_ids[:2]) if player_ids else None

            provinces = [g["province"] for g in group if g.get("province")]
            display_prov = provinces[0] if provinces else None

            # Extract partner info when possible
            partner_name = None
            partner_player_id = None
            if display_name and "/" in display_name:
                parts = [p.strip() for p in display_name.split("/") if p.strip()]
                if len(parts) >= 2:
                    partner_name = parts[1]
            if display_id and "/" in display_id:
                parts = [p.strip() for p in display_id.split("/") if p.strip()]
                if len(parts) >= 2:
                    partner_player_id = parts[1]

            out.append(
                RankingEntry(
                    rank=rank_idx,
                    player_name=display_name,
                    points=float(pts),
                    province=display_prov,
                    previous_rank=None,
                    player_id=display_id,
                    partner_name=partner_name,
                    partner_player_id=partner_player_id,
                )
            )
    else:
        # Singles: take top N players
        top = candidates[:limit]
        for i, entry in enumerate(top, start=1):
            out.append(
                RankingEntry(
                    rank=i,
                    player_name=entry["player_name"],
                    points=entry["points"],
                    province=entry["province"],
                    previous_rank=None,
                    player_id=entry["player_id"],
                )
            )

    print(f"✅ ABC: {len(out)} joueurs extraits (top {limit})")
    if out:
        print(f"   1er: {out[0].player_name} ({out[0].points})")
    return out

async def scrape_badminton_canada_news(limit: int = 20) -> List[NewsItem]:
    """
    Scrape Badminton Canada news via the public RSS feed (includes image enclosure URLs).
    """
    print(f"🌐 Scraping News RSS: {BADMINTON_CANADA_NEWS_FEED}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(BADMINTON_CANADA_NEWS_FEED, headers=headers, follow_redirects=True)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "xml")
    items: List[NewsItem] = []

    for item in soup.find_all("item"):
        title = (item.find("title").get_text(strip=True) if item.find("title") else "").strip()
        link = (item.find("link").get_text(strip=True) if item.find("link") else "").strip()
        guid = (item.find("guid").get_text(strip=True) if item.find("guid") else "").strip()
        pub = (item.find("pubDate").get_text(strip=True) if item.find("pubDate") else None)
        desc = (item.find("description").get_text(strip=True) if item.find("description") else None)

        enclosure = item.find("enclosure")
        image_url = enclosure.get("url") if enclosure and enclosure.get("url") else None

        if not link or not title:
            continue

        items.append(NewsItem(
            id=guid or link,
            title=title,
            url=link,
            image_url=image_url,
            excerpt=desc or None,
            published=pub
        ))

        # Keep collecting; we will filter by language below.

    # Keep only French items (feed mixes FR + EN)
    fr_items = []
    for it in items:
        fr, en, accents = _lang_score(it.title)
        if accents or (fr > en):
            fr_items.append(it)
    return fr_items[:limit]


async def scrape_news_from_sheet(limit: int = 20) -> List[NewsItem]:
    """
    Read a public Google Sheet published as CSV.
    Expected columns: title, url, image_url, excerpt, published
    """
    if not NEWS_SHEET_CSV_URL:
        return []

    async with httpx.AsyncClient(timeout=20.0) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = await client.get(NEWS_SHEET_CSV_URL, headers=headers, follow_redirects=True)
        resp.raise_for_status()

    text = resp.text or ""
    reader = csv.DictReader(io.StringIO(text))
    items: List[NewsItem] = []
    for row in reader:
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        if not title or not url:
            continue
        items.append(
            NewsItem(
                id=url,
                title=title,
                url=url,
                image_url=(row.get("image_url") or "").strip() or None,
                excerpt=(row.get("excerpt") or "").strip() or None,
                published=(row.get("published") or "").strip() or None,
            )
        )
        if len(items) >= limit:
            break
    return items


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _normalize_person_name(name: str) -> str:
    raw = _strip_accents((name or "").strip().lower())
    raw = re.sub(r"[^\w\s,]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) >= 2:
            raw = f"{parts[1]} {parts[0]}".strip()
    return raw


def _elo_expected(rating_a: float, rating_b: float) -> float:
    # E = 1 / (1 + 10^((Rb - Ra)/400))
    return 1.0 / (1.0 + (10.0 ** ((rating_b - rating_a) / 400.0)))


def _k_factor(tournament_name: str) -> int:
    t = (tournament_name or "").lower()
    if "championship" in t or "championnat" in t:
        return 100
    if "national" in t or "nationaux" in t or "canadian" in t:
        return 75
    if "abc" in t or "provinc" in t or "quebec" in t:
        return 50
    # Default: treat as provincial-level
    return 50


def _try_parse_date(text: str) -> Optional[datetime]:
    s = (text or "").strip()
    if not s:
        return None
    # common patterns: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt)
        except Exception:
            pass
    return None


def _normalize_team_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


def _seed_order_for_player(
    player_id: Optional[str],
    player_name: str,
    national_seed: dict[str, int],
    abc_seed: dict[str, int],
    name_to_id: dict[str, str],
) -> int:
    if player_id and player_id in national_seed:
        return national_seed[player_id]
    if player_id and player_id in abc_seed:
        return abc_seed[player_id]
    norm = _normalize_person_name(player_name)
    pid = name_to_id.get(norm)
    if pid and pid in national_seed:
        return national_seed[pid]
    if pid and pid in abc_seed:
        return abc_seed[pid]
    return 9999


async def _fetch_draw_players(draw_url: str) -> List[dict]:
    """
    Best-effort parse of draw page to extract player ids + names.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = await client.get(draw_url, headers=headers)
        resp.raise_for_status()
        html = resp.text or ""

    soup = BeautifulSoup(html, "html.parser")
    players: List[dict] = []
    seen = set()

    # First pass: anchors (most reliable for ids)
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if "player=" not in href and "playerid=" not in href and "/player/" not in href:
            continue
        name = _normalize_team_name(a.get_text(" ", strip=True))
        if not name or name.lower() == "bye":
            continue

        pid = None
        m = re.search(r"[?&]player=([0-9]+)", href)
        if not m:
            m = re.search(r"[?&]playerid=([0-9]+)", href)
        if not m:
            m = re.search(r"/player/([0-9]+)", href)
        if m:
            pid = m.group(1)

        key = (pid or "", name)
        if key in seen:
            continue
        seen.add(key)
        players.append({"player_id": pid, "player_name": name})

    if players:
        return players

    # Fallback: try common draw participant selectors (no ids)
    text_candidates: List[str] = []
    for sel in [
        ".participant__name",
        ".draw__participant",
        ".draw__team",
        ".team__name",
        ".event-match__player",
        ".match__player",
        ".player",
        ".player__name",
    ]:
        for el in soup.select(sel):
            t = _normalize_team_name(el.get_text(" ", strip=True))
            if t:
                text_candidates.append(t)

    for t in text_candidates:
        if t.lower() == "bye":
            continue
        key = ("", t)
        if key in seen:
            continue
        seen.add(key)
        players.append({"player_id": None, "player_name": t})

    return players


async def _find_upcoming_abc_quebec_tournaments(limit: int = 5) -> List[TournamentSearchItem]:
    today = datetime.now().date().isoformat()
    out: List[TournamentSearchItem] = []
    seen_ids: set[str] = set()
    queries = ["ABC Quebec", "ABC Québec", "ABC"]

    for q in queries:
        items = await search_tournaments_ts(query=q, page=1, limit=25)
        for it in items:
            if it.tournament_id in seen_ids:
                continue
            sd = (it.start_date or "").split(" ")[0] if it.start_date else ""
            if not sd or sd < today:
                continue
            loc = (it.location or "").lower()
            if q == "ABC":
                # For generic query, keep only Quebec-ish locations
                if "quebec" not in loc and "québec" not in loc and "qc" not in loc:
                    continue
            seen_ids.add(it.tournament_id)
            out.append(it)
            if len(out) >= limit:
                return out

    return out


async def _find_upcoming_national_tournaments(limit: int = 5) -> List[TournamentSearchItem]:
    today = datetime.now().date().isoformat()
    out: List[TournamentSearchItem] = []
    seen_ids: set[str] = set()
    queries = ["National", "Nationaux", "Canadian", "Canada"]

    for q in queries:
        items = await search_tournaments_ts(query=q, page=1, limit=25)
        for it in items:
            if it.tournament_id in seen_ids:
                continue
            sd = (it.start_date or "").split(" ")[0] if it.start_date else ""
            if not sd or sd < today:
                continue
            seen_ids.add(it.tournament_id)
            out.append(it)
            if len(out) >= limit:
                return out
    return out


def _extract_tournament_name_from_soup(soup: BeautifulSoup) -> Optional[str]:
    for tag in ["h1", "h2", "h3"]:
        el = soup.find(tag)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                return text
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return title or None


def _extract_names_from_lines(lines: List[str]) -> List[str]:
    names: List[str] = []
    for raw in lines:
        t = raw.strip()
        if not t:
            continue
        t = re.sub(r"\[[^\]]+\]", "", t).strip()  # remove seeds [3/4]
        low = t.lower()
        if low in ["h2h", "bye", "cancelled"]:
            continue
        if re.search(r"\b(round|groupe|group|final|semi|quarter|venue|court)\b", low):
            continue
        if re.search(r"\b(MSA|WSA|MS|WS|MD|WD|XD|DMB|DMC|DMA|DDA|DDC)\b", t):
            continue
        if re.search(r"\d", t):
            # skip times / scores / venue numbers
            continue
        if len(t) < 3:
            continue
        names.append(t)
    # de-dupe, preserve order
    out = []
    seen = set()
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _detect_event_code(lines: List[str]) -> Optional[str]:
    joined = " ".join(lines)
    joined = re.sub(r"\s+", " ", joined)
    men_codes = [
        "MSA", "MSB", "MSC", "MS",
        "SMA", "SMB", "SMC", "SM",
        "SIMPLE HOMMES", "MEN'S SINGLES", "MENS SINGLES",
    ]
    women_codes = [
        "WSA", "WSB", "WSC", "WS",
        "SFA", "SFB", "SFC", "SF",
        "SIMPLE FEMMES", "WOMEN'S SINGLES", "WOMENS SINGLES",
    ]
    for code in men_codes:
        if re.search(rf"\b{re.escape(code)}\b", joined, flags=re.IGNORECASE):
            return "MS"
    for code in women_codes:
        if re.search(rf"\b{re.escape(code)}\b", joined, flags=re.IGNORECASE):
            return "WS"
    return None


async def _fetch_tournament_matches(tournament_id: str) -> tuple[Optional[str], List[dict]]:
    tid = (tournament_id or "").strip()
    if not re.match(r"^[0-9A-Fa-f\\-]{36}$", tid):
        return None, []
    url = f"{TS_BASE}/tournament/{tid}/Matches"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text or ""

    soup = BeautifulSoup(html, "html.parser")
    tname = _extract_tournament_name_from_soup(soup)

    matchups: List[dict] = []
    seen = set()
    debug_blocks: List[dict] = []

    # Match cards often include "H2H" link/button; use it as anchor.
    for node in soup.find_all(string=lambda s: s and "H2H" in s):
        container = node.parent
        for _ in range(4):
            if not container or container.name in ["body", "html"]:
                break
            text = container.get_text("\n", strip=True)
            if text and text.count("\n") >= 3:
                break
            container = container.parent
        if not container:
            continue

        text = container.get_text("\n", strip=True)
        lines = [l for l in text.split("\n") if l.strip()]
        event = _detect_event_code(lines)
        if event not in ["MS", "WS"]:
            if len(debug_blocks) < 5:
                debug_blocks.append({"event": event, "lines": lines[:8]})
            continue

        names = _extract_names_from_lines(lines)
        if len(names) < 2:
            if len(debug_blocks) < 5:
                debug_blocks.append({"event": event, "lines": lines[:8]})
            continue
        player_a = names[0]
        player_b = names[1]
        key = (event, player_a, player_b)
        if key in seen:
            continue
        seen.add(key)
        matchups.append({
            "event": event,
            "player_a_name": player_a,
            "player_b_name": player_b,
        })
    return tname, matchups


def _pair_first_round(players: List[dict], seed_map: dict[str, int], abc_seed: dict[str, int], name_to_id: dict[str, str]) -> List[tuple[dict, dict]]:
    ranked = []
    for p in players:
        seed = _seed_order_for_player(p.get("player_id"), p.get("player_name", ""), seed_map, abc_seed, name_to_id)
        ranked.append((seed, p))
    ranked.sort(key=lambda it: it[0])
    ordered = [p for _, p in ranked]
    pairs = []
    i, j = 0, len(ordered) - 1
    while i < j:
        pairs.append((ordered[i], ordered[j]))
        i += 1
        j -= 1
    return pairs


def _seed_to_rating(seed: int) -> float:
    if seed <= 0 or seed >= 9999:
        return 1500.0
    # Simple mapping: higher seed => higher rating
    rating = 2000.0 - (seed * 5.0)
    return max(1200.0, min(2000.0, rating))


async def _build_prediction_matchups(
    tournament_id: str,
    tournament_name: str,
    category: str,
    national_seed: dict[str, int],
    abc_seed: dict[str, int],
    name_to_id: dict[str, str],
) -> List[PredictionMatchup]:
    draws = await scrape_tournament_draws_ts(tournament_id)
    matchups: List[PredictionMatchup] = []

    for d in draws:
        name_upper = (d.name or "").upper()
        if category == "MS" and not ("MS" in name_upper or "MEN" in name_upper or "HOMME" in name_upper):
            continue
        if category == "WS" and not ("WS" in name_upper or "WOMEN" in name_upper or "FEMME" in name_upper):
            continue

        draw_players = await _fetch_draw_players(d.url)
        if len(draw_players) < 2:
            continue

        pairs = _pair_first_round(draw_players, national_seed, abc_seed, name_to_id)
        for left, right in pairs:
            pid_a = left.get("player_id")
            pid_b = right.get("player_id")
            seed_a = _seed_order_for_player(pid_a, left.get("player_name", ""), national_seed, abc_seed, name_to_id)
            seed_b = _seed_order_for_player(pid_b, right.get("player_name", ""), national_seed, abc_seed, name_to_id)
            ra = _seed_to_rating(seed_a)
            rb = _seed_to_rating(seed_b)
            e = _elo_expected(ra, rb)
            k = float(_k_factor(tournament_name or d.name))

            delta_a_win = k * (1.0 - e)
            delta_a_loss = k * (0.0 - e)
            delta_b_win = -delta_a_loss
            delta_b_loss = -delta_a_win

            matchups.append(
                PredictionMatchup(
                    player_a_id=pid_a,
                    player_a_name=left.get("player_name") or "",
                    player_b_id=pid_b,
                    player_b_name=right.get("player_name") or "",
                    expected_a=float(e),
                    expected_b=float(1.0 - e),
                    delta_a_win=float(delta_a_win),
                    delta_a_loss=float(delta_a_loss),
                    delta_b_win=float(delta_b_win),
                    delta_b_loss=float(delta_b_loss),
                    k=float(k),
                    source=d.name,
                    tournament_name=tournament_name or None,
                )
            )

    return matchups


def _build_prediction_matchups_from_matches(
    matches: List[dict],
    tournament_name: str,
    category: str,
    national_seed: dict[str, int],
    abc_seed: dict[str, int],
    name_to_id: dict[str, str],
) -> List[PredictionMatchup]:
    matchups: List[PredictionMatchup] = []
    for m in matches:
        if m.get("event") != category:
            continue
        name_a = m.get("player_a_name") or ""
        name_b = m.get("player_b_name") or ""
        pid_a = name_to_id.get(_normalize_person_name(name_a))
        pid_b = name_to_id.get(_normalize_person_name(name_b))

        seed_a = _seed_order_for_player(pid_a, name_a, national_seed, abc_seed, name_to_id)
        seed_b = _seed_order_for_player(pid_b, name_b, national_seed, abc_seed, name_to_id)
        ra = _seed_to_rating(seed_a)
        rb = _seed_to_rating(seed_b)
        e = _elo_expected(ra, rb)
        k = float(_k_factor(tournament_name or "ABC"))

        delta_a_win = k * (1.0 - e)
        delta_a_loss = k * (0.0 - e)
        delta_b_win = -delta_a_loss
        delta_b_loss = -delta_a_win

        matchups.append(
            PredictionMatchup(
                player_a_id=pid_a,
                player_a_name=name_a,
                player_b_id=pid_b,
                player_b_name=name_b,
                expected_a=float(e),
                expected_b=float(1.0 - e),
                delta_a_win=float(delta_a_win),
                delta_a_loss=float(delta_a_loss),
                delta_b_win=float(delta_b_win),
                delta_b_loss=float(delta_b_loss),
                k=float(k),
                source=m.get("event") or "",
                tournament_name=tournament_name or None,
            )
        )
    return matchups


async def _fetch_player_match_rows(player_id: str) -> List[dict]:
    """
    Best-effort scrape of recent matches from TournamentSoftware ranking player page.
    Returns list of dicts: {tournament, opponent, result, score, date}
    """
    pid = str(player_id).strip()
    if not pid or not re.match(r"^\d+$", pid):
        return []
    url = f"{TS_BASE}/ranking/player.aspx?id={RANKING_LIST_ID}&player={pid}"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text or ""

    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        tournament = cells[0]
        opponent = cells[1]
        score = cells[2]
        result_text = cells[3]

        # require a score-ish pattern and W/L-ish token
        score_ok = re.search(r"(\d+\s*-\s*\d+)", score or "") is not None
        rlow = (result_text or "").strip().lower()
        has_res = (rlow in ["w", "l"]) or ("win" in rlow) or ("loss" in rlow) or ("victoire" in rlow) or ("défaite" in rlow) or ("defaite" in rlow)
        if not score_ok and not has_res:
            continue

        is_win = True
        if rlow in ["l"] or "loss" in rlow or "défaite" in rlow or "defaite" in rlow:
            is_win = False
        elif rlow in ["w"] or "win" in rlow or "victoire" in rlow:
            is_win = True
        else:
            # fallback: compare first/last number
            parts = [p.strip() for p in re.split(r"\s*-\s*", score) if p.strip()]
            a = int(parts[0]) if parts and parts[0].isdigit() else 0
            b = int(parts[-1]) if parts and parts[-1].isdigit() else 0
            is_win = a > b

        # date: scan all cells for something parseable
        dt = None
        for c in cells:
            dt = _try_parse_date(c)
            if dt:
                break
        out.append({
            "tournament": tournament,
            "opponent": opponent,
            "score": score,
            "is_win": is_win,
            "date": dt.isoformat() if dt else None,
        })
    return out

@app.get("/")
async def root():
    return {
        "message": "BCR API v3 - Real Data Working",
        "version": "3.0.0",
        "endpoints": [
            "/health",
            "/rankings/{category}/national",
            "/cache/clear"
        ]
    }

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/rankings/{category}", response_model=RankingResponse)
async def get_rankings(category: str, scope: str = "national", province: Optional[str] = None):
    """
    Backward-compatible endpoint used by the iOS app.
    Supports scope=national. Other scopes return a clear error instead of 404.
    """
    scope_norm = (scope or "national").strip().lower()
    if scope_norm in ["national", "nat"]:
        return await get_national_rankings(category)
    if scope_norm == "provincial":
        raise HTTPException(
            status_code=501,
            detail="Le scope provincial n'est plus supporté par cette API."
        )
    raise HTTPException(status_code=400, detail="Scope invalide")


@app.get("/rankings/{category}/national", response_model=RankingResponse)
async def get_national_rankings(category: str):
    category = category.upper()
    
    if category not in ["MS", "WS", "MD", "WD", "XD"]:
        raise HTTPException(status_code=400, detail="Catégorie invalide")
    
    # Vérifier le cache
    cache_key = f"national_{category}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data
    
    print(f"🔄 Scraping de {category}...")
    
    # Scraper les données
    rankings = await scrape_rankings(category)
    
    response = RankingResponse(
        category=category,
        scope="national",
        last_updated=datetime.now().isoformat(),
        rankings=rankings,
        total_count=len(rankings)
    )
    
    # Mettre en cache
    cache[cache_key] = (response, datetime.now())
    
    return response


@app.get("/rankings/{category}/provincial/{province}", response_model=RankingResponse)
async def get_provincial_rankings(category: str, province: str):
    """
    Legacy endpoint kept to avoid 404s. Provincial rankings are no longer supported.
    """
    raise HTTPException(
        status_code=501,
        detail="Le classement provincial n'est plus supporté par cette API."
    )

@app.get("/abc/{tier}/{category}", response_model=RankingResponse)
async def get_abc_rankings(tier: str, category: str):
    """
    Classement Élite ABC (Badminton Québec).
    tier: A/B/C
    category: MS/WS/MD/WD/XD
    """
    tier = tier.upper()
    category = category.upper()

    cache_key = f"abc_{tier}_{category}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        rankings = await scrape_abc_rankings(tier=tier, category=category, limit=20)
        response = RankingResponse(
            category=category,
            scope=f"abc-{tier}",
            last_updated=datetime.now().isoformat(),
            rankings=rankings,
            total_count=len(rankings),
        )
        cache[cache_key] = (response, datetime.now())
        return response
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP ABC")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"❌ ABC erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne ABC")


@app.get("/rankings/{category}/elo", response_model=EloRankingResponse)
async def get_elo_rankings(category: str, debug: bool = Query(False)):
    """
    Elo-like 1v1 ranking for singles based on recent head-to-head results.
    - Uses 52-week sliding window (best-effort based on match row dates if available)
    - K-factor by tournament name: Championship=100, National=75, Provincial/ABC=50
    - Final ordering by avg points per match
    - Active if >= 3 distinct tournaments in window
    Source players: union of National rankings + ABC tier A rankings (same category).
    """
    cat = (category or "").upper().strip()
    if cat not in ["MS", "WS"]:
        raise HTTPException(status_code=400, detail="Elo disponible seulement pour MS/WS (1 contre 1).")

    cache_key = f"elo_{cat}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            return data

    # Source player universe
    national = await scrape_rankings(cat)
    abc_a = await scrape_abc_rankings(tier="A", category=cat, limit=200)
    players = {}
    for it in (national + abc_a):
        if it.player_id:
            players[str(it.player_id)] = it.player_name

    if not players:
        resp = EloRankingResponse(
            category=cat,
            scope="elo",
            last_updated=datetime.now().isoformat(),
            items=[],
            total_count=0,
        )
        cache[cache_key] = (resp, datetime.now())
        return resp

    name_to_id = {_normalize_person_name(n): pid for pid, n in players.items()}

    # Elo computation
    ratings = {pid: 1500.0 for pid in players.keys()}
    total_delta = {pid: 0.0 for pid in players.keys()}
    match_count = {pid: 0 for pid in players.keys()}
    tournaments = {pid: set() for pid in players.keys()}
    debug_info = {
        "source_players": len(players),
        "seen_matches": 0,
        "simulated_matches": 0,
        "upcoming_national": [],
        "upcoming_abc": [],
    }

    cutoff = datetime.now() - timedelta(weeks=52)
    seen_matches: set[str] = set()

    # Build seed maps from rankings
    national_seed = {str(r.player_id): int(r.rank) for r in national if r.player_id}
    abc_seed = {str(r.player_id): int(r.rank) for r in abc_a if r.player_id}

    # Gather matches (best-effort) and apply updates once per unique match
    for pid, pname in players.items():
        rows = await _fetch_player_match_rows(pid)
        for m in rows:
            opp_name = _normalize_person_name(m.get("opponent") or "")
            opp_id = name_to_id.get(opp_name)
            if not opp_id:
                continue
            if opp_id == pid:
                continue

            # date handling
            dt = None
            if m.get("date"):
                try:
                    dt = datetime.fromisoformat(m["date"])
                except Exception:
                    dt = None
            if dt and dt < cutoff:
                continue

            tournament_name = (m.get("tournament") or "").strip()
            score = (m.get("score") or "").strip()

            a, b = sorted([pid, opp_id])
            key = f"{a}|{b}|{tournament_name}|{m.get('date') or ''}|{score}"
            if key in seen_matches:
                continue
            seen_matches.add(key)

            is_win = bool(m.get("is_win"))
            # Determine direction: result is from pid perspective; if pid isn't the canonical "A",
            # we may need to flip.
            if pid != a:
                # pid is b; flip perspective for canonical a
                is_win = not is_win

            ra = ratings[a]
            rb = ratings[b]
            e = _elo_expected(ra, rb)
            result = 1.0 if is_win else 0.0
            k = float(_k_factor(tournament_name))
            delta_a = k * (result - e)
            delta_b = -delta_a

            ratings[a] = ra + delta_a
            ratings[b] = rb + delta_b

            total_delta[a] += delta_a
            total_delta[b] += delta_b
            match_count[a] += 1
            match_count[b] += 1
            if tournament_name:
                tournaments[a].add(tournament_name)
                tournaments[b].add(tournament_name)

    # If we couldn't compute from past matches, simulate from upcoming ABC Quebec draws.
    if not seen_matches:
        upcoming_abc = await _find_upcoming_abc_quebec_tournaments(limit=3)
        upcoming_nat = await _find_upcoming_national_tournaments(limit=3)
        upcoming = upcoming_nat + upcoming_abc
        debug_info["upcoming_national"] = [
            {"id": it.tournament_id, "name": it.name, "start": it.start_date, "draws": 0, "players": 0}
            for it in upcoming_nat
        ]
        debug_info["upcoming_abc"] = [
            {"id": it.tournament_id, "name": it.name, "start": it.start_date, "draws": 0, "players": 0}
            for it in upcoming_abc
        ]
        for t in upcoming:
            draws = await scrape_tournament_draws_ts(t.tournament_id)
            # attach draw counts to debug
            for bucket in ("upcoming_national", "upcoming_abc"):
                for drow in debug_info[bucket]:
                    if drow["id"] == t.tournament_id:
                        drow["draws"] = len(draws)
            for d in draws:
                # Match category by draw name
                name_upper = (d.name or "").upper()
                if cat == "MS" and not ("MS" in name_upper or "MEN" in name_upper or "HOMME" in name_upper):
                    continue
                if cat == "WS" and not ("WS" in name_upper or "WOMEN" in name_upper or "FEMME" in name_upper):
                    continue

                draw_players = await _fetch_draw_players(d.url)
                for bucket in ("upcoming_national", "upcoming_abc"):
                    for drow in debug_info[bucket]:
                        if drow["id"] == t.tournament_id:
                            drow["players"] += len(draw_players)
                if len(draw_players) < 2:
                    continue

                pairs = _pair_first_round(draw_players, national_seed, abc_seed, name_to_id)
                for left, right in pairs:
                    pid_a = left.get("player_id")
                    pid_b = right.get("player_id")
                    if not pid_a or not pid_b:
                        continue
                    if pid_a == pid_b:
                        continue
                    if pid_a not in ratings or pid_b not in ratings:
                        continue

                    # Predict winner: lower seed wins (based on national/ABC seeding).
                    seed_a = _seed_order_for_player(pid_a, left.get("player_name", ""), national_seed, abc_seed, name_to_id)
                    seed_b = _seed_order_for_player(pid_b, right.get("player_name", ""), national_seed, abc_seed, name_to_id)
                    is_win = seed_a <= seed_b

                    ra = ratings[pid_a]
                    rb = ratings[pid_b]
                    e = _elo_expected(ra, rb)
                    result = 1.0 if is_win else 0.0
                    k = float(_k_factor(t.name))
                    delta_a = k * (result - e)
                    delta_b = -delta_a

                    ratings[pid_a] = ra + delta_a
                    ratings[pid_b] = rb + delta_b
                    total_delta[pid_a] += delta_a
                    total_delta[pid_b] += delta_b
                    match_count[pid_a] += 1
                    match_count[pid_b] += 1
                    tournaments[pid_a].add(t.name or "")
                    tournaments[pid_b].add(t.name or "")
                    debug_info["simulated_matches"] += 1

    debug_info["seen_matches"] = len(seen_matches)
    rows: List[EloRankingEntry] = []
    for pid, pname in players.items():
        mc = match_count[pid]
        avg = (total_delta[pid] / mc) if mc > 0 else 0.0
        tcount = len(tournaments[pid])
        active = tcount >= 3
        rows.append(EloRankingEntry(
            rank=0,  # filled after sorting actives
            player_id=pid,
            player_name=pname,
            rating=float(ratings[pid]),
            avg_points_per_match=float(avg),
            matches=mc,
            tournaments=tcount,
            active=active,
        ))

    active_rows = [r for r in rows if r.active]
    inactive_rows = [r for r in rows if not r.active]
    active_rows.sort(key=lambda r: (r.avg_points_per_match, r.rating), reverse=True)
    for i, r in enumerate(active_rows, start=1):
        r.rank = i
    # keep inactive after actives (rank = 0)

    items = active_rows + inactive_rows
    resp = EloRankingResponse(
        category=cat,
        scope="elo",
        last_updated=datetime.now().isoformat(),
        items=items,
        total_count=len(items),
        debug=debug_info if debug else None,
    )
    cache[cache_key] = (resp, datetime.now())
    return resp

@app.get("/news", response_model=NewsResponse)
async def get_news():
    cache_key = "news_badmintonca_fr"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        # Prefer Google Sheet if configured
        items = await scrape_news_from_sheet(limit=20)
        source = NEWS_SHEET_CSV_URL or "badminton.ca"
        if not items:
            items = await scrape_badminton_canada_news(limit=20)
            source = "badminton.ca"
        response = NewsResponse(
            source=source,
            last_updated=datetime.now().isoformat(),
            items=items,
            total_count=len(items),
        )
        cache[cache_key] = (response, datetime.now())
        return response
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP News")
    except Exception as e:
        print(f"❌ News erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne News")


@app.get("/news/custom", response_model=NewsResponse)
async def get_news_custom():
    cache_key = "news_sheet_custom"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        items = await scrape_news_from_sheet(limit=20)
        response = NewsResponse(
            source=NEWS_SHEET_CSV_URL or "sheet",
            last_updated=datetime.now().isoformat(),
            items=items,
            total_count=len(items),
        )
        cache[cache_key] = (response, datetime.now())
        return response
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP News Sheet")
    except Exception as e:
        print(f"❌ News sheet erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne News Sheet")


@app.get("/media/photos/{player_id}", response_model=List[MediaPhoto])
async def list_media_photos(player_id: str):
    items = _load_media_photos(player_id)
    # Add image_url for each item (relative path)
    out: List[MediaPhoto] = []
    for it in items:
        file_name = it.get("file_name") or ""
        object_key = (it.get("object_key") or "").strip()
        if not object_key and file_name:
            object_key = f"photos/{player_id}/{file_name}"
        image_url = MEDIA_STORAGE.public_url_for(object_key) if object_key else None
        if not image_url and file_name:
            # Backward compatible local path (older metadata)
            image_url = f"/media-files/photos/{player_id}/{file_name}"
        out.append(
            MediaPhoto(
                id=it.get("id") or "",
                user_id=it.get("user_id") or player_id,
                file_name=file_name,
                created_at=it.get("created_at") or "",
                added_by=it.get("added_by") or "media",
                added_by_id=it.get("added_by_id"),
                image_url=image_url
            )
        )
    return out


@app.post("/media/photos/{player_id}", response_model=List[MediaPhoto])
async def upload_media_photo(
    player_id: str,
    request: Request,
    file: UploadFile = File(...),
    added_by: str = Form("media"),
    added_by_id: Optional[str] = Form(None),
):
    _enforce_rate_limit(request, bucket="media_write")
    pid = str(player_id).strip()
    if not pid:
        raise HTTPException(status_code=400, detail="player_id invalide")
    added_by = (added_by or "").strip().lower()

    if added_by != "media":
        raise HTTPException(status_code=403, detail="Seuls les médias peuvent ajouter des photos.")
    _require_media_key(request)

    pdir = _media_player_dir(pid)
    os.makedirs(pdir, exist_ok=True)

    # Save file (expects JPEG from app)
    photo_id = str(uuid.uuid4())
    file_name = f"photo_{photo_id}.jpg"

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide")
    object_key = f"photos/{pid}/{file_name}"
    try:
        MEDIA_STORAGE.put_bytes(object_key=object_key, data=data, content_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur storage media: {e}")

    created_at = datetime.now().isoformat()
    items = _load_media_photos(pid)
    items.insert(
        0,
        {
            "id": photo_id,
            "user_id": pid,
            "file_name": file_name,
            "object_key": object_key,
            "created_at": created_at,
            "added_by": added_by,
            "added_by_id": added_by_id,
        }
    )
    _save_media_photos(pid, items)

    # Return updated list
    return await list_media_photos(pid)


@app.delete("/media/photos/{player_id}/{photo_id}", response_model=List[MediaPhoto])
async def delete_media_photo(
    player_id: str,
    photo_id: str,
    request: Request,
    added_by_id: Optional[str] = Query(None),
):
    """
    Deletes a media photo.
    Security note: this is NOT strong auth. We only allow deleting "media" uploads
    when the provided added_by_id matches the stored added_by_id.
    """
    pid = str(player_id).strip()
    if not pid:
        raise HTTPException(status_code=400, detail="player_id invalide")
    photo_id = str(photo_id).strip()
    if not photo_id:
        raise HTTPException(status_code=400, detail="photo_id invalide")

    items = _load_media_photos(pid)
    target = None
    for it in items:
        if (it.get("id") or "") == photo_id:
            target = it
            break

    if not target:
        raise HTTPException(status_code=404, detail="Photo introuvable")

    added_by = (target.get("added_by") or "").strip().lower()
    stored_uploader = (target.get("added_by_id") or "").strip()
    provided = str(added_by_id).strip() if added_by_id is not None else ""

    # Allow deletion when the caller proves they are the original uploader.
    # "media" uploads require matching added_by_id; "self" uploads also require matching added_by_id.
    if added_by != "media":
        raise HTTPException(status_code=403, detail="Suppression non autorisée")
    if not provided or stored_uploader != provided:
        raise HTTPException(status_code=403, detail="Suppression non autorisée")
    _enforce_rate_limit(request, bucket="media_delete")
    _require_media_key(request)

    # Remove object (best-effort)
    object_key = (target.get("object_key") or "").strip()
    if not object_key:
        file_name = (target.get("file_name") or "").strip()
        if file_name:
            object_key = f"photos/{pid}/{file_name}"
    if object_key:
        MEDIA_STORAGE.delete(object_key)

    # Remove from metadata
    next_items = [it for it in items if (it.get("id") or "") != photo_id]
    _save_media_photos(pid, next_items)
    return await list_media_photos(pid)


@app.get("/media/avatar/{player_id}")
async def get_media_avatar(player_id: str):
    pid = str(player_id).strip()
    if not pid:
        raise HTTPException(status_code=400, detail="player_id invalide")
    file_name = f"avatar_{pid}.jpg"
    object_key = f"avatars/{file_name}"
    if not MEDIA_STORAGE.exists(object_key):
        return {"avatar_url": None}
    url = MEDIA_STORAGE.public_url_for(object_key) or f"/media-files/avatars/{file_name}"
    return {"avatar_url": url}


@app.post("/media/avatar/{player_id}")
async def upload_media_avatar(
    player_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    pid = str(player_id).strip()
    if not pid:
        raise HTTPException(status_code=400, detail="player_id invalide")
    _enforce_rate_limit(request, bucket="media_avatar")
    actor_id = (request.headers.get("x-self-id") or "").strip()
    if not actor_id:
        raise HTTPException(status_code=401, detail="Signature manquante.")
    _require_self_signature(request, player_id=pid, actor_id=actor_id, action="upload_avatar")

    os.makedirs(MEDIA_AVATARS_DIR, exist_ok=True)
    file_name = f"avatar_{pid}.jpg"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide")
    object_key = f"avatars/{file_name}"
    try:
        MEDIA_STORAGE.put_bytes(object_key=object_key, data=data, content_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur storage media: {e}")
    return {"avatar_url": MEDIA_STORAGE.public_url_for(object_key) or f"/media-files/avatars/{file_name}"}


@app.get("/player/{player_id}", response_model=PlayerProfileResponse)
async def get_player_profile(player_id: str):
    cache_key = f"player_{player_id}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        profile = await scrape_player_profile(player_id)
        cache[cache_key] = (profile, datetime.now())
        return profile
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP Player")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"❌ Player erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne Player")


@app.get("/players/search", response_model=List[PlayerSearchResult])
async def players_search(q: str):
    q_norm = (q or "").strip().lower()
    cache_key = f"player_search_{q_norm}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        results = await search_players(q, limit=25)
        cache[cache_key] = (results, datetime.now())
        return results
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP Search")
    except Exception as e:
        print(f"❌ Search erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne Search")


@app.get("/abc/calendar", response_model=ABCCalendarResponse)
async def get_abc_calendar():
    cache_key = "abc_calendar"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        events = await scrape_abc_calendar(limit=250)
        resp = ABCCalendarResponse(
            source=ABC_CALENDAR_URL,
            last_updated=datetime.now().isoformat(),
            events=events,
            total_count=len(events),
        )
        cache[cache_key] = (resp, datetime.now())
        return resp
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP ABC Calendar")
    except Exception as e:
        print(f"❌ ABC Calendar erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne ABC Calendar")


@app.get("/tournaments/search", response_model=TournamentSearchResponse)
async def tournaments_search(q: str):
    q = (q or "").strip()
    if len(q) < 1:
        return TournamentSearchResponse(
            query=q,
            source=f"{TS_BASE}/find",
            last_updated=datetime.now().isoformat(),
            items=[],
            total_count=0,
        )
    season_start, season_end = _current_season_range()
    cache_key = f"ts_tournaments_search_{q.lower()}_{season_start}_{season_end}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        items = await search_tournaments_ts(query=q, page=1, limit=25)
        resp = TournamentSearchResponse(
            query=q,
            source=f"{TS_BASE}/find (season {season_start}..{season_end})",
            last_updated=datetime.now().isoformat(),
            items=items,
            total_count=len(items),
        )
        cache[cache_key] = (resp, datetime.now())
        return resp
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP TS search")
    except Exception as e:
        print(f"❌ TS search erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne TS search")


@app.get("/tournaments/live", response_model=TournamentSearchResponse)
async def tournaments_live():
    today = datetime.now().date().isoformat()
    cache_key = f"ts_tournaments_live_{today}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        items = await fetch_live_tournaments_ts(limit=30)
        resp = TournamentSearchResponse(
            query="live",
            source=f"{TS_BASE}/find (live {today})",
            last_updated=datetime.now().isoformat(),
            items=items,
            total_count=len(items),
        )
        cache[cache_key] = (resp, datetime.now())
        return resp
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP TS live")
    except Exception as e:
        print(f"❌ TS live erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne TS live")


@app.get("/tournament/{tournament_id}/draws", response_model=TournamentDrawsResponse)
async def tournament_draws(tournament_id: str):
    cache_key = f"ts_draws_{tournament_id.upper()}"
    if cache_key in cache:
        data, timestamp = cache[cache_key]
        if datetime.now() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit pour {cache_key}")
            return data

    try:
        draws = await scrape_tournament_draws_ts(tournament_id=tournament_id)
        resp = TournamentDrawsResponse(
            tournament_id=tournament_id.upper(),
            source=f"{TS_BASE}/sport/draws.aspx?id={tournament_id.upper()}",
            last_updated=datetime.now().isoformat(),
            draws=draws,
            total_count=len(draws),
        )
        cache[cache_key] = (resp, datetime.now())
        return resp
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Erreur HTTP TS draws")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"❌ TS draws erreur: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne TS draws")


@app.get("/tournament/{tournament_id}/predict", response_model=PredictionResponse)
async def tournament_predict(tournament_id: str, category: str = Query("MS"), debug: bool = Query(False)):
    cat = (category or "").upper().strip()
    if cat not in ["MS", "WS"]:
        raise HTTPException(status_code=400, detail="Prévisions disponibles seulement pour MS/WS (1 contre 1).")

    tid = (tournament_id or "").strip().upper()
    if not re.match(r"^[0-9A-Fa-f\\-]{36}$", tid):
        raise HTTPException(status_code=400, detail="tournament_id invalide")

    # Seeds from national + ABC A
    national = await scrape_rankings(cat)
    abc_a = await scrape_abc_rankings(tier="A", category=cat, limit=200)
    national_seed = {str(r.player_id): int(r.rank) for r in national if r.player_id}
    abc_seed = {str(r.player_id): int(r.rank) for r in abc_a if r.player_id}
    name_to_id = {}
    for r in national + abc_a:
        if not r.player_id:
            continue
        base = _normalize_person_name(r.player_name)
        if base:
            name_to_id[base] = str(r.player_id)
        # also index "Last First" without comma if needed
        raw = _strip_accents((r.player_name or "").strip().lower())
        raw = re.sub(r"[^\w\s,]", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        if "," in raw:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if len(parts) >= 2:
                alt = f"{parts[0]} {parts[1]}".strip()
                name_to_id[alt] = str(r.player_id)

    tname, matches = await _fetch_tournament_matches(tid)
    matchups = _build_prediction_matchups_from_matches(
        matches=matches,
        tournament_name=tname or "",
        category=cat,
        national_seed=national_seed,
        abc_seed=abc_seed,
        name_to_id=name_to_id,
    )
    if not matchups:
        matchups = await _build_prediction_matchups(
            tournament_id=tid,
            tournament_name=tname or "",
            category=cat,
            national_seed=national_seed,
            abc_seed=abc_seed,
            name_to_id=name_to_id,
        )

    return PredictionResponse(
        tournament_id=tid,
        category=cat,
        last_updated=datetime.now().isoformat(),
        matchups=matchups,
        total_count=len(matchups),
        debug={
            "tournament_name": tname,
            "matches_found": len(matches),
        } if debug else None,
    )


@app.get("/player/{player_id}/predictions", response_model=PredictionResponse)
async def player_predictions(player_id: str, category: str = Query("MS"), debug: bool = Query(False)):
    cat = (category or "").upper().strip()
    if cat not in ["MS", "WS"]:
        raise HTTPException(status_code=400, detail="Prévisions disponibles seulement pour MS/WS (1 contre 1).")

    pid = (player_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="player_id invalide")

    national = await scrape_rankings(cat)
    abc_a = await scrape_abc_rankings(tier="A", category=cat, limit=200)
    national_seed = {str(r.player_id): int(r.rank) for r in national if r.player_id}
    abc_seed = {str(r.player_id): int(r.rank) for r in abc_a if r.player_id}
    name_to_id = {}
    for r in national + abc_a:
        if not r.player_id:
            continue
        base = _normalize_person_name(r.player_name)
        if base:
            name_to_id[base] = str(r.player_id)
        raw = _strip_accents((r.player_name or "").strip().lower())
        raw = re.sub(r"[^\w\s,]", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        if "," in raw:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if len(parts) >= 2:
                alt = f"{parts[0]} {parts[1]}".strip()
                name_to_id[alt] = str(r.player_id)

    player_name = None
    for r in national + abc_a:
        if str(r.player_id) == pid:
            player_name = r.player_name
            break

    upcoming_abc = await _find_upcoming_abc_quebec_tournaments(limit=5)
    upcoming_nat = await _find_upcoming_national_tournaments(limit=5)
    upcoming = upcoming_nat + upcoming_abc

    filtered: List[PredictionMatchup] = []
    for t in upcoming:
        tname, matches = await _fetch_tournament_matches(t.tournament_id)
        matchups = _build_prediction_matchups_from_matches(
            matches=matches,
            tournament_name=tname or t.name,
            category=cat,
            national_seed=national_seed,
            abc_seed=abc_seed,
            name_to_id=name_to_id,
        )
        if not matchups:
            matchups = await _build_prediction_matchups(
                tournament_id=t.tournament_id,
                tournament_name=t.name,
                category=cat,
                national_seed=national_seed,
                abc_seed=abc_seed,
                name_to_id=name_to_id,
            )
        for m in matchups:
            if m.player_a_id == pid or m.player_b_id == pid:
                filtered.append(m)
            elif player_name:
                norm = _normalize_person_name(player_name)
                if _normalize_person_name(m.player_a_name) == norm or _normalize_person_name(m.player_b_name) == norm:
                    filtered.append(m)

    return PredictionResponse(
        tournament_id=pid,
        category=cat,
        last_updated=datetime.now().isoformat(),
        matchups=filtered,
        total_count=len(filtered),
        debug={
            "upcoming_count": len(upcoming),
            "filtered_count": len(filtered),
        } if debug else None,
    )

@app.post("/cache/clear")
async def clear_cache():
    cache.clear()
    return {"message": "Cache vidé"}

if __name__ == "__main__":
    import uvicorn
    print("🚀 BCR API v3 sur http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
