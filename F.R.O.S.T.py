#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║      F.R.O.S.T — Fully Responsive Operations & Search Terminal  ║
║      Voice-Controlled AI Desktop Assistant                      ║
║      Snowflake Technologies  ·  Python  ·  Selenium  ·  BS4    ║
╚══════════════════════════════════════════════════════════════════╝

Modules used:
    PyAutoGUI      → GUI automation (screenshot, scroll, type, hotkeys)
    BeautifulSoup  → Web scraping & page summarisation
    Selenium       → Browser automation (search, navigate, YouTube)

Extra dependencies:
    flask              → localhost control panel
    speechrecognition  → microphone voice input
    pyttsx3            → text-to-speech output
    webdriver-manager  → auto-download ChromeDriver
"""

# ── Standard library ──────────────────────────────────────────────────────────
import os
import re
import time
import queue
import logging
import threading
from datetime import datetime

# ── Third-party ───────────────────────────────────────────────────────────────
import requests
import pyttsx3
import pyautogui
import speech_recognition as sr
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, render_template_string
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException

try:
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    _USE_WDM = True
except ImportError:
    _USE_WDM = False

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG  (edit these to customise behaviour)
# ──────────────────────────────────────────────────────────────────────────────
PORT             = 5000          # Web control panel port
SPEECH_RATE      = 165           # Words per minute for TTS
SPEECH_VOLUME    = 1.0           # 0.0 – 1.0
ENERGY_THRESHOLD = 3500          # Mic sensitivity (lower = more sensitive)
LISTEN_TIMEOUT   = 10            # Seconds to wait for speech to start
PHRASE_LIMIT     = 8             # Max seconds per phrase
WAKE_WORDS       = {"frost", "hey frost", "ok frost", "yo frost"}
SCREENSHOT_DIR   = "screenshots" # Where to save screenshots

logging.basicConfig(level=logging.ERROR)

# ──────────────────────────────────────────────────────────────────────────────
# SHARED STATE  (read/written by Flask + voice thread concurrently)
# ──────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()

state = {
    "status":             "Initializing…",
    "listening":          False,
    "speaking":           False,
    "commands_processed": 0,
    "log":                [],          # list of {role, message, time}
    "uptime_start":       datetime.now().isoformat(),
}


def _log(role: str, message: str) -> None:
    """Append a message to the shared log and print it."""
    entry = {
        "role":    role,
        "message": message,
        "time":    datetime.now().strftime("%H:%M:%S"),
    }
    with _lock:
        state["log"].append(entry)
    tag = {"frost": "❄ FROST  ", "user": "🎙 USER  ", "system": "⚙ SYSTEM",
           "error": "❌ ERROR "}.get(role, role.upper())
    print(f"[{tag}]  {message}")


# ──────────────────────────────────────────────────────────────────────────────
# TEXT-TO-SPEECH  (queue-based so it never blocks callers)
# ──────────────────────────────────────────────────────────────────────────────
_tts_q: queue.Queue = queue.Queue()


def _tts_worker() -> None:
    """Dedicated thread: initialises pyttsx3 once, drains the queue."""
    engine = pyttsx3.init()
    engine.setProperty("rate",   SPEECH_RATE)
    engine.setProperty("volume", SPEECH_VOLUME)

    # Pick a pleasant English voice if available
    for v in engine.getProperty("voices"):
        name = v.name.lower()
        if any(k in name for k in ("english", "david", "zira", "en_")):
            engine.setProperty("voice", v.id)
            break

    while True:
        text = _tts_q.get()
        if text is None:            # sentinel → shutdown
            break
        with _lock:
            state["speaking"] = True
            state["status"]   = "Speaking…"
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as exc:
            _log("error", f"TTS error: {exc}")
        finally:
            with _lock:
                state["speaking"] = False
                state["status"]   = "Listening…"


threading.Thread(target=_tts_worker, daemon=True, name="tts-worker").start()


def speak(text: str) -> None:
    """Queue text to be spoken and log it (non-blocking)."""
    _log("frost", text)
    _tts_q.put(text)


# ──────────────────────────────────────────────────────────────────────────────
# SPEECH RECOGNITION
# ──────────────────────────────────────────────────────────────────────────────
_recognizer = sr.Recognizer()
_recognizer.energy_threshold        = ENERGY_THRESHOLD
_recognizer.dynamic_energy_threshold = True
_recognizer.pause_threshold          = 0.8


def listen_once() -> str | None:
    """
    Block until the user says something, return lowercase text.
    Returns None on timeout, silence, or recognition error.
    """
    try:
        with sr.Microphone() as source:
            with _lock:
                state["listening"] = True
                state["status"]    = "Listening…"
            _log("system", "Waiting for voice command…")
            _recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio = _recognizer.listen(
                source,
                timeout=LISTEN_TIMEOUT,
                phrase_time_limit=PHRASE_LIMIT,
            )
        with _lock:
            state["listening"] = False
            state["status"]    = "Recognising…"

        text = _recognizer.recognize_google(audio)
        _log("user", text)
        return text.lower()

    except sr.WaitTimeoutError:
        pass
    except sr.UnknownValueError:
        _log("system", "Could not understand audio — please try again")
    except sr.RequestError as exc:
        _log("error", f"Google Speech API error: {exc}")
    except Exception as exc:
        _log("error", f"Mic error: {exc}")
    finally:
        with _lock:
            state["listening"] = False

    return None


def _strip_wake_word(text: str) -> str:
    """Remove a wake word prefix if present."""
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        if text.startswith(w):
            return text[len(w):].strip()
    return text


# ──────────────────────────────────────────────────────────────────────────────
# SELENIUM — Browser automation
# ──────────────────────────────────────────────────────────────────────────────
_driver: webdriver.Chrome | None = None
_driver_lock = threading.Lock()


def _get_driver() -> webdriver.Chrome:
    """Return a shared Chrome WebDriver instance (lazy init)."""
    global _driver
    with _driver_lock:
        if _driver is None:
            opts = ChromeOptions()
            opts.add_argument("--start-maximized")
            opts.add_argument("--disable-infobars")
            opts.add_experimental_option("detach", True)          # keep open
            if _USE_WDM:
                svc = Service(ChromeDriverManager().install())
                _driver = webdriver.Chrome(service=svc, options=opts)
            else:
                _driver = webdriver.Chrome(options=opts)
        return _driver


def browser_search(query: str) -> None:
    """
    Open Chrome and search Google for *query*.
    Uses BeautifulSoup to extract a featured snippet and read it back.
    """
    speak(f"Searching Google for: {query}")
    d = _get_driver()
    d.get(f"https://www.google.com/search?q={query.replace(' ', '+')}")
    time.sleep(2)

    # ── BeautifulSoup: parse the live page source ─────────────────────────
    soup    = BeautifulSoup(d.page_source, "html.parser")
    snippet = _extract_snippet(soup)

    if snippet:
        speak(f"Here's what I found: {snippet}")
    else:
        speak(f"Opened search results for: {query}")


def _extract_snippet(soup: BeautifulSoup) -> str | None:
    """
    BeautifulSoup helper — extract a readable answer from a Google results page.
    Tries several CSS classes in order of preference.
    """
    selectors = [
        {"class": "hgKElc"},   # featured snippet paragraph
        {"class": "BNeawe"},   # top answer
        {"class": "ILfuVd"},   # knowledge panel
        {"class": "LGOjhe"},   # knowledge panel description
    ]
    for sel in selectors:
        el = soup.find("div", sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if len(txt) > 30:
                return txt[:380]
    return None


def browser_open(site: str) -> None:
    """Navigate Chrome to a URL."""
    if not re.match(r"https?://", site):
        if "." not in site:
            site = f"www.{site}.com"
        site = f"https://{site}"
    speak(f"Opening {site}")
    _get_driver().get(site)


def browser_youtube(query: str) -> None:
    """Search YouTube."""
    speak(f"Searching YouTube for: {query}")
    _get_driver().get(
        f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
    )


def browser_wikipedia(query: str) -> None:
    """Open Wikipedia and read the intro with BeautifulSoup."""
    speak(f"Looking up {query} on Wikipedia")
    url = f"https://en.wikipedia.org/wiki/{query.replace(' ', '_')}"
    _get_driver().get(url)
    time.sleep(1.5)

    # ── BeautifulSoup: scrape the intro paragraph ─────────────────────────
    soup  = BeautifulSoup(_get_driver().page_source, "html.parser")
    intro = soup.select_one("div.mw-parser-output > p:not(.mw-empty-elt)")
    if intro:
        text = re.sub(r"\[.*?\]", "", intro.get_text(" ", strip=True))
        speak(text[:400])
    else:
        speak("I opened Wikipedia but could not extract the article text.")


def page_summarize(url: str) -> str:
    """
    BeautifulSoup: fetch *url* via requests and return a plain-text summary.
    This runs WITHOUT Selenium — pure HTTP + BS4.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FROST/1.0; Snowflake Technologies)"}
        resp    = requests.get(url, headers=headers, timeout=7)
        soup    = BeautifulSoup(resp.content, "html.parser")

        # Strip noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        paras = [
            p.get_text(" ", strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 50
        ]
        return (" ".join(paras[:5]))[:500] or "No readable content found."
    except Exception as exc:
        return f"Could not fetch page: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
# PYAUTOGUI — GUI automation
# ──────────────────────────────────────────────────────────────────────────────
pyautogui.FAILSAFE = True   # move mouse to top-left corner to abort
pyautogui.PAUSE    = 0.04   # small delay between actions


def gui_screenshot() -> None:
    """Capture the whole screen and save it."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"screenshot_{ts}.png")
    pyautogui.screenshot(path)
    speak(f"Screenshot saved as {path}")


def gui_type(text: str) -> None:
    """Type *text* at the current cursor position."""
    speak(f"Typing: {text}")
    time.sleep(0.35)
    pyautogui.typewrite(text, interval=0.04)


def gui_scroll(direction: str, clicks: int = 4) -> None:
    """Scroll the active window up or down."""
    pyautogui.scroll(-clicks if direction == "down" else clicks)
    speak(f"Scrolled {direction}")


def gui_press(key: str) -> None:
    """Press a single key."""
    pyautogui.press(key)
    speak(f"Pressed {key}")


def gui_hotkey(*keys: str) -> None:
    """Execute a keyboard shortcut."""
    pyautogui.hotkey(*keys)
    speak(f"Shortcut: {' + '.join(keys)}")


# ──────────────────────────────────────────────────────────────────────────────
# COMMAND DISPATCHER
# ──────────────────────────────────────────────────────────────────────────────
def dispatch(raw: str) -> None:
    """Parse *raw* (voice or typed text) and call the right handler."""
    if not raw:
        return

    cmd = _strip_wake_word(raw.lower().strip())
    if not cmd:
        speak("Yes? FROST is listening.")
        return

    with _lock:
        state["commands_processed"] += 1
        state["status"] = "Processing…"

    # ── Time & Date ────────────────────────────────────────────────────────
    if any(p in cmd for p in ["what time", "current time", "time is it"]):
        speak(f"It's {datetime.now().strftime('%I:%M %p')}")

    elif any(p in cmd for p in ["what day", "what's today", "today's date", "what is the date"]):
        speak(f"Today is {datetime.now().strftime('%A, %B %d, %Y')}")

    # ── Wikipedia ─────────────────────────────────────────────────────────
    elif any(p in cmd for p in ["wikipedia", "wiki", "tell me about", "who is", "what is"]):
        q = re.sub(r"(wikipedia|wiki|tell me about|who is|what is)\s*", "", cmd).strip()
        browser_wikipedia(q if q else cmd)

    # ── YouTube ───────────────────────────────────────────────────────────
    elif any(p in cmd for p in ["youtube", "play video", "watch", "play on youtube"]):
        q = re.sub(r"(youtube|play video|watch video?|play on youtube|search youtube for)\s*", "", cmd).strip()
        browser_youtube(q if q else cmd)

    # ── Summarize a URL ───────────────────────────────────────────────────
    elif "summarize" in cmd:
        url_m = re.search(r"https?://\S+", cmd)
        if url_m:
            speak("Fetching and summarizing, one moment…")
            summary = page_summarize(url_m.group())
            speak(summary)
        else:
            speak("Please say 'summarize' followed by a full URL.")

    # ── Open website ──────────────────────────────────────────────────────
    elif any(cmd.startswith(p) for p in ["open ", "go to ", "visit ", "navigate to "]):
        site = re.sub(r"^(open|go to|visit|navigate to)\s+", "", cmd).strip()
        browser_open(site)

    # ── Screenshot ────────────────────────────────────────────────────────
    elif any(p in cmd for p in ["screenshot", "take a photo", "capture screen", "take a picture"]):
        gui_screenshot()

    # ── Scroll ────────────────────────────────────────────────────────────
    elif "scroll down" in cmd:
        gui_scroll("down")
    elif "scroll up" in cmd:
        gui_scroll("up")

    # ── Type ──────────────────────────────────────────────────────────────
    elif cmd.startswith("type "):
        gui_type(cmd[5:].strip())

    # ── Press a key ───────────────────────────────────────────────────────
    elif cmd.startswith("press "):
        gui_press(cmd[6:].strip())

    # ── Keyboard shortcuts ────────────────────────────────────────────────
    elif "copy" in cmd:
        gui_hotkey("ctrl", "c")
    elif "paste" in cmd:
        gui_hotkey("ctrl", "v")
    elif "undo" in cmd:
        gui_hotkey("ctrl", "z")
    elif "close tab" in cmd:
        gui_hotkey("ctrl", "w")
    elif "new tab" in cmd:
        gui_hotkey("ctrl", "t")

    # ── Shutdown ──────────────────────────────────────────────────────────
    elif any(p in cmd for p in ["stop", "exit", "quit", "shutdown", "goodbye", "bye", "shut down"]):
        speak("Goodbye. Shutting down FROST. Snowflake Technologies signing off.")
        with _lock:
            state["status"] = "Offline"
        _tts_q.join()               # wait for the goodbye to finish
        try:
            if _driver:
                _driver.quit()
        except Exception:
            pass
        os._exit(0)

    # ── Default: Google search ─────────────────────────────────────────────
    else:
        q = re.sub(r"^(search for|search|google|look up|find)\s+", "", cmd).strip()
        browser_search(q if q else cmd)


# ──────────────────────────────────────────────────────────────────────────────
# FLASK — Web control panel at http://localhost:{PORT}
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.ERROR)

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>F.R.O.S.T — Snowflake Technologies</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet"/>
<style>
/*── Reset ───────────────────────────────────────────────────*/
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;}
:root{
  --c:  #00d4ff;
  --c2: #0090cc;
  --bg: #000c1a;
  --card:#00111f;
  --br: rgba(0,212,255,.18);
  --glow:rgba(0,212,255,.55);
  --txt:#cce8ff;
  --dim:rgba(180,220,255,.45);
}
html,body{height:100%;overflow:hidden;}
body{
  background:var(--bg);
  color:var(--txt);
  font-family:'Share Tech Mono',monospace;
  display:flex;
  flex-direction:column;
}

/*── Hex grid background ─────────────────────────────────────*/
body::before{
  content:'';
  position:fixed;inset:0;
  background:
    radial-gradient(ellipse 70% 50% at 50% 50%, rgba(0,80,180,.06) 0%,transparent 70%),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='98'%3E%3Cpath d='M28 66L0 50V17L28 0l28 17v33z' fill='none' stroke='rgba(0,200,255,.035)' stroke-width='1'/%3E%3Cpath d='M28 100L0 83V50l28-17 28 17v33z' fill='none' stroke='rgba(0,200,255,.035)' stroke-width='1'/%3E%3C/svg%3E");
  pointer-events:none;z-index:0;
}
body>*{position:relative;z-index:1;}

/*── Header ──────────────────────────────────────────────────*/
header{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 28px;
  border-bottom:1px solid var(--br);
  background:linear-gradient(180deg,rgba(0,212,255,.05) 0%,transparent 100%);
  flex-shrink:0;
}
.logo h1{
  font-family:'Orbitron',sans-serif;
  font-size:1.55rem;font-weight:900;
  letter-spacing:.55rem;color:var(--c);
  text-shadow:0 0 22px var(--glow);
}
.logo p{font-size:.58rem;letter-spacing:.28rem;color:var(--dim);margin-top:3px;}
.hstats{display:flex;gap:22px;}
.hstats>div{display:flex;flex-direction:column;align-items:center;gap:2px;}
.hstats strong{font-family:'Orbitron',sans-serif;font-size:1.05rem;color:var(--c);}
.hstats span{font-size:.6rem;color:var(--dim);letter-spacing:.12rem;}

/*── Main grid ───────────────────────────────────────────────*/
.main{flex:1;display:flex;gap:14px;padding:14px;overflow:hidden;min-height:0;}

/*── Left panel ──────────────────────────────────────────────*/
.left{flex:0 0 210px;display:flex;flex-direction:column;gap:12px;}

/*── Orb card ────────────────────────────────────────────────*/
.orb-card{
  background:var(--card);border:1px solid var(--br);border-radius:12px;
  padding:22px 10px;
  display:flex;flex-direction:column;align-items:center;gap:14px;
}
.orb-wrap{position:relative;width:128px;height:128px;display:flex;align-items:center;justify-content:center;}
.ring{
  position:absolute;border:1.5px solid var(--c);border-radius:50%;
  opacity:0;
}
.ring:nth-child(1){width:42px;height:42px;}
.ring:nth-child(2){width:66px;height:66px;}
.ring:nth-child(3){width:94px;height:94px;}
.ring:nth-child(4){width:124px;height:124px;}
@keyframes ripple{
  0%{transform:scale(.82);opacity:.85;}
  100%{transform:scale(1);opacity:0;}
}
body.active .ring:nth-child(1){animation:ripple 2s ease-out infinite 0.0s;}
body.active .ring:nth-child(2){animation:ripple 2s ease-out infinite 0.5s;}
body.active .ring:nth-child(3){animation:ripple 2s ease-out infinite 1.0s;}
body.active .ring:nth-child(4){animation:ripple 2s ease-out infinite 1.5s;}
.orb{
  width:38px;height:38px;border-radius:50%;z-index:2;
  background:radial-gradient(circle at 36% 34%,#55eeff,#003a88);
  border:2px solid var(--c);
  box-shadow:0 0 18px var(--glow),inset 0 0 12px rgba(0,212,255,.3);
  transition:box-shadow .4s;
}
body.active .orb{
  box-shadow:0 0 34px var(--glow),0 0 60px rgba(0,212,255,.25),inset 0 0 16px rgba(0,212,255,.45);
}
#status-txt{
  font-size:.72rem;letter-spacing:.2rem;color:var(--c);
  text-align:center;min-height:1em;
}

/*── Info card ───────────────────────────────────────────────*/
.info-card{
  background:var(--card);border:1px solid var(--br);border-radius:12px;
  padding:13px 15px;font-size:.7rem;color:var(--dim);
}
.info-card .row{display:flex;justify-content:space-between;padding:2px 0;}
.info-card .row b{color:var(--c);}

/*── Commands reference ──────────────────────────────────────*/
.cmd-ref{
  background:var(--card);border:1px solid var(--br);border-radius:12px;
  padding:13px 15px;font-size:.67rem;color:var(--dim);
  line-height:1.95;flex:1;overflow-y:auto;
}
.cmd-ref h3{
  font-size:.62rem;letter-spacing:.2rem;color:var(--c);
  border-bottom:1px solid var(--br);padding-bottom:6px;margin-bottom:8px;
}
.cmd-ref kbd{
  background:rgba(0,212,255,.07);border:1px solid var(--br);
  border-radius:4px;padding:0 5px;
  font-family:'Share Tech Mono',monospace;font-size:.64rem;color:var(--c);
}

/*── Log panel ───────────────────────────────────────────────*/
.log-panel{
  flex:1;display:flex;flex-direction:column;
  background:var(--card);border:1px solid var(--br);border-radius:12px;
  overflow:hidden;
}
.log-head{
  padding:9px 16px;border-bottom:1px solid var(--br);
  font-size:.63rem;letter-spacing:.2rem;color:var(--dim);flex-shrink:0;
}
#log{
  flex:1;overflow-y:auto;padding:14px;
  display:flex;flex-direction:column;gap:10px;
}
#log::-webkit-scrollbar{width:4px;}
#log::-webkit-scrollbar-thumb{background:var(--br);border-radius:2px;}

/*── Bubbles ─────────────────────────────────────────────────*/
.msg{display:flex;flex-direction:column;gap:3px;max-width:80%;}
.msg.user  {align-self:flex-end;align-items:flex-end;}
.msg.frost {align-self:flex-start;align-items:flex-start;}
.msg.system{align-self:center;max-width:94%;}
.msg.error {align-self:center;max-width:94%;}
.bubble{
  padding:9px 14px;border-radius:10px;
  font-size:.82rem;line-height:1.55;
}
.msg.user   .bubble{background:rgba(0,100,200,.22);border:1px solid rgba(0,130,220,.4);color:#d6ecff;}
.msg.frost  .bubble{background:rgba(0,212,255,.06);border:1px solid var(--br);color:var(--c);}
.msg.system .bubble{background:rgba(255,195,0,.04);border:1px solid rgba(255,195,0,.15);color:rgba(255,215,80,.75);font-size:.72rem;text-align:center;}
.msg.error  .bubble{background:rgba(255,40,40,.06);border:1px solid rgba(255,60,60,.25);color:rgba(255,120,120,.85);font-size:.72rem;text-align:center;}
.msg .t{font-size:.6rem;color:var(--dim);}

/*── Input bar ───────────────────────────────────────────────*/
.input-bar{
  padding:11px 14px;border-top:1px solid var(--br);
  display:flex;gap:9px;background:rgba(0,15,35,.6);flex-shrink:0;
}
#cmd-in{
  flex:1;
  background:rgba(0,212,255,.04);border:1px solid var(--br);border-radius:8px;
  color:var(--txt);padding:10px 14px;
  font-family:'Share Tech Mono',monospace;font-size:.84rem;
  outline:none;transition:border-color .25s,box-shadow .25s;
}
#cmd-in:focus{border-color:var(--c);box-shadow:0 0 14px rgba(0,212,255,.15);}
#cmd-in::placeholder{color:var(--dim);}
.btn{
  font-family:'Orbitron',sans-serif;font-size:.68rem;font-weight:700;
  letter-spacing:.08rem;padding:10px 18px;border-radius:8px;
  border:1px solid var(--c);background:rgba(0,212,255,.07);
  color:var(--c);cursor:pointer;
  transition:background .2s,box-shadow .2s;white-space:nowrap;
}
.btn:hover{background:rgba(0,212,255,.18);box-shadow:0 0 14px rgba(0,212,255,.28);}
.btn:active{transform:scale(.97);}
</style>
</head>
<body>

<!-- HEADER -->
<header>
  <div class="logo">
    <h1>F.R.O.S.T</h1>
    <p>FULLY RESPONSIVE OPERATIONS &amp; SEARCH TERMINAL &mdash; SNOWFLAKE TECHNOLOGIES</p>
  </div>
  <div class="hstats">
    <div><strong id="hd-time">--:--:--</strong><span>TIME</span></div>
    <div><strong id="hd-cmds">0</strong><span>COMMANDS</span></div>
    <div><strong id="hd-stat">INIT</strong><span>STATUS</span></div>
  </div>
</header>

<!-- MAIN -->
<div class="main">

  <!-- LEFT PANEL -->
  <div class="left">

    <!-- Orb / Pulse -->
    <div class="orb-card">
      <div class="orb-wrap">
        <div class="ring"></div><div class="ring"></div>
        <div class="ring"></div><div class="ring"></div>
        <div class="orb"></div>
      </div>
      <div id="status-txt">INITIALIZING</div>
    </div>

    <!-- Info -->
    <div class="info-card">
      <div class="row"><span>HOST</span>   <b>localhost:{{ port }}</b></div>
      <div class="row"><span>MODULES</span><b>3 active</b></div>
      <div class="row"><span>BROWSER</span><b>Chrome</b></div>
      <div class="row"><span>TTS</span>    <b>pyttsx3</b></div>
      <div class="row"><span>ASR</span>    <b>Google API</b></div>
    </div>

    <!-- Commands reference -->
    <div class="cmd-ref">
      <h3>VOICE COMMANDS</h3>
      &ldquo;Hey Frost, search for <kbd>query</kbd>&rdquo;<br/>
      &ldquo;Open <kbd>website.com</kbd>&rdquo;<br/>
      &ldquo;YouTube <kbd>query</kbd>&rdquo;<br/>
      &ldquo;Who is / What is <kbd>X</kbd>&rdquo;<br/>
      &ldquo;Summarize <kbd>URL</kbd>&rdquo;<br/>
      &ldquo;Screenshot&rdquo;<br/>
      &ldquo;Scroll down / up&rdquo;<br/>
      &ldquo;Type <kbd>text</kbd>&rdquo;<br/>
      &ldquo;Press <kbd>key</kbd>&rdquo;<br/>
      &ldquo;Copy / Paste / Undo&rdquo;<br/>
      &ldquo;What time is it&rdquo;<br/>
      &ldquo;What day is today&rdquo;<br/>
      &ldquo;Goodbye&rdquo; / &ldquo;Exit&rdquo;<br/>
    </div>

  </div><!-- /left -->

  <!-- LOG PANEL -->
  <div class="log-panel">
    <div class="log-head">LIVE CONVERSATION LOG — SNOWFLAKE TECHNOLOGIES</div>
    <div id="log"></div>
    <div class="input-bar">
      <input id="cmd-in" type="text" placeholder="Type a command and press Enter (or just speak to FROST)…" autocomplete="off"/>
      <button class="btn" onclick="sendCmd()">EXECUTE</button>
    </div>
  </div>

</div><!-- /main -->

<script>
const logEl   = document.getElementById('log');
const statEl  = document.getElementById('status-txt');
const hdStat  = document.getElementById('hd-stat');
const hdCmds  = document.getElementById('hd-cmds');
const hdTime  = document.getElementById('hd-time');
const input   = document.getElementById('cmd-in');
let   lastLen = 0;

// ── Clock ───────────────────────────────────────────────────
function tick(){
  hdTime.textContent = new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
setInterval(tick, 1000); tick();

// ── Poll backend ─────────────────────────────────────────────
async function poll(){
  try{
    const r = await fetch('/status');
    const d = await r.json();

    const s = (d.status||'').toUpperCase();
    statEl.textContent = s;
    hdStat.textContent = s.split(' ')[0];
    hdCmds.textContent = d.commands_processed ?? 0;

    document.body.classList.toggle('active', d.listening||d.speaking);

    const entries = d.log || [];
    if(entries.length > lastLen){
      for(let i=lastLen; i<entries.length; i++) addMsg(entries[i]);
      lastLen = entries.length;
      logEl.scrollTop = logEl.scrollHeight;
    }
  }catch(_){}
}
setInterval(poll, 650); poll();

function addMsg(e){
  const w = document.createElement('div');
  w.className = `msg ${e.role}`;
  w.innerHTML = `<div class="bubble">${esc(e.message)}</div><div class="t">${e.time}</div>`;
  logEl.appendChild(w);
}

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function sendCmd(){
  const v = input.value.trim();
  if(!v) return;
  input.value = '';
  await fetch('/command',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({command:v})
  });
}

input.addEventListener('keydown', e=>{ if(e.key==='Enter') sendCmd(); });
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(_HTML, port=PORT)


@app.route("/status")
def get_status():
    with _lock:
        return jsonify(dict(state))


@app.route("/command", methods=["POST"])
def post_command():
    data = request.get_json(silent=True) or {}
    cmd  = data.get("command", "").strip()
    if cmd:
        _log("user", cmd)
        threading.Thread(target=dispatch, args=(cmd,), daemon=True).start()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No command provided"}), 400


# ──────────────────────────────────────────────────────────────────────────────
# VOICE LOOP
# ──────────────────────────────────────────────────────────────────────────────
def voice_loop() -> None:
    """Runs in a background thread: listen → dispatch, forever."""
    speak("FROST online. Snowflake Technologies control panel is live at localhost, port five thousand.")
    while True:
        try:
            text = listen_once()
            if text:
                threading.Thread(target=dispatch, args=(text,), daemon=True).start()
        except Exception as exc:
            _log("error", str(exc))
        time.sleep(0.05)


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    banner = f"""
╔══════════════════════════════════════════════════════════════════╗
║   F.R.O.S.T — Snowflake Technologies AI Assistant              ║
║   Fully Responsive Operations & Search Terminal                 ║
║   Control Panel → http://localhost:{PORT}                           ║
║   Press  Ctrl+C  to quit                                        ║
╚══════════════════════════════════════════════════════════════════╝"""
    print(banner)

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    with _lock:
        state["status"] = "Starting…"

    # Start the continuous voice listening thread
    threading.Thread(target=voice_loop, daemon=True, name="voice-loop").start()

    # Start Flask (blocks main thread)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
