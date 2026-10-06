# MeetIQ

MeetIQ is a college project for hosting online meetings with participant management, attendance tracking, AI summaries, attention monitoring, and chat and speech moderation.

## Project folders

- `frontend/` contains the pages, styles, and browser scripts.
- `backend/` contains the Flask app, database models, and Python dependencies.
- `ai-services/` contains the speech, summarization, toxicity, and attention services and their required model assets.

## Run MeetIQ locally on Windows

1. Install Python 3.11 and FFmpeg. Make sure `ffmpeg` is available in PowerShell's PATH.
2. From the project folder, create the virtual environment and install the backend dependencies:

   ```powershell
   py -3.11 -m venv backend\venv
   backend\venv\Scripts\python -m pip install --upgrade pip
   backend\venv\Scripts\python -m pip install -r backend\requirements.txt
   ```

3. Create `backend/.env` and set `MONGODB_URI` to a MongoDB database you can access. Keep this file private; it contains credentials.
4. Start the app:

   ```powershell
   cd backend
   .\venv\Scripts\python app.py
   ```

5. Open <http://localhost:5000> in a browser. AI features may need Ollama or model downloads, depending on the feature.

## Share a temporary public demo link for free

MeetIQ can be shared at no hosting cost with a Cloudflare Quick Tunnel. The app still runs on your computer, so keep it awake and connected to the internet while others use the link. The link is temporary and changes when the tunnel is restarted.

1. Complete the local setup above and make sure `backend/.env` contains a working `MONGODB_URI`.
2. Install `cloudflared` using [Cloudflare's Windows installation instructions](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
3. In a new PowerShell window at the project folder, run:

   ```powershell
   .\Start-PublicDemo.ps1
   ```

   If PowerShell blocks the script, run it for this session with:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Start-PublicDemo.ps1
   ```

4. Share the `https://*.trycloudflare.com` link printed in the terminal. Press Ctrl+C in that window to stop the tunnel and app.

The helper starts the app with debug mode disabled and creates temporary session and admin-registration secrets for that run. The admin registration code is printed in the terminal. Anyone with the link can access the demo, so use test data and do not share `.env` or the registration code publicly.

This is a temporary demo link, not an always-on hosted site. The app, database, real-time calls, and AI services still depend on your computer and configured services being available. A permanent link that works while your computer is off requires a hosted server and database; this guide uses no paid hosting.
