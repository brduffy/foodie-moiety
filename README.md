# Foodie Moiety

A cross-platform recipe management app with voice control, built with Python and PySide6.

## Features

- **Recipe Management** — Create, edit, and organize recipes with ingredients, steps, and rich text directions
- **Recipe Books** — Group recipes into digital cookbooks with cover images, categories, and table of contents
- **Video Integration** — Attach cooking videos to recipes with inline playback
- **Voice Control** — Hands-free operation via wake word ("Hey Foodie") + voice commands for navigating recipes, controlling video playback, and more
- **Community** — Browse, upload, and download shared recipes
- **Grocery Lists** — Generate shopping lists from recipe ingredients
- **Import/Export** — Share recipes and books as portable zip files
- **Dark Theme** — Full dark mode UI

## Setup

### Prerequisites

- Python 3.10+
- macOS (Apple Silicon recommended) or Windows

### Install

```bash
git clone https://github.com/brduffy/foodie-moiety.git
cd foodie-moiety
python3 -m venv venv
source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### Configuration

```bash
cp config.template.json config.json
```

Edit `config.json` with your backend credentials (Cognito, API Gateway, etc.). The app runs without community features if this is left unconfigured.

### Run

```bash
python main.py
```

### Voice Control

Voice commands require the Whisper model. Download `small.en` to `models/whisper/small.en/`:

```bash
# Using faster-whisper's download utility
python -c "from faster_whisper import WhisperModel; WhisperModel('small.en', download_root='models/whisper')"
```

## Building Installers

### macOS

```bash
# Unsigned test build
bash build_release_mac.sh

# Full signed + notarized release (requires env vars — see release.sh)
bash release.sh
```

### Required Environment Variables (release builds)

| Variable | Description |
|----------|-------------|
| `FM_CODESIGN_IDENTITY` | Apple Developer ID signing identity |
| `FM_APPLE_ID` | Apple ID email for notarization |
| `FM_TEAM_ID` | Apple Developer Team ID |
| `FM_S3_BUCKET` | S3 bucket for CDN uploads |
| `FM_CDN_BASE` | CDN base URL |

## Architecture

- **UI Framework**: PySide6 (Qt6 for Python)
- **Database**: SQLite
- **Voice Pipeline**: openwakeword (wake word) → faster-whisper (STT) → regex parser → command dispatch
- **Audio**: AVAudioEngine (macOS) / QAudioSource (Windows) for mic input, QAudioSink for TTS output

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
