# ❄️ F.R.O.S.T COMING SOON!

### Fully Responsive Operations & Search Terminal
**Voice-Controlled AI Desktop Assistant — Snowflake Technologies**

---

## What it does
FROST listens to your microphone, understands your command, carries it out **without you touching anything**, and speaks the result back to you. A live control panel runs at `http://localhost:5000` so you can monitor every conversation or type commands manually.

---

## 📦 Modules used (school requirement: 3)

| Module | Role |
|---|---|
| **PyAutoGUI** | GUI automation — takes screenshots, types text, scrolls pages, triggers keyboard shortcuts |
| **BeautifulSoup** | Web scraping — parses Google featured snippets, Wikipedia intros, and any page summary you ask for |
| **Selenium** | Browser automation — opens Chrome, navigates URLs, searches Google, opens YouTube |

Plus: `flask` (control panel), `SpeechRecognition` (microphone → text), `pyttsx3` (text → speech).

---

## 🔧 Installation

### 1. Clone the repo
```bash
git clone https://github.com/Snowflake-Technologies/F.R.O.S.T.git
cd frost
```

### 2. Create a virtual environment (recommended)
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

> **Linux users** — install PortAudio first:
> ```bash
> sudo apt install portaudio19-dev python3-pyaudio
> ```
>
> **macOS users:**
> ```bash
> brew install portaudio
> ```

### 4. Make sure Google Chrome is installed
`webdriver-manager` will automatically download the matching ChromeDriver, so you don't need to do it manually.

---

## ▶️ Running FROST

```bash
python F.R.O.S.T.py
```

You will see:
```
╔══════════════════════════════════════════════════════════════════╗
║   F.R.O.S.T — Snowflake Technologies AI Assistant              ║
║   Fully Responsive Operations & Search Terminal                 ║
║   Control Panel → http://localhost:5000                         ║
║   Press  Ctrl+C  to quit                                        ║
╚══════════════════════════════════════════════════════════════════╝
```

Open **http://localhost:5000** in your browser to see the Snowflake Technologies control panel.

FROST will greet you and start listening immediately.

---

## 🎙️ Voice Commands

Just say these — no button pressing needed! You can prefix any command with **"Hey Frost"**, **"OK Frost"**, or just **"Frost"**.

| Say… | What happens |
|---|---|
| `"Hey Frost, search for black holes"` | Opens Chrome, searches Google, reads back a snippet |
| `"Open youtube.com"` | Navigates Chrome to YouTube |
| `"YouTube lo-fi music"` | Searches YouTube for lo-fi music |
| `"Who is Elon Musk"` | Opens Wikipedia, reads the intro paragraph |
| `"What is quantum computing"` | Wikipedia lookup |
| `"Summarize https://example.com"` | Fetches the page with BeautifulSoup, reads a summary |
| `"Screenshot"` | Saves a screenshot to `screenshots/` folder |
| `"Scroll down"` | Scrolls the active window down |
| `"Scroll up"` | Scrolls up |
| `"Type Hello World"` | Types at the current cursor position |
| `"Press enter"` | Presses Enter key |
| `"Copy"` | Ctrl+C |
| `"Paste"` | Ctrl+V |
| `"Undo"` | Ctrl+Z |
| `"What time is it"` | Speaks the current time |
| `"What day is today"` | Speaks today's date |
| `"Goodbye"` / `"Exit"` | Shuts FROST down |

---

## 🖥️ Control Panel (`http://localhost:5000`)

- **Live orb animation** — pulses when FROST is listening or speaking
- **Conversation log** — every command and response shown in real-time
- **Manual input** — type a command instead of speaking
- **System info** — module status, command count, uptime

---

## 📁 Project Structure

```
frost/
├── jarvis.py          ← Main application (all logic in one file)
├── requirements.txt   ← Python dependencies
├── README.md          ← This file
└── screenshots/       ← Auto-created when you take a screenshot
```

---

## ⚙️ Configuration

At the top of `jarvis.py` you can change:

```python
PORT             = 5000    # Web panel port
SPEECH_RATE      = 165     # Words per minute
ENERGY_THRESHOLD = 3500    # Mic sensitivity (lower = picks up quieter sounds)
LISTEN_TIMEOUT   = 10      # Seconds to wait for you to start speaking
WAKE_WORDS       = {"frost", "hey frost", "ok frost", "yo frost"}
```

---

## 🛠️ Troubleshooting

**Microphone not working**
- Make sure your mic is set as the default input device in your OS settings.
- Try lowering `ENERGY_THRESHOLD` in the config section.

**"No module named 'pyaudio'"**
- Windows: `pip install pipwin` then `pipwin install pyaudio`
- Linux: `sudo apt install python3-pyaudio`

**ChromeDriver error**
- Make sure Google Chrome is installed.
- `webdriver-manager` handles the driver automatically, but it needs Chrome itself.

**FROST not understanding speech**
- Speak clearly and not too fast.
- Make sure you have an internet connection (Google Speech API is used).

---

## 📄 License
MIT — feel free to use, modify and share.

---

*Snowflake Technologies — Built with Python 3.12, Selenium, BeautifulSoup & PyAutoGUI.*
