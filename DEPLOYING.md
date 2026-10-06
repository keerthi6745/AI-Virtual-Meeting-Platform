# Share a free MeetIQ demo link

This college project can be shared at no hosting cost using a Cloudflare Quick Tunnel. MeetIQ still runs on your computer, so keep it awake and connected to the internet while someone uses the link. Quick Tunnel links are temporary and change when restarted; this is not a permanent hosted deployment.

## One-time setup

1. Make sure MeetIQ runs locally and `backend/.env` has the required database configuration (`MONGODB_URI`). Do not share or commit `.env`.
2. Install `cloudflared` using Cloudflare's official Windows instructions: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>.
3. Start the tunnel from this project folder by running `Start-PublicDemo.ps1` in PowerShell. If Windows blocks the script, use `powershell -ExecutionPolicy Bypass -File .\Start-PublicDemo.ps1` for that one run.
4. Copy the `https://*.trycloudflare.com` address shown in the terminal and share it. Press Ctrl+C to stop sharing.

The script starts the local app in production mode, with debug mode disabled. It generates temporary secret values for that run if you have not set them yourself. If you need to register an admin, use the admin registration code printed in the terminal. Participant account registration remains available through the public app.

## Limits

- Your computer must remain on, and the app and tunnel must keep running.
- The public link can be visited by anyone who receives it. Share it only for the demo, and use test data.
- Video calls, speech moderation, and summaries depend on the local machine, database connectivity, and installed AI services/models. Test those features before presenting.
- Do not expose development/debug mode or share `.env` secrets.

For a permanent URL that works while your computer is off, the app needs a hosted server and database. Free tiers are too constrained for this app's real-time and AI workloads; this setup deliberately avoids paid services.
