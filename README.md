# Digital Signage — Pi Zero 2 W

A lightweight web app that displays uploaded PPTX / PDF / image files in a looping slideshow on a connected monitor.

---

## Project Layout

```
slideshow/
├── app.py               # Flask backend
├── config.json          # Slide metadata (auto-created)
├── requirements.txt
├── media/               # Processed JPEG slides (auto-created)
├── templates/
│   ├── index.html       # Admin / management UI
│   └── display.html     # Fullscreen display loop
└── slideshow.service    # Systemd unit file
```

---

## Pi Setup

### 1 — System dependencies

```bash
sudo apt update

# Core tools + venv support
# python3-full is required on Raspberry Pi OS Bookworm (Debian 12)
# so that the venv includes pip. Without it you get:
#   "externally-managed-environment" / PEP 668 errors
sudo apt install -y python3-full python3-pip python3-venv \
    libreoffice-impress \
    poppler-utils \
    chromium-browser

# Pillow C build dependencies (required — no armv7l wheel on PyPI)
# Without these you get: RequiredDependencyException: jpeg
sudo apt install -y libjpeg-dev zlib1g-dev libpng-dev libfreetype6-dev
```

> **RAM tip:** `libreoffice --headless` is only invoked during upload, not during
> playback. Chromium runs kiosk mode for the display.

### 2 — Python environment

> ⚠️ **Always activate the venv before running pip.**
> Raspberry Pi OS Bookworm (Debian 12) enforces PEP 668 and blocks pip
> from installing packages into the system Python directly. Running pip
> outside the venv gives an `externally-managed-environment` error.

```bash
cd /home/pi
git clone https://github.com/Erosi11/slideshow.git slideshow
cd slideshow

# Create the virtual environment
python3 -m venv venv

# Activate it — your prompt will show (venv) when active
source venv/bin/activate

# Now install — pip operates inside the venv, not the system Python
pip install -r requirements.txt
```

> If you open a new terminal session later, always re-run
> `source venv/bin/activate` before using `pip` or running the app
> manually. The systemd service handles activation automatically via
> the full venv path in `ExecStart`.

### 3 — Systemd service

```bash
# Copy the service file
sudo cp slideshow.service /etc/systemd/system/

# Edit WorkingDirectory / User if needed
sudo nano /etc/systemd/system/slideshow.service

# Enable + start
sudo systemctl daemon-reload
sudo systemctl enable slideshow
sudo systemctl start slideshow

# Check status / logs
sudo systemctl status slideshow
journalctl -u slideshow -f
```

### 4 — Auto-launch Chromium in kiosk mode

Add the following to `/etc/xdg/lxsession/LXDE-pi/autostart` (Raspberry Pi OS desktop):

```
@chromium-browser --kiosk --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble \
  http://localhost:5000/display
```

Or, for a Wayland/framebuffer setup, use `cage` + `chromium-browser`.

---

## Usage

| URL | Purpose |
|-----|---------|
| `http://<pi-ip>:5000/` | Admin panel — upload files, reorder, set delays |
| `http://<pi-ip>:5000/display` | Fullscreen slideshow (open on the Pi monitor) |
| `http://<pi-ip>:5000/api/config` | Raw JSON config |

### Admin panel features
- **Drag-and-drop upload** of PPTX, PDF, or images
- **Per-slide delay override** (leave blank to use the global default)
- **Up / Down ordering** buttons
- **Delete** slides
- **Global delay** setting

### Display behaviour
- Crossfades between slides
- Polls `/api/config` every 30 s — picks up changes without a page reload
- Preloads the next image while the current one is showing
- Requests the browser **Wake Lock API** to prevent screen blanking
- Shows a "No slides configured" message if the list is empty

---

## config.json format

```json
{
  "global_delay": 10,
  "slides": [
    { "filename": "slide_abc123_1.jpg", "delay_override": null, "order": 1 },
    { "filename": "slide_def456_1.jpg", "delay_override": 45,   "order": 2 }
  ]
}
```

`delay_override: null` means the slide uses `global_delay`.

---

## Resilience

Writes to `config.json` use an **atomic rename**:
1. Write to `config.json.tmp`
2. `os.replace()` — a single kernel call, safe against power loss mid-write

---

## Memory optimisation

- PDF pages are converted and saved **one at a time** (the `page` object is `del`-ed immediately).
- LibreOffice is only spawned during upload, not during playback.
- The display page preloads only **one** image ahead.
- The systemd unit caps the process at `MemoryMax=400M`.
