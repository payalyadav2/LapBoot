# lapbot_final.py — LAPBOT ULTRA (final)
from __future__ import annotations
import os, sys, time, re, json, threading, subprocess, webbrowser, urllib.parse, platform
from datetime import datetime
from collections import deque

# Optional libs (graceful fallback)
try:
    import speech_recognition as sr
except Exception:
    sr = None
try:
    import pyttsx3
except Exception:
    pyttsx3 = None
try:
    import pyautogui
    pyautogui.FAILSAFE = True
except Exception:
    pyautogui = None
try:
    import requests
except Exception:
    requests = None

# Selenium optional (only used for optional YouTube auto-play)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False

# ---------------- CONFIG ----------------
VERBOSE = True
USE_TEXT_MODE = False               # set True to force text input instead of mic
RELEASE_MIC_DELAY = 0.25
EST_SECONDS_PER_WORD = 0.34
TTS_BLOCK_ENABLED = True
TTS_SAFETY_BUFFER = 0.05
ACTION_HISTORY_SIZE = 100
MACROS_PATH = os.path.join(os.path.expanduser("~"), ".lapbot_macros.json")
SELENIUM_HEADLESS = False
SELENIUM_PAGE_LOAD_TIMEOUT = 12

# OpenAI / ChatGPT settings
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # must be set in env to enable ChatGPT queries
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
AI_MODE = bool(OPENAI_API_KEY and requests)  # enabled only if key present and requests available
AI_TIMEOUT_SECONDS = 12

OS_NAME = platform.system()

# ---------------- HELPERS ----------------
def safe_print(*a, **k):
    if VERBOSE:
        try: print(*a, **k)
        except: pass

def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- TTS ----------------
_GLOBAL_PYTTS_ENGINE = None

def _system_tts_spawn(text: str):
    try:
        if OS_NAME == "Windows":
            ps = f'Add-Type –AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak("{text}")'
            subprocess.Popen(["powershell", "-Command", ps])
            return True
        elif OS_NAME == "Darwin":
            subprocess.Popen(["say", text])
            return True
        else:
            subprocess.Popen(["spd-say", text])
            return True
    except Exception as e:
        safe_print("TTS spawn failed:", e)
        return False

def _pyttsx3_say(text: str, block=True):
    global _GLOBAL_PYTTS_ENGINE
    if not pyttsx3:
        return False
    try:
        if _GLOBAL_PYTTS_ENGINE is None:
            _GLOBAL_PYTTS_ENGINE = pyttsx3.init()
            _GLOBAL_PYTTS_ENGINE.setProperty("rate", 150)
        engine = _GLOBAL_PYTTS_ENGINE
        engine.stop()
        engine.say(text)
        engine.runAndWait()
        return True
    except Exception as e:
        safe_print("pyttsx3 failed:", e)
        return False

def tts_say(text: str, block=True):
    # prefer pyttsx3 if available, else system TTS
    if pyttsx3:
        ok = _pyttsx3_say(text, block)
        if ok: return True
    ok = _system_tts_spawn(text)
    if ok and block:
        est = min(12, max(1, len(text.split())) * EST_SECONDS_PER_WORD)
        time.sleep(est)
    return ok

def speak(text: str):
    tts_say(text, block=True)

_tts_active = threading.Event()

def speak_and_block(text: str, safety_buffer=None):
    if safety_buffer is None: safety_buffer = TTS_SAFETY_BUFFER
    try:
        _tts_active.set()
        tts_say(text, block=True)
        time.sleep(safety_buffer)
    finally:
        _tts_active.clear()

# ---------------- MICROPHONE / LISTEN ----------------
recognizer = None
if sr:
    try:
        recognizer = sr.Recognizer()
    except Exception:
        recognizer = None

def listen_from_mic(timeout=6, phrase_time_limit=7):
    if not recognizer:
        return None
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.4)
            safe_print("Listening...")
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
    except Exception:
        return None
    try:
        return recognizer.recognize_google(audio)
    except Exception:
        return None

def listen(prompt=None, timeout=6, phrase_time_limit=7):
    if prompt:
        tts_say(prompt, block=False)
        time.sleep(0.2)
    if USE_TEXT_MODE or not recognizer:
        try:
            return input("> ")
        except Exception:
            return None
    return listen_from_mic(timeout, phrase_time_limit)

# ---------------- ACTION QUEUE ----------------
_action_queue = deque()
_action_history = deque(maxlen=ACTION_HISTORY_SIZE)
_action_lock = threading.Lock()
_stop_worker = False

def enqueue_action(func, *args, **kwargs):
    with _action_lock:
        _action_queue.append((func, args, kwargs))

def _worker_loop():
    while not _stop_worker:
        while _tts_active.is_set():
            time.sleep(0.03)
        if _action_queue:
            with _action_lock:
                func, args, kwargs = _action_queue.popleft()
            try:
                func(*args, **kwargs)
                _action_history.appendleft((now_ts(), getattr(func, "__name__", str(func)), args, kwargs))
            except Exception as e:
                safe_print("Worker error:", e)
        else:
            time.sleep(0.05)

threading.Thread(target=_worker_loop, daemon=True).start()

# ---------------- MACROS ----------------
def load_macros():
    if os.path.exists(MACROS_PATH):
        try:
            return json.load(open(MACROS_PATH, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_macros(macros):
    json.dump(macros, open(MACROS_PATH, "w", encoding="utf-8"), indent=2)

_macros = load_macros()

def run_macro(name: str):
    name = name.strip().lower()
    if name not in _macros:
        speak("Macro nahi mila.")
        return
    for step in _macros[name]:
        enqueue_action(process_command_sync, step)
    speak("Macro started.")

# ---------------- SELENIUM DRIVER HELP ----------------
def _make_selenium_driver():
    try:
        opt = Options()
        if SELENIUM_HEADLESS:
            opt.add_argument("--headless=new")
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opt)
        driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
        return driver
    except Exception as e:
        safe_print("Selenium driver failed:", e)
        return None

# ---------------- OS / GUI ACTIONS ----------------
def open_app_or_search(query: str):
    q = (query or "").strip().lower()
    safe_print("open_app_or_search:", q)
    if not q:
        speak("Kya open karna hai?")
        return
    if q.startswith("http://") or q.startswith("https://") or ("." in q and " " not in q):
        url = q if q.startswith("http") else "http://" + q
        webbrowser.open(url); speak(f"Opening {url}"); return
    if "youtube" in q:
        webbrowser.open("https://www.youtube.com"); speak("Opening YouTube."); return
    if "google" == q or q == "search":
        webbrowser.open("https://www.google.com"); speak("Opening Google."); return
    if "calculator" in q or q == "calc":
        try:
            if OS_NAME == "Windows": subprocess.Popen(["calc.exe"])
            elif OS_NAME == "Darwin": subprocess.Popen(["open", "-a", "Calculator"])
            else:
                for cmd in (["gnome-calculator"], ["kcalc"], ["galculator"]):
                    try: subprocess.Popen(cmd); break
                    except: pass
            speak("Calculator opened."); return
        except Exception as e:
            safe_print("open calculator failed:", e)
    if "terminal" in q or "cmd" in q or "powershell" in q:
        try:
            if OS_NAME == "Windows": subprocess.Popen(["cmd.exe"])
            elif OS_NAME == "Darwin": subprocess.Popen(["open", "-a", "Terminal"])
            else:
                for cmd in (["gnome-terminal"], ["konsole"], ["xterm"], ["tilix"]):
                    try: subprocess.Popen(cmd); break
                    except: pass
            speak("Terminal opened."); return
        except Exception as e:
            safe_print("open terminal failed:", e)
    # fallback: google search
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    webbrowser.open(url); speak(f"Searching for {query}")

def close_app(name: str):
    if not name: return
    safe_print("close_app:", name)
    try:
        if OS_NAME == "Windows":
            subprocess.call(["taskkill", "/IM", f"{name}.exe", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.call(["pkill", "-f", name])
        speak(f"Closed {name}")
    except Exception as e:
        safe_print("close_app failed:", e)
        speak("Close nahi hua.")

def open_top_result_for(cmd: str):
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(cmd)
    webbrowser.open(url); speak(f"Searching for {cmd}")

def play_on_youtube(term: str):
    if not term:
        webbrowser.open("https://www.youtube.com"); speak("Opening YouTube"); return
    query = urllib.parse.quote_plus(term)
    url = f"https://www.youtube.com/results?search_query={query}"
    webbrowser.open(url); speak(f"Searching YouTube for {term}")
    if SELENIUM_AVAILABLE and not SELENIUM_HEADLESS:
        try:
            driver = _make_selenium_driver()
            if driver:
                driver.get(url); time.sleep(1.5)
                elems = driver.find_elements(By.CSS_SELECTOR, "a#video-title")
                if elems:
                    elems[0].click(); speak("Playing first result.")
                else:
                    driver.quit()
        except Exception as e:
            safe_print("selenium yt error:", e)

# ---------------- pyautogui helpers ----------------
def do_type_sync(text: str):
    if not pyautogui:
        speak("Typing unavailable (pyautogui missing)."); return
    try:
        pyautogui.write(text, interval=0.02)
    except Exception as e:
        safe_print("do_type_sync failed:", e)

def do_press_sync(key_combo: str):
    if not pyautogui:
        speak("Press unavailable (pyautogui missing)."); return
    try:
        parts = re.split(r"\s*[\+\-]\s*|\s+", key_combo.strip())
        parts = [p.lower() for p in parts if p]
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            for k in parts[:-1]: pyautogui.keyDown(k)
            pyautogui.press(parts[-1])
            for k in reversed(parts[:-1]): pyautogui.keyUp(k)
    except Exception as e:
        safe_print("do_press_sync error:", e)

def move_mouse_relative(direction: str, px: int = 100):
    if not pyautogui:
        speak("Mouse movement unavailable (pyautogui missing)."); return
    try:
        dx = dy = 0
        if direction == "left": dx = -px
        elif direction == "right": dx = px
        elif direction == "up": dy = -px
        elif direction == "down": dy = px
        pyautogui.moveRel(dx, dy, duration=0.2)
    except Exception as e:
        safe_print("move_mouse_relative failed:", e)

def do_scroll_cmd(direction: str, amt: int = 300):
    if not pyautogui:
        speak("Scroll unavailable (pyautogui missing)."); return
    try:
        pyautogui.scroll(amt if direction == "up" else -amt)
    except Exception as e:
        safe_print("do_scroll_cmd failed:", e)

# ---------------- Local Q&A and safe math ----------------
local_qa = {
    "what is your name": "Mera naam Jarvis hai. Main tumhara LAPBOT hoon.",
    "who made you": "Mujhe tumne banaya hai — main tumhari madad ke liye hoon.",
    "tell me a joke": "Ek programmer se bola: 'Mujhe aisa code likh do jo khud ko fix kar le.' Programmer bola: 'Use recursion.'",
    "how are you": "Main theek hoon, tum batao kaise ho?",
}

math_allowed = re.compile(r'^[0-9\.\+\-\*\/\(\)\s%]+$')
def safe_eval_math(expr: str):
    expr = expr.strip()
    if not math_allowed.match(expr): return None
    try: return str(eval(expr, {"__builtins__": None}, {}))
    except Exception:
        return None

# ---------------- OpenAI helper ----------------
def query_openai_chat(prompt: str, model: str = None, timeout: int = AI_TIMEOUT_SECONDS):
    if not OPENAI_API_KEY or not requests:
        return None, "OpenAI not configured."
    model = model or OPENAI_MODEL
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    system_msg = ("You are a helpful assistant that answers briefly and in friendly Hinglish if appropriate. "
                  "Keep replies concise; if user asks for code, return code in a code block.")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.6,
        "max_tokens": 600
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code != 200:
            safe_print("OpenAI API error:", r.status_code, r.text)
            return None, f"API error {r.status_code}"
        data = r.json()
        out = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return out.strip(), None
    except Exception as e:
        safe_print("OpenAI request failed:", e)
        return None, str(e)

# ---------------- ask ChatGPT and speak ----------------
def ask_chatgpt_and_speak(query: str):
    q = (query or "").strip()
    safe_print("ask_chatgpt_and_speak:", q)
    if not q:
        speak("Kya poochna hai?")
        return
    if not OPENAI_API_KEY or not requests:
        speak("OpenAI API key ya requests library missing. Local fallback use karta hoon.")
        enqueue_action(handle_question, q)
        return
    speak("Thik hai, main ChatGPT se poochta hoon...")
    out, err = query_openai_chat(q)
    if out:
        # speak reasonably (don't flood with extremely long replies)
        short = out.strip()
        words = short.split()
        if len(words) > 140:
            # speak first ~60 words then open a browser search so user can read full answer
            speak(" ".join(words[:80]) + " ... baki browser me khol raha hoon.")
            try: webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
            except: pass
        else:
            speak(short)
    else:
        safe_print("ChatGPT error:", err)
        speak("ChatGPT se jawab nahi mila. Web search khol raha hoon.")
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))

# ---------------- fallback question handler ----------------
def handle_question(query: str):
    q = (query or "").strip()
    safe_print("handle_question:", q)
    if not q:
        speak("Kya poochna hai?")
        return
    q_lower = q.lower()
    if q_lower in local_qa:
        ans = local_qa[q_lower]
        speak(ans); return
    m = re.search(r'(what is|calculate|what\'s|solve)\s+(.+)', q_lower)
    if m:
        expr = m.group(2)
        expr = re.sub(r'[^\d\.\+\-\*\/\(\)\s%]', ' ', expr)
        res = safe_eval_math(expr)
        if res is not None:
            speak(f"{expr.strip()} = {res}"); return
    if "time" in q_lower:
        speak(datetime.now().strftime("Samay %I:%M %p hai.")); return
    if "date" in q_lower or "day" in q_lower:
        speak(datetime.now().strftime("Aaj ki date %Y-%m-%d hai.")); return
    if "joke" in q_lower:
        speak(local_qa.get("tell me a joke")); return
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
    speak("Maine web par search khol diya, dekho browser mein.")

# ---------------- COMMAND PROCESSOR ----------------
INTENT_PATTERNS = [
    ("EXIT", re.compile(r"\b(exit|quit|stop|shutdown)\b", re.IGNORECASE)),
    ("OPEN", re.compile(r"\b(open|launch|start) (.+)", re.IGNORECASE)),
    ("CLOSE", re.compile(r"\bclose (.+)", re.IGNORECASE)),
    ("PLAY_YT", re.compile(r"\bplay (.+) on youtube\b", re.IGNORECASE)),
    ("CLICK_AT", re.compile(r"\bclick at (\d+)[ ,]+(\d+)\b", re.IGNORECASE)),
    ("CLICK", re.compile(r"\bclick\b", re.IGNORECASE)),
    ("MOVE_REL", re.compile(r"\bmove (left|right|up|down)(?: (\d+))?\b", re.IGNORECASE)),
    ("SCROLL", re.compile(r"\bscroll (up|down)(?: (\d+))?\b", re.IGNORECASE)),
    ("TYPE", re.compile(r"\btype (.+)", re.IGNORECASE)),
    ("PRESS", re.compile(r"\bpress (.+)", re.IGNORECASE)),
    ("MACRO_RUN", re.compile(r"\brun macro (.+)", re.IGNORECASE)),
    ("MACRO_SAVE", re.compile(r"\bsave macro (.+) as (.+)", re.IGNORECASE)),
    ("ASK", re.compile(r"\b(what|who|how|when|where|why|tell me|define|search for|calculate|solve)\b(.+)", re.IGNORECASE)),
]

def process_command_sync(cmd: str):
    if not cmd: return
    cmd = cmd.strip()
    safe_print("process_command_sync:", cmd)
    for intent, pat in INTENT_PATTERNS:
        m = pat.search(cmd)
        if not m: continue
        if intent == "EXIT":
            speak("Goodbye."); os._exit(0)
        if intent == "OPEN":
            enqueue_action(open_app_or_search, m.group(2)); return
        if intent == "CLOSE":
            enqueue_action(close_app, m.group(1)); return
        if intent == "PLAY_YT":
            enqueue_action(play_on_youtube, m.group(1)); return
        if intent == "CLICK_AT":
            x, y = int(m.group(1)), int(m.group(2))
            if pyautogui: enqueue_action(lambda a,b: pyautogui.click(a,b), x, y)
            else: speak("pyautogui installed nahi hai."); return
        if intent == "CLICK":
            if pyautogui: enqueue_action(lambda: pyautogui.click())
            else: speak("pyautogui installed nahi hai."); return
        if intent == "MOVE_REL":
            direction = m.group(1); px = int(m.group(2)) if m.group(2) else 100
            enqueue_action(move_mouse_relative, direction, px); return
        if intent == "SCROLL":
            d = m.group(1); amt = int(m.group(2)) if m.group(2) else 300
            enqueue_action(do_scroll_cmd, d, amt); return
        if intent == "TYPE":
            enqueue_action(do_type_sync, m.group(1)); return
        if intent == "PRESS":
            enqueue_action(do_press_sync, m.group(1)); return
        if intent == "MACRO_RUN":
            enqueue_action(run_macro, m.group(1)); return
        if intent == "ASK":
            # If user used "what/why/..." treat as question; if prefixed by "jarvis" we prefer ChatGPT
            enqueue_action(handle_question, cmd); return
    # fallback
    enqueue_action(open_top_result_for, cmd)

def process_command(cmd: str):
    time.sleep(RELEASE_MIC_DELAY)
    speak_and_block("Theek hai. " + cmd)
    enqueue_action(process_command_sync, cmd)

# ---------------- MAIN LOOP (Jarvis trigger: direct ChatGPT ask) ----------------
def startup_diagnostics():
    safe_print("=== LAPBOT STARTUP ===")
    safe_print("DEBUG KEY:", bool(OPENAI_API_KEY))
    safe_print("REQUESTS:", bool(requests))
    safe_print("AI_MODE:", AI_MODE)
    tts_say("Startup complete.", block=True)

def main_loop():
    global USE_TEXT_MODE
    if "--text-mode" in sys.argv:
        USE_TEXT_MODE = True
    startup_diagnostics()
    speak("Hello. Jarvis ready. Say Jarvis to activate me.")
    while True:
        text = listen(timeout=6, phrase_time_limit=8)
        if not text:
            continue
        raw = text.strip()
        if not raw:
            continue
        lower = raw.lower()
        # If user says 'jarvis' with something after -> ask ChatGPT (preferred)
        if "jarvis" in lower:
            idx = lower.find("jarvis")
            rest = raw[idx + len("jarvis"):].strip()
            if rest:
                # Prefer ChatGPT if available; else fallback to local handler
                if AI_MODE:
                    enqueue_action(ask_chatgpt_and_speak, rest)
                else:
                    # if API not configured, still try local handler first then open web search
                    enqueue_action(handle_question, rest)
                continue
            else:
                speak("Haan? Kya karna hai? Bol do.")
                reply = listen(timeout=6, phrase_time_limit=8)
                if not reply:
                    speak("Say again."); continue
                if AI_MODE:
                    enqueue_action(ask_chatgpt_and_speak, reply)
                else:
                    enqueue_action(process_command_sync, reply)
                continue
        # Normal commands without explicit 'Jarvis'
        process_command(raw)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        safe_print("Exit by user")
        pass
