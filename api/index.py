import os
import time
import tempfile
import asyncio
import requests
from datetime import datetime
from flask import Flask, jsonify, Response

try:
    from shazamio import Shazam
except Exception:
    Shazam = None


app = Flask(__name__)

# ============================================================
# CONFIGURAÇÕES
# ============================================================

RADIO_NAME = "Radio Antena 1 Brasil"
STREAM_URL = "https://antenaone.crossradio.com.br/stream/2;"

# No plano gratuito / ambiente serverless, mantemos abaixo dos 10 segundos.
CAPTURE_SECONDS = 9

# Limite para não criar ficheiros grandes em /tmp
MAX_CAPTURE_BYTES = 1_800_000

DEFAULT_COVER = "https://images.unsplash.com/photo-1516280440614-37939bbacd81?auto=format&fit=crop&w=900&q=80"

LAST_IDENTIFICATION = {
    "success": False,
    "title": RADIO_NAME,
    "artist": "Ao vivo",
    "cover": DEFAULT_COVER,
    "itunes": "",
    "time": "",
    "message": "Aguardando identificação automática..."
}


# ============================================================
# HELPERS
# ============================================================

def normalize_song(title, artist):
    title = (title or "").strip()
    artist = (artist or "").strip()

    if not title:
        title = "Desconhecido"

    if not artist:
        artist = "Desconhecido"

    return title, artist


def search_itunes_cover(title, artist):
    try:
        query = f"{artist} {title}"

        r = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "media": "music",
                "limit": 1
            },
            timeout=6
        )

        data = r.json()

        if data.get("resultCount", 0) > 0:
            item = data["results"][0]
            cover = item.get("artworkUrl100", "")

            if cover:
                cover = cover.replace("100x100bb", "600x600bb")

            return {
                "cover": cover,
                "itunes": item.get("trackViewUrl", "")
            }

    except Exception:
        pass

    return {
        "cover": "",
        "itunes": ""
    }


def capture_stream_without_ffmpeg():
    """
    Versão preparada para Vercel:
    - Não usa FFmpeg.
    - Grava diretamente bytes MP3/AAC do stream.
    - Guarda apenas temporariamente em /tmp.
    - Para antes dos 10 segundos.
    """

    tmp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".mp3",
        dir="/tmp"
    )
    output_file = tmp.name
    tmp.close()

    started = time.time()
    total = 0

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Connection": "close"
    }

    try:
        with requests.get(
            STREAM_URL,
            stream=True,
            headers=headers,
            timeout=(5, 12)
        ) as r:
            r.raise_for_status()

            with open(output_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue

                    f.write(chunk)
                    total += len(chunk)

                    elapsed = time.time() - started

                    if elapsed >= CAPTURE_SECONDS:
                        break

                    if total >= MAX_CAPTURE_BYTES:
                        break

        if os.path.exists(output_file) and os.path.getsize(output_file) > 80_000:
            return output_file

        try:
            os.remove(output_file)
        except Exception:
            pass

        return None

    except Exception:
        try:
            os.remove(output_file)
        except Exception:
            pass

        return None


async def recognize_with_shazam(file_path):
    if Shazam is None:
        return None

    shazam = Shazam()
    result = await shazam.recognize(file_path)

    track = result.get("track")
    if not track:
        return None

    title = track.get("title", "")
    artist = track.get("subtitle", "")

    cover = ""
    images = track.get("images", {})

    if images:
        cover = images.get("coverarthq") or images.get("coverart") or ""

    return {
        "title": title,
        "artist": artist,
        "cover": cover,
        "url": track.get("url", "")
    }


def identify_song_now():
    global LAST_IDENTIFICATION

    if Shazam is None:
        return {
            "success": False,
            "title": RADIO_NAME,
            "artist": "Ao vivo",
            "cover": DEFAULT_COVER,
            "itunes": "",
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": "O módulo shazamio não está instalado."
        }

    audio_file = None

    try:
        audio_file = capture_stream_without_ffmpeg()

        if not audio_file:
            return {
                "success": False,
                "title": RADIO_NAME,
                "artist": "Ao vivo",
                "cover": DEFAULT_COVER,
                "itunes": "",
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": "Não foi possível gravar 9 segundos do stream em /tmp."
            }

        result = asyncio.run(recognize_with_shazam(audio_file))

        if not result:
            return {
                "success": False,
                "title": LAST_IDENTIFICATION.get("title", RADIO_NAME),
                "artist": LAST_IDENTIFICATION.get("artist", "Ao vivo"),
                "cover": LAST_IDENTIFICATION.get("cover", DEFAULT_COVER),
                "itunes": LAST_IDENTIFICATION.get("itunes", ""),
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": "O Shazam não conseguiu identificar esta música."
            }

        title = result.get("title", "")
        artist = result.get("artist", "")
        cover = result.get("cover", "")
        shazam_url = result.get("url", "")

        title, artist = normalize_song(title, artist)

        itunes_data = search_itunes_cover(title, artist)

        if not cover:
            cover = itunes_data.get("cover", "")

        if not cover:
            cover = DEFAULT_COVER

        itunes_url = itunes_data.get("itunes", "") or shazam_url

        LAST_IDENTIFICATION = {
            "success": True,
            "title": title,
            "artist": artist,
            "cover": cover,
            "itunes": itunes_url,
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": "Música identificada automaticamente."
        }

        return LAST_IDENTIFICATION

    except Exception as e:
        return {
            "success": False,
            "title": LAST_IDENTIFICATION.get("title", RADIO_NAME),
            "artist": LAST_IDENTIFICATION.get("artist", "Ao vivo"),
            "cover": LAST_IDENTIFICATION.get("cover", DEFAULT_COVER),
            "itunes": LAST_IDENTIFICATION.get("itunes", ""),
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": f"Erro ao identificar música: {str(e)}"
        }

    finally:
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
            except Exception:
                pass


# ============================================================
# ROTAS
# ============================================================

@app.route("/")
def index():
    html = """
<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <title>Radio Antena 1 Brasil</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <style>
        :root {
            --creme: #f7e6c4;
            --creme2: #f2d39b;
            --marisco: #d8793b;
            --marisco2: #b9542d;
            --castanho: #3b2418;
            --dark: #140c08;
            --glass: rgba(255, 239, 210, 0.16);
            --white: #fff8ec;
            --gold: #ffd185;
            --danger: #ff6b4a;
            --success: #79ffbc;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            min-height: 100vh;
            font-family: Arial, Helvetica, sans-serif;
            background:
                radial-gradient(circle at 20% 20%, rgba(255, 202, 120, 0.35), transparent 30%),
                radial-gradient(circle at 80% 10%, rgba(216, 121, 59, 0.32), transparent 30%),
                radial-gradient(circle at 50% 90%, rgba(255, 232, 180, 0.24), transparent 35%),
                linear-gradient(135deg, #1a0f09, #3b2012 45%, #0d0704);
            color: var(--white);
            overflow-x: hidden;
        }

        body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background-image:
                linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px);
            background-size: 42px 42px;
            mask-image: radial-gradient(circle, black, transparent 75%);
        }

        .page {
            width: min(1200px, 94%);
            margin: auto;
            padding: 32px 0 60px;
            position: relative;
            z-index: 2;
        }

        .hero {
            display: grid;
            grid-template-columns: 1.1fr 0.9fr;
            gap: 28px;
            align-items: center;
            min-height: 88vh;
        }

        .panel {
            background: linear-gradient(145deg, rgba(255, 245, 221, 0.18), rgba(255, 210, 145, 0.08));
            border: 1px solid rgba(255, 229, 185, 0.25);
            box-shadow:
                0 25px 80px rgba(0,0,0,0.45),
                inset 0 0 0 1px rgba(255,255,255,0.05);
            backdrop-filter: blur(18px);
            border-radius: 34px;
            overflow: hidden;
        }

        .main-card {
            padding: 38px;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            padding: 10px 16px;
            background: rgba(255, 209, 133, 0.14);
            border: 1px solid rgba(255, 209, 133, 0.35);
            border-radius: 999px;
            color: var(--gold);
            font-weight: 800;
            letter-spacing: 0.4px;
            margin-bottom: 20px;
        }

        .live-dot {
            width: 11px;
            height: 11px;
            border-radius: 50%;
            background: #ff4d30;
            box-shadow: 0 0 18px #ff4d30;
            animation: pulse 1.2s infinite;
        }

        @keyframes pulse {
            0%, 100% {
                transform: scale(1);
                opacity: 1;
            }

            50% {
                transform: scale(1.45);
                opacity: 0.55;
            }
        }

        h1 {
            font-size: clamp(42px, 7vw, 86px);
            line-height: 0.96;
            letter-spacing: -3px;
            margin-bottom: 18px;
            background: linear-gradient(90deg, #fff4dc, #ffd185, #d8793b);
            -webkit-background-clip: text;
            color: transparent;
            text-shadow: 0 0 35px rgba(255, 209, 133, 0.18);
        }

        .subtitle {
            color: rgba(255, 248, 236, 0.78);
            font-size: 18px;
            line-height: 1.7;
            max-width: 620px;
            margin-bottom: 28px;
        }

        .now-playing {
            display: grid;
            grid-template-columns: 120px 1fr;
            gap: 18px;
            padding: 18px;
            background: rgba(20, 12, 8, 0.44);
            border: 1px solid rgba(255, 229, 185, 0.18);
            border-radius: 26px;
            margin-bottom: 24px;
            position: relative;
            overflow: hidden;
        }

        .cover {
            width: 120px;
            height: 120px;
            border-radius: 22px;
            object-fit: cover;
            box-shadow:
                0 18px 40px rgba(0,0,0,0.4),
                0 0 0 1px rgba(255,255,255,0.12);
            background: var(--creme2);
        }

        .small-label {
            color: var(--gold);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 2px;
            font-weight: 900;
            margin-bottom: 8px;
        }

        .song-title {
            font-size: 26px;
            font-weight: 900;
            color: #fff8ec;
            margin-bottom: 6px;
            word-break: break-word;
        }

        .song-artist {
            color: rgba(255, 248, 236, 0.68);
            font-size: 17px;
            word-break: break-word;
        }

        .controls {
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
            align-items: center;
        }

        button {
            border: none;
            cursor: pointer;
            border-radius: 18px;
            padding: 15px 22px;
            font-weight: 900;
            color: #1d1008;
            background: linear-gradient(135deg, #fff1cf, #ffd185 45%, #d8793b);
            box-shadow:
                0 14px 34px rgba(216, 121, 59, 0.32),
                inset 0 1px 0 rgba(255,255,255,0.55);
            transition: 0.25s ease;
            font-size: 15px;
        }

        button:hover {
            transform: translateY(-3px);
        }

        button.secondary {
            background: rgba(255, 240, 207, 0.12);
            color: var(--white);
            border: 1px solid rgba(255, 229, 185, 0.25);
            box-shadow: none;
        }

        button.danger {
            background: linear-gradient(135deg, #ffb199, #ff6b4a);
            color: #250a04;
        }

        .volume-wrap {
            flex: 1;
            min-width: 180px;
            display: flex;
            align-items: center;
            gap: 12px;
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.12);
            padding: 12px 14px;
            border-radius: 18px;
        }

        .volume-wrap span {
            white-space: nowrap;
            color: rgba(255,248,236,0.78);
            font-weight: 800;
        }

        input[type="range"] {
            width: 100%;
            accent-color: var(--marisco);
        }

        .status {
            margin-top: 14px;
            color: rgba(255,248,236,0.75);
            font-size: 14px;
            min-height: 22px;
        }

        .auto-status {
            margin-top: 10px;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 9px 13px;
            border-radius: 999px;
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.13);
            color: rgba(255,248,236,0.78);
            font-size: 13px;
            font-weight: 800;
        }

        .auto-light {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background: #777;
        }

        .auto-on .auto-light {
            background: var(--success);
            box-shadow: 0 0 14px var(--success);
        }

        .visual-card {
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .radio-orb {
            height: 420px;
            border-radius: 32px;
            background:
                radial-gradient(circle at 50% 45%, rgba(255, 245, 221, 0.88), rgba(255, 209, 133, 0.38) 30%, rgba(216, 121, 59, 0.16) 55%, transparent 72%),
                linear-gradient(145deg, rgba(255,255,255,0.12), rgba(255,255,255,0.02));
            display: grid;
            place-items: center;
            position: relative;
            overflow: hidden;
            border: 1px solid rgba(255, 229, 185, 0.22);
        }

        .radio-orb::before {
            content: "";
            position: absolute;
            width: 540px;
            height: 540px;
            border-radius: 50%;
            border: 2px solid rgba(255, 229, 185, 0.18);
            animation: spin 18s linear infinite;
        }

        .radio-orb::after {
            content: "";
            position: absolute;
            width: 300px;
            height: 300px;
            border-radius: 50%;
            border: 2px dashed rgba(255, 209, 133, 0.3);
            animation: spin 9s linear infinite reverse;
        }

        @keyframes spin {
            to {
                transform: rotate(360deg);
            }
        }

        .logo-circle {
            width: 230px;
            height: 230px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            text-align: center;
            background:
                linear-gradient(145deg, rgba(255, 248, 236, 0.95), rgba(242, 211, 155, 0.9));
            color: #3b2418;
            box-shadow:
                0 30px 80px rgba(0,0,0,0.45),
                0 0 60px rgba(255, 209, 133, 0.5);
            z-index: 3;
            padding: 20px;
        }

        .logo-circle strong {
            display: block;
            font-size: 34px;
            line-height: 1;
            letter-spacing: -1px;
        }

        .logo-circle span {
            display: block;
            margin-top: 8px;
            font-weight: 900;
            color: var(--marisco2);
            letter-spacing: 2px;
        }

        .equalizer {
            height: 95px;
            display: flex;
            align-items: end;
            justify-content: center;
            gap: 8px;
            padding: 18px;
            border-radius: 24px;
            background: rgba(20, 12, 8, 0.42);
            border: 1px solid rgba(255, 229, 185, 0.15);
        }

        .bar {
            width: 12px;
            border-radius: 999px;
            background: linear-gradient(to top, #d8793b, #ffd185, #fff8ec);
            height: 18px;
            opacity: 0.85;
            animation: eq 1s ease-in-out infinite;
            animation-play-state: paused;
        }

        .playing .bar {
            animation-play-state: running;
        }

        .bar:nth-child(2) { animation-delay: 0.1s; }
        .bar:nth-child(3) { animation-delay: 0.2s; }
        .bar:nth-child(4) { animation-delay: 0.3s; }
        .bar:nth-child(5) { animation-delay: 0.4s; }
        .bar:nth-child(6) { animation-delay: 0.5s; }
        .bar:nth-child(7) { animation-delay: 0.6s; }
        .bar:nth-child(8) { animation-delay: 0.7s; }
        .bar:nth-child(9) { animation-delay: 0.8s; }
        .bar:nth-child(10) { animation-delay: 0.9s; }

        @keyframes eq {
            0%, 100% { height: 18px; }
            50% { height: 72px; }
        }

        .sections {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-top: 26px;
        }

        .list-card {
            padding: 26px;
        }

        .list-card h2 {
            font-size: 28px;
            margin-bottom: 18px;
            color: var(--gold);
        }

        .song-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .mini-song {
            display: grid;
            grid-template-columns: 58px 1fr auto;
            gap: 12px;
            align-items: center;
            padding: 12px;
            border-radius: 18px;
            background: rgba(20, 12, 8, 0.36);
            border: 1px solid rgba(255, 229, 185, 0.13);
        }

        .mini-song img {
            width: 58px;
            height: 58px;
            border-radius: 14px;
            object-fit: cover;
            background: var(--creme2);
        }

        .mini-title {
            font-weight: 900;
            color: #fff8ec;
            margin-bottom: 4px;
        }

        .mini-artist {
            color: rgba(255,248,236,0.62);
            font-size: 13px;
        }

        .mini-count {
            color: #1d1008;
            background: linear-gradient(135deg, #fff1cf, #ffd185, #d8793b);
            padding: 7px 10px;
            border-radius: 999px;
            font-weight: 900;
            font-size: 12px;
        }

        .empty {
            color: rgba(255,248,236,0.52);
            padding: 20px;
            border-radius: 18px;
            background: rgba(20, 12, 8, 0.28);
            border: 1px dashed rgba(255, 229, 185, 0.2);
        }

        audio {
            display: none;
        }

        a {
            color: inherit;
            text-decoration: none;
        }

        @media (max-width: 900px) {
            .hero {
                grid-template-columns: 1fr;
                min-height: auto;
            }

            .sections {
                grid-template-columns: 1fr;
            }

            .radio-orb {
                height: 320px;
            }

            .logo-circle {
                width: 190px;
                height: 190px;
            }

            .logo-circle strong {
                font-size: 28px;
            }
        }

        @media (max-width: 600px) {
            .main-card {
                padding: 24px;
            }

            .now-playing {
                grid-template-columns: 1fr;
            }

            .cover {
                width: 100%;
                height: auto;
                aspect-ratio: 1 / 1;
            }

            .mini-song {
                grid-template-columns: 50px 1fr;
            }

            .mini-count {
                grid-column: 2;
                width: max-content;
            }
        }
    </style>
</head>

<body>
    <audio id="radio" src="__STREAM_URL__" crossorigin="anonymous"></audio>

    <main class="page">
        <section class="hero">
            <div class="panel main-card">
                <div class="badge">
                    <span class="live-dot"></span>
                    AO VIVO · BRASIL
                </div>

                <h1>Radio Antena 1 Brasil</h1>

                <p class="subtitle">
                    Rádio online em modo Super Deus, preparada para Vercel,
                    com visual Creme de Marisco, identificação automática por Shazam
                    e capas via iTunes.
                </p>

                <div class="now-playing">
                    <img id="cover" class="cover" src="__DEFAULT_COVER__" alt="Capa da música">

                    <div class="song-info">
                        <div class="small-label">Agora a tocar</div>
                        <div id="songTitle" class="song-title">Radio Antena 1 Brasil</div>
                        <div id="songArtist" class="song-artist">Ao vivo</div>
                    </div>
                </div>

                <div class="controls">
                    <button id="playBtn">Ligar rádio</button>
                    <button id="identifyBtn" class="secondary">Identificar agora</button>

                    <div class="volume-wrap">
                        <span>Volume</span>
                        <input id="volume" type="range" min="0" max="1" step="0.01" value="0.85">
                    </div>
                </div>

                <div id="status" class="status">Pronto para tocar.</div>

                <div id="autoStatus" class="auto-status">
                    <span class="auto-light"></span>
                    <span id="autoText">Identificação automática parada</span>
                </div>
            </div>

            <div class="panel visual-card">
                <div class="radio-orb">
                    <div class="logo-circle">
                        <div>
                            <strong>ANTENA 1</strong>
                            <span>BRASIL</span>
                        </div>
                    </div>
                </div>

                <div id="equalizer" class="equalizer">
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                </div>
            </div>
        </section>

        <section class="sections">
            <div class="panel list-card">
                <h2>Últimas 10 músicas</h2>
                <div id="historyList" class="song-list">
                    <div class="empty">Ainda não existe histórico.</div>
                </div>
            </div>

            <div class="panel list-card">
                <h2>Top 10 tocadas</h2>
                <div id="topList" class="song-list">
                    <div class="empty">Ainda não existe top.</div>
                </div>
            </div>
        </section>
    </main>

    <script>
        const STREAM_URL = "__STREAM_URL__";
        const DEFAULT_COVER = "__DEFAULT_COVER__";

        const audio = document.getElementById("radio");
        const playBtn = document.getElementById("playBtn");
        const identifyBtn = document.getElementById("identifyBtn");
        const volume = document.getElementById("volume");
        const statusBox = document.getElementById("status");
        const equalizer = document.getElementById("equalizer");

        const cover = document.getElementById("cover");
        const songTitle = document.getElementById("songTitle");
        const songArtist = document.getElementById("songArtist");

        const historyList = document.getElementById("historyList");
        const topList = document.getElementById("topList");

        const autoStatus = document.getElementById("autoStatus");
        const autoText = document.getElementById("autoText");

        let isPlaying = false;
        let identifying = false;
        let autoTimer = null;

        const AUTO_IDENTIFY_INTERVAL = 75000;

        audio.volume = Number(volume.value);

        function setStatus(text) {
            statusBox.textContent = text;
        }

        function setPlayingUI(playing) {
            isPlaying = playing;

            if (playing) {
                playBtn.textContent = "Desligar rádio";
                playBtn.classList.add("danger");
                equalizer.classList.add("playing");
                autoStatus.classList.add("auto-on");
                autoText.textContent = "Identificação automática ativa";
                setStatus("Rádio ligada. A identificação automática está ativa.");
            } else {
                playBtn.textContent = "Ligar rádio";
                playBtn.classList.remove("danger");
                equalizer.classList.remove("playing");
                autoStatus.classList.remove("auto-on");
                autoText.textContent = "Identificação automática parada";
                setStatus("Rádio pausada.");
            }
        }

        playBtn.addEventListener("click", async () => {
            try {
                if (!isPlaying) {
                    audio.src = STREAM_URL + "?t=" + Date.now();
                    await audio.play();
                    setPlayingUI(true);
                    startAutoIdentify();
                } else {
                    audio.pause();
                    setPlayingUI(false);
                    stopAutoIdentify();
                }
            } catch (e) {
                console.error(e);
                setStatus("Erro ao iniciar a rádio. Clica novamente.");
                setPlayingUI(false);
                stopAutoIdentify();
            }
        });

        volume.addEventListener("input", () => {
            audio.volume = Number(volume.value);
        });

        audio.addEventListener("playing", () => {
            setPlayingUI(true);
        });

        audio.addEventListener("pause", () => {
            setPlayingUI(false);
            stopAutoIdentify();
        });

        audio.addEventListener("error", () => {
            setStatus("Erro ao carregar o stream. Tenta novamente.");
            setPlayingUI(false);
            stopAutoIdentify();
        });

        function startAutoIdentify() {
            stopAutoIdentify();

            setTimeout(() => {
                if (isPlaying) {
                    identifySong(false);
                }
            }, 5000);

            autoTimer = setInterval(() => {
                if (isPlaying) {
                    identifySong(false);
                }
            }, AUTO_IDENTIFY_INTERVAL);
        }

        function stopAutoIdentify() {
            if (autoTimer) {
                clearInterval(autoTimer);
                autoTimer = null;
            }
        }

        identifyBtn.addEventListener("click", () => {
            identifySong(true);
        });

        async function identifySong(manual = false) {
            if (identifying) return;

            identifying = true;
            identifyBtn.disabled = true;
            identifyBtn.textContent = "A identificar...";

            if (manual) {
                setStatus("A identificar música manualmente...");
            } else {
                setStatus("A identificar automaticamente. A gravar 9 segundos em /tmp...");
                autoText.textContent = "A identificar música...";
            }

            try {
                const res = await fetch("/api/identify?t=" + Date.now());
                const data = await res.json();

                if (data.success) {
                    updateCurrentUI(data);
                    saveSong(data);
                    renderStats();

                    setStatus(data.message || "Música identificada.");
                    autoText.textContent = "Identificação automática ativa";
                } else {
                    setStatus(data.message || "Não foi possível identificar esta música.");
                    autoText.textContent = isPlaying ? "Identificação automática ativa" : "Identificação automática parada";
                }

            } catch (e) {
                console.error(e);
                setStatus("Erro ao identificar música.");
                autoText.textContent = isPlaying ? "Identificação automática ativa" : "Identificação automática parada";
            }

            identifying = false;
            identifyBtn.disabled = false;
            identifyBtn.textContent = "Identificar agora";
        }

        function updateCurrentUI(data) {
            if (!data) return;

            songTitle.textContent = data.title || "Desconhecido";
            songArtist.textContent = data.artist || "Desconhecido";
            cover.src = data.cover || DEFAULT_COVER;
        }

        function getHistory() {
            try {
                return JSON.parse(localStorage.getItem("antena1_history") || "[]");
            } catch {
                return [];
            }
        }

        function setHistory(history) {
            localStorage.setItem("antena1_history", JSON.stringify(history.slice(0, 10)));
        }

        function getTop() {
            try {
                return JSON.parse(localStorage.getItem("antena1_top") || "{}");
            } catch {
                return {};
            }
        }

        function setTop(top) {
            localStorage.setItem("antena1_top", JSON.stringify(top));
        }

        function saveSong(data) {
            const title = data.title || "Desconhecido";
            const artist = data.artist || "Desconhecido";
            const coverUrl = data.cover || DEFAULT_COVER;
            const itunes = data.itunes || "";

            const key = artist + " - " + title;

            let history = getHistory();

            const repeated =
                history.length &&
                history[0].title === title &&
                history[0].artist === artist;

            if (!repeated) {
                history.unshift({
                    title,
                    artist,
                    cover: coverUrl,
                    itunes,
                    time: new Date().toLocaleTimeString("pt-PT"),
                    date: new Date().toLocaleDateString("pt-PT")
                });

                history = history.slice(0, 10);
                setHistory(history);

                const top = getTop();

                if (!top[key]) {
                    top[key] = {
                        title,
                        artist,
                        cover: coverUrl,
                        itunes,
                        count: 0
                    };
                }

                top[key].count += 1;
                top[key].cover = coverUrl;
                top[key].itunes = itunes;

                setTop(top);
            }
        }

        function renderStats() {
            const history = getHistory();

            if (history.length) {
                historyList.innerHTML = history.map(item => songItem(item, false)).join("");
            } else {
                historyList.innerHTML = `<div class="empty">Ainda não existe histórico.</div>`;
            }

            const topObj = getTop();
            const top = Object.values(topObj)
                .sort((a, b) => Number(b.count || 0) - Number(a.count || 0))
                .slice(0, 10);

            if (top.length) {
                topList.innerHTML = top.map(item => songItem(item, true)).join("");
            } else {
                topList.innerHTML = `<div class="empty">Ainda não existe top.</div>`;
            }
        }

        function songItem(item, showCount = false) {
            const linkStart = item.itunes ? `<a href="${item.itunes}" target="_blank">` : "";
            const linkEnd = item.itunes ? `</a>` : "";

            return `
                <div class="mini-song">
                    ${linkStart}<img src="${item.cover || DEFAULT_COVER}" alt="Capa">${linkEnd}
                    <div>
                        <div class="mini-title">${escapeHtml(item.title || "Desconhecido")}</div>
                        <div class="mini-artist">${escapeHtml(item.artist || "Desconhecido")}</div>
                    </div>
                    ${showCount ? `<div class="mini-count">${item.count}x</div>` : ""}
                </div>
            `;
        }

        function escapeHtml(text) {
            return String(text)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;")
                .replaceAll("'", "&#039;");
        }

        renderStats();
    </script>
</body>
</html>
    """

    html = html.replace("__STREAM_URL__", STREAM_URL)
    html = html.replace("__DEFAULT_COVER__", DEFAULT_COVER)
    return html


@app.route("/api/identify")
def api_identify():
    result = identify_song_now()
    return jsonify(result)


@app.route("/api/current")
def api_current():
    return jsonify(LAST_IDENTIFICATION)


@app.route("/api/health")
def api_health():
    return jsonify({
        "success": True,
        "radio": RADIO_NAME,
        "stream": STREAM_URL,
        "capture_seconds": CAPTURE_SECONDS,
        "tmp": "/tmp",
        "message": "API online."
    })


@app.route("/stream")
def stream_info():
    """
    Na Vercel não usamos proxy infinito de stream.
    O frontend toca diretamente o STREAM_URL.
    """
    return jsonify({
        "success": True,
        "stream_url": STREAM_URL,
        "message": "Na Vercel o áudio toca diretamente no navegador."
    })