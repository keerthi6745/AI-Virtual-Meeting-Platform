from flask import Flask, Response, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask_mail import Mail
from flask_mail import Message
from html import escape as html_escape
from urllib.parse import urlsplit
import csv
import hmac
import io
import os
import re
import uuid
import sys
import whisper
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
from zipfile import BadZipFile
from werkzeug.utils import secure_filename
from models.user_model import (
    create_admin,
    admins_collection,
    create_participant,
    participants_collection
)

from models.meeting_model import (
    create_meeting as create_meeting_record,
    meetings_collection
)
from models.notification_model import (
    create_notification,
    notifications_collection
)
import subprocess
import time
import requests

def ensure_ollama_running():
    """Start the local Ollama server automatically if it isn't already running."""
    try:
        requests.get("http://127.0.0.1:11434", timeout=2)
        print("MeetIQ: Ollama is already running.")
        return
    except Exception:
        pass

    print("MeetIQ: Ollama not detected. Starting it now...")

    launch_options = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        launch_options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.Popen(
            ["ollama", "serve"],
            **launch_options
        )
    except (FileNotFoundError, OSError) as error:
        print("MeetIQ: 'ollama' not found on PATH. Install Ollama or start it manually.")
        return

    for attempt in range(10):
        time.sleep(1)
        try:
            requests.get("http://127.0.0.1:11434", timeout=2)
            print("MeetIQ: Ollama started successfully.")
            return
        except Exception:
            continue

    print("MeetIQ: Ollama did not respond in time. Summaries may fail until it's ready.")

ensure_ollama_running()
import sys

SUMMARIZER_PATH = Path(__file__).resolve().parent.parent / "ai-services" / "summarization"

if str(SUMMARIZER_PATH) not in sys.path:
    sys.path.append(str(SUMMARIZER_PATH))

from summarizer import generate_summary
# =========================================================
# PROJECT PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"


# =========================================================
# FLASK APPLICATION
# =========================================================

app = Flask(
    __name__,
    template_folder=str(FRONTEND_DIR / "templates"),
    static_folder=str(FRONTEND_DIR / "static"),
    static_url_path="/static"
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

IS_PRODUCTION = os.getenv("APP_ENV", "").strip().lower() == "production"
secret_key = os.getenv("SECRET_KEY")
if IS_PRODUCTION and not secret_key:
    raise RuntimeError("SECRET_KEY must be configured in production.")
app.secret_key = secret_key or "meetiq-development-secret-key"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION

socketio = SocketIO(
    app,
    cors_allowed_origins="*"
)

@app.route("/healthz")
def deployment_health_check():
    return jsonify({"status": "ok"}), 200

socket_users = {}
socket_meetings = {}   # socket id -> meeting id, used to close attendance on disconnect
@socketio.on("connect")
def handle_socket_connect():

    user_id = session.get("user_id")

    if user_id:

        socket_users[user_id] = request.sid

        print(
            "Socket connected:",
            user_id,
            request.sid
        )


@socketio.on("disconnect")
def handle_socket_disconnect():

    disconnected_socket = request.sid
    meeting_id = socket_meetings.pop(disconnected_socket, None)
    user_id = session.get("user_id")
    role = session.get("role")

    if meeting_id:
        if role == "participant" and user_id:
            finalize_attendance_session(meeting_id, user_id, datetime.utcnow())
        socketio.emit(
            "participant_left",
            {
                "participant_id": user_id,
                "participant_name": session.get("user_name", "Participant"),
                "role": role,
                "message": "The host left the meeting." if role == "admin" else "A participant left the meeting."
            },
            room=f"meeting_{meeting_id}"
        )

    if user_id and socket_users.get(user_id) == disconnected_socket:
        del socket_users[user_id]

    print("Socket disconnected:", user_id or disconnected_socket)
# =========================================================
# SECRET KEY
# =========================================================

app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True").strip().lower() in {"true", "1", "yes"}
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "False").strip().lower() in {"true", "1", "yes"}
app.config["MAIL_TIMEOUT"] = int(os.getenv("MAIL_TIMEOUT", 20))
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)


def safe_next_path(value):
    """Allow only same-site absolute paths for post-login redirects."""
    if not value or "\\" in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return value


def external_app_url(endpoint, **values):
    """Build links using the public tunnel origin when one is configured."""
    public_base = os.getenv("MEETIQ_PUBLIC_URL", "").strip().rstrip("/")
    if public_base:
        path = url_for(endpoint, **values)
        return f"{public_base}/{path.lstrip('/')}"
    return url_for(endpoint, _external=True, **values)


def send_invitation_email(recipient, subject, body, action_url, action_label):
    """Send a plain-text and clickable HTML meeting invitation."""
    sender = (
        app.config.get("MAIL_DEFAULT_SENDER")
        or app.config.get("MAIL_USERNAME")
    )
    try:
        if not sender:
            raise RuntimeError("MAIL_DEFAULT_SENDER or MAIL_USERNAME is not configured")

        message = Message(
            subject=subject,
            sender=sender,
            recipients=[recipient]
        )
        message.body = f"{body.rstrip()}\n\n{action_label}: {action_url}"
        paragraphs = "".join(
            f"<p>{html_escape(line)}</p>" for line in body.splitlines() if line.strip()
        )
        message.html = (
            '<div style="font-family:Arial,sans-serif;color:#172033;line-height:1.6">'
            f"{paragraphs}"
            f'<p><a href="{html_escape(action_url, quote=True)}" '
            'style="display:inline-block;padding:12px 18px;border-radius:8px;'
            'background:#6548ef;color:#fff;text-decoration:none;font-weight:600">'
            f"{html_escape(action_label)}</a></p></div>"
        )
        mail.send(message)
        return True
    except Exception as email_error:
        print("MeetIQ: Invitation email could not be sent:", email_error)
        return False

# =========================================================
# TEMPORARY USER STORAGE
# =========================================================

admins = []
participants = []

# Temporary meeting storage
meetings = []


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def find_admin(email):
    email = email.strip().lower()

    return admins_collection.find_one({
        "email": email
    })


def find_participant(login_value):
    login_value = login_value.strip().lower()

    return participants_collection.find_one({
        "$or": [
            {"email": login_value},
            {"user_id": login_value}
        ]
    })


def parse_participant_upload(upload):
    """Read a CSV or first-sheet XLSX participant list with name/email columns."""
    filename = (upload.filename or "").lower()
    content = upload.read()
    if filename.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    elif filename.endswith(".xlsx"):
        namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with ZipFile(io.BytesIO(content)) as workbook:
            try:
                root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
                strings = ["".join(node.itertext()) for node in root.findall("m:si", namespace)]
            except KeyError:
                strings = []
            sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            rows = []
            for row in sheet.findall(".//m:sheetData/m:row", namespace):
                values = []
                for cell in row.findall("m:c", namespace):
                    letters = re.match(r"[A-Z]+", cell.attrib.get("r", "A1")).group(0)
                    column = 0
                    for letter in letters:
                        column = column * 26 + ord(letter) - 64
                    while len(values) < column:
                        values.append("")
                    value_node = cell.find("m:v", namespace)
                    value = value_node.text if value_node is not None else ""
                    if cell.attrib.get("t") == "s" and value:
                        value = strings[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        inline = cell.find("m:is", namespace)
                        value = "".join(inline.itertext()) if inline is not None else ""
                    values[column - 1] = value
                rows.append(values)
    else:
        raise ValueError("Upload a .csv or .xlsx file.")

    if not rows:
        raise ValueError("The participant file is empty.")
    headers = [str(value).strip().lower() for value in rows[0]]
    if "name" not in headers or "email" not in headers:
        raise ValueError("The file needs columns named name and email.")
    name_index, email_index = headers.index("name"), headers.index("email")
    participants = []
    for line_number, row in enumerate(rows[1:], start=2):
        name = str(row[name_index]).strip() if len(row) > name_index else ""
        email = str(row[email_index]).strip().lower() if len(row) > email_index else ""
        if not name and not email:
            continue
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError(f"Row {line_number} must contain a name and valid email address.")
        participants.append({"name": name, "email": email})
    if not participants:
        raise ValueError("No participant rows were found in the file.")
    return participants


# =========================================================
# DATE FORMATTER
# =========================================================

def format_meeting_date(date_value):
    """
    Converts:
        2026-09-14
    into:
        14 September 2026
    """

    try:
        date_object = datetime.strptime(
            date_value,
            "%Y-%m-%d"
        )

        return date_object.strftime(
            "%d %B %Y"
        )

    except (ValueError, TypeError):
        return date_value


# =========================================================
# TIME FORMATTER
# =========================================================

def format_meeting_time(time_value):
    """
    Converts:
        14:30
    into:
        02:30 PM

    Also accepts:
        02:30 PM
        2:30 PM
    """

    if not time_value:
        return ""

    # If already in AM/PM format
    for time_format in [
        "%I:%M %p",
        "%I:%M%p"
    ]:

        try:
            time_object = datetime.strptime(
                time_value.strip(),
                time_format
            )

            return time_object.strftime(
                "%I:%M %p"
            )

        except ValueError:
            pass

    # If HTML time input sends 24-hour format
    try:

        time_object = datetime.strptime(
            time_value.strip(),
            "%H:%M"
        )

        return time_object.strftime(
            "%I:%M %p"
        )

    except ValueError:

        return time_value


# =========================================================
# ROLE SELECTION
# =========================================================

@app.route("/")
def role():

    return render_template(
        "auth/role.html"
    )


# =========================================================
# ADMIN LOGIN
# =========================================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        # Empty field validation
        if not email or not password:

            flash(
                "Please enter your email and password.",
                "error"
            )

            return redirect(
                url_for("admin_login")
            )

        # Find registered admin
        admin = find_admin(email)

        # Check account + password
        if admin and check_password_hash(
            admin["password"],
            password
        ):

            session["user_id"] = admin["email"]
            session["user_name"] = admin["name"]
            session["role"] = "admin"

            flash(
                "Welcome back, " + admin["name"] + "!",
                "success"
            )

            return redirect(
                url_for("admin_dashboard")
            )

        # Invalid credentials
        flash(
            "Invalid email or password.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    return render_template(
        "auth/admin_login.html"
    )


# =========================================================
# ADMIN REGISTER
# =========================================================

@app.route(
    "/admin/register",
    methods=["GET", "POST"]
)
def admin_register():

    if request.method == "POST":

        if IS_PRODUCTION:
            registration_key = os.getenv("ADMIN_REGISTRATION_KEY", "")
            supplied_key = request.form.get("registration_key", "")
            if not registration_key or not hmac.compare_digest(supplied_key, registration_key):
                flash("A valid administrator registration code is required.", "error")
                return redirect(url_for("admin_register"))

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        # Required fields
        if not name or not email or not password or not confirm_password:

            flash(
                "Please fill in all fields.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Password confirmation
        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Minimum password length
        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Check duplicate email
        if find_admin(email):

            flash(
                "An admin account with this email already exists.",
                "error"
            )

            return redirect(
                url_for("admin_register")
            )

        # Create account
        new_admin = create_admin(
    name,
    email,
    password
)

        flash(
            "Admin account created successfully. Please sign in.",
            "success"
        )

        return redirect(
            url_for("admin_login")
        )

    return render_template(
        "auth/admin_register.html",
        admin_registration_required=IS_PRODUCTION
    )


# =========================================================
# PARTICIPANT LOGIN
# =========================================================

@app.route(
    "/participant/login",
    methods=["GET", "POST"]
)
def participant_login():

    next_url = safe_next_path(request.values.get("next", ""))

    if request.method == "POST":

        login_value = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        # Empty field validation
        if not login_value or not password:

            flash(
                "Please enter your email/ID and password.",
                "error"
            )

            return redirect(
                url_for("participant_login", next=next_url)
            )

        # Find participant
        participant = find_participant(
            login_value
        )

        # Check password
        if participant and check_password_hash(
            participant["password"],
            password
        ):

            session["user_id"] = participant["user_id"]

            session["user_name"] = participant["name"]

            session["role"] = "participant"

            flash(
                "Welcome back, " + participant["name"] + "!",
                "success"
            )

            return redirect(next_url or url_for("participant_dashboard"))

        # Invalid credentials
        flash(
            "Invalid email/ID or password.",
            "error"
        )

        return redirect(
            url_for("participant_login", next=next_url)
        )

    return render_template(
        "auth/participant_login.html",
        next_url=next_url
    )


# =========================================================
# PARTICIPANT REGISTER
# =========================================================

@app.route(
    "/participant/register",
    methods=["GET", "POST"]
)
def participant_register():

    next_url = safe_next_path(request.values.get("next", ""))
    invite_email = request.values.get("email", "").strip().lower()

    def registration_url():
        return url_for(
            "participant_register",
            next=next_url,
            email=invite_email or None
        )

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        department = request.form.get(
            "department",
            ""
        ).strip()

        year = request.form.get(
            "year",
            ""
        ).strip()

        user_id = request.form.get(
            "user_id",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        # Required fields
        if not all([
            name,
            email,
            department,
            year,
            user_id,
            password,
            confirm_password
        ]):

            flash(
                "Please fill in all fields.",
                "error"
            )

            return redirect(
                registration_url()
            )

        # Password confirmation
        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return redirect(
                registration_url()
            )

        # Minimum password length
        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                registration_url()
            )

               # Check duplicate email
        if participants_collection.find_one({
            "email": email
        }):

            flash(
                "An account with this email already exists.",
                "error"
            )

            return redirect(
                registration_url()
            )

        # Check duplicate Student/Employee ID
        if participants_collection.find_one({
            "user_id": user_id
        }):

            flash(
                "This Student/Employee ID is already registered.",
                "error"
            )

            return redirect(
                registration_url()
            )

        # Create participant
                
        new_participant = create_participant(
            name,
            email,
            department,
            year,
            user_id,
            password
        )

        pending_meetings = list(meetings_collection.find({
            "pending_invites.email": email
        }))
        for pending_meeting in pending_meetings:
            meetings_collection.update_one(
                {"_id": pending_meeting["_id"], "pending_invites.email": email},
                {
                    "$addToSet": {"invited_participants": user_id},
                    "$pull": {"pending_invites": {"email": email}}
                }
            )
            create_notification(
                participant_id=user_id,
                participant_email=email,
                meeting_id=str(pending_meeting["_id"]),
                meeting_title=pending_meeting.get("title", "Meeting"),
                meeting_date=pending_meeting.get("formatted_date", pending_meeting.get("date", "")),
                meeting_time=pending_meeting.get("formatted_time", pending_meeting.get("time", "")),
                message=f"You have been invited to {pending_meeting.get('title', 'a meeting')}."
            )

       

        flash(
            "Participant account created successfully. Please sign in.",
            "success"
        )

        return redirect(
            url_for("participant_login", next=next_url)
        )

    return render_template(
        "auth/participant_register.html",
        next_url=next_url,
        invite_email=invite_email
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================
def summary_has_content(ai_summary):
    """Return True only if the AI summary has actual generated content."""
    if not ai_summary or not isinstance(ai_summary, dict):
        return False

    return bool(
        ai_summary.get("discussion_points")
        or ai_summary.get("decisions")
        or ai_summary.get("action_items")
    )

@app.route("/admin/dashboard")
def admin_dashboard():

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    admin_email = session.get("user_id")

    admin_meetings_list = list(
        meetings_collection.find({
            "created_by_email": admin_email
        }).sort("date", -1)
    )
    total_meetings = len(admin_meetings_list)

    upcoming_meetings = sum(
        1 for meeting in admin_meetings_list
        if meeting.get("status") == "upcoming"
    )

    ai_insights = sum(
        1 for meeting in admin_meetings_list
        if summary_has_content(meeting.get("ai_summary"))
    )

    live_meetings = [
        meeting for meeting in admin_meetings_list
        if meeting.get("status") == "live"
    ]
    upcoming_meetings_list = [
        meeting for meeting in admin_meetings_list
        if meeting.get("status", "upcoming") == "upcoming"
    ]

    return render_template(
        "admin/dashboard.html",
        total_meetings=total_meetings,
        upcoming_meetings=upcoming_meetings,
        ai_insights=ai_insights,
        live_meetings=live_meetings,
        upcoming_meetings_list=upcoming_meetings_list
    )


@app.route("/admin/profile", methods=["GET", "POST"])
def admin_profile():
    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    email = session.get("user_id", "").strip().lower()
    admin = admins_collection.find_one({"email": email})
    if not admin:
        session.clear()
        flash("Your account could not be found. Please sign in again.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name:
            flash("Please enter your name.", "error")
            return redirect(url_for("admin_profile"))
        if new_password:
            if not current_password or not check_password_hash(admin["password"], current_password):
                flash("Enter your current password to change it.", "error")
                return redirect(url_for("admin_profile"))
            if len(new_password) < 6:
                flash("Your new password must contain at least 6 characters.", "error")
                return redirect(url_for("admin_profile"))
            if new_password != confirm_password:
                flash("The new passwords do not match.", "error")
                return redirect(url_for("admin_profile"))

        updates = {"name": name}
        if new_password:
            updates["password"] = generate_password_hash(new_password)
        admins_collection.update_one({"_id": admin["_id"]}, {"$set": updates})
        session["user_name"] = name
        flash("Your profile has been updated.", "success")
        return redirect(url_for("admin_profile"))

    return render_template("admin/profile.html", profile=admin)


@app.route("/participant/profile", methods=["GET", "POST"])
def participant_profile():
    if session.get("role") != "participant":
        flash("Please sign in to view your profile.", "error")
        return redirect(url_for("participant_login"))

    participant_id = session.get("user_id", "")
    participant = participants_collection.find_one({"user_id": participant_id})
    if not participant:
        session.clear()
        flash("Your account could not be found. Please sign in again.", "error")
        return redirect(url_for("participant_login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        department = request.form.get("department", "").strip()
        year = request.form.get("year", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name:
            flash("Please enter your name.", "error")
            return redirect(url_for("participant_profile"))
        if new_password:
            if not current_password or not check_password_hash(participant["password"], current_password):
                flash("Enter your current password to change it.", "error")
                return redirect(url_for("participant_profile"))
            if len(new_password) < 6:
                flash("Your new password must contain at least 6 characters.", "error")
                return redirect(url_for("participant_profile"))
            if new_password != confirm_password:
                flash("The new passwords do not match.", "error")
                return redirect(url_for("participant_profile"))

        updates = {"name": name, "department": department, "year": year}
        if new_password:
            updates["password"] = generate_password_hash(new_password)
        participants_collection.update_one({"_id": participant["_id"]}, {"$set": updates})
        session["user_name"] = name
        flash("Your profile has been updated.", "success")
        return redirect(url_for("participant_profile"))

    return render_template("participants/profile.html", profile=participant)


@app.route("/admin/insights")
def admin_ai_insights():
    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    meetings = list(meetings_collection.find({
        "created_by_email": session.get("user_id")
    }).sort("date", -1))

    insight_meetings = []
    summary_count = 0
    def summary_items(summary, key):
        values = summary.get(key, []) if isinstance(summary, dict) else []
        if isinstance(values, str):
            values = [values] if values.strip() else []
        return [str(value) for value in values if value]

    for meeting in meetings:
        summary = meeting.get("ai_summary") or {}
        discussions = summary_items(summary, "discussion_points")
        decisions = summary_items(summary, "decisions")
        actions = summary_items(summary, "action_items")
        moderation_events = meeting.get("toxicity_events") or []
        attention_alerts = [
            alert for alert in (meeting.get("attention_alerts") or [])
            if isinstance(alert, dict)
        ]
        if summary_has_content(summary):
            summary_count += 1
        insight_meetings.append({
            "meeting": meeting,
            "summary_available": summary_has_content(summary),
            "discussion_points": discussions,
            "decisions": decisions,
            "action_items": actions,
            "attention_alerts": attention_alerts,
            "attention_count": len(attention_alerts),
            "moderation_count": len(moderation_events)
        })

    return render_template(
        "admin/ai_insights.html",
        insight_meetings=insight_meetings,
        total_meetings=len(meetings),
        summary_count=summary_count
    )

# =========================================================
# ADMIN MEETINGS
# =========================================================

@app.route("/admin/meetings")
def admin_meetings():

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    # -----------------------------------------------------
    # GET ALL MEETINGS FROM MONGODB
    # -----------------------------------------------------

    admin_email = session.get("user_id")

    meetings = list(
        meetings_collection.find({
            "created_by_email": admin_email
        }).sort(
            "date",
            -1
        )
    )

    # -----------------------------------------------------
    # MEETING STATISTICS
    # -----------------------------------------------------

    total_meetings = len(meetings)

    upcoming_meetings = 0
    live_meetings = 0
    completed_meetings = 0
    ai_insights = 0

    for meeting in meetings:

        # Count upcoming meetings
        if meeting.get("status") == "upcoming":

            upcoming_meetings += 1
        elif meeting.get("status") == "live":
            live_meetings += 1
        elif meeting.get("status") == "completed":
            completed_meetings += 1

        # Count meetings that have AI insights
        if meeting.get("ai_summary"):

            ai_insights += 1

    # -----------------------------------------------------
    # SEND DATA TO MEETINGS PAGE
    # -----------------------------------------------------

    return render_template(
        "admin/meetings.html",

        meetings=meetings,

        total_meetings=total_meetings,

        upcoming_meetings=upcoming_meetings,

        live_meetings=live_meetings,

        completed_meetings=completed_meetings,

        ai_insights=ai_insights
    )
# =========================================================
# MEETING OWNERSHIP HELPER
# =========================================================

def get_admin_meeting(meeting_id):
    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:
        return meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })
    except Exception:
        return None


# =========================================================
# ADMIN MEETING DETAILS
# =========================================================

@app.route("/admin/meetings/<meeting_id>")
def meeting_details(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "You are not authorized to access this meeting.",
            "error"
        )
        return redirect(url_for("admin_meetings"))

    invited_ids = meeting.get(
        "invited_participants",
        []
    )

    invited_participants = list(
        participants_collection.find({
            "user_id": {
                "$in": invited_ids
            }
        }).sort(
            "name",
            1
        )
    )

    return render_template(
        "admin/meeting_details.html",
        meeting=meeting,
        invited_participants=invited_participants
    )
# ============================================================
# START MEETING
# ============================================================

@app.route("/admin/meetings/<meeting_id>/start", methods=["POST"])
def start_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:

        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })

    except Exception:

        meeting = None

    if not meeting:

        flash(
            "Meeting not found or you are not authorized to manage it.",
            "error"
        )

        return redirect(
            url_for("admin_meetings")
        )

    # Only upcoming meetings can be started
    if meeting.get("status") != "upcoming":

        flash(
            "This meeting cannot be started.",
            "error"
        )

        return redirect(
            url_for(
                "meeting_details",
                meeting_id=meeting_id
            )
        )

    meetings_collection.update_one(
        {
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        },
        {
            "$set": {
                "status": "live"
            }
        }
    )

    flash(
        "Meeting started successfully.",
        "success"
    )

    return redirect(
        url_for(
            "meeting_details",
            meeting_id=meeting_id
        )
    )

@app.route("/admin/meetings/<meeting_id>/host")
def host_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "Meeting not found or you are not authorized to access it.",
            "error"
        )
        return redirect(url_for("admin_meetings"))

    if meeting.get("status") != "live":
        flash(
            "The meeting must be started before entering the host room.",
            "error"
        )
        return redirect(
            url_for(
                "meeting_details",
                meeting_id=meeting_id
            )
        )

    return render_template(
        "admin/host_meeting.html",
        meeting=meeting,
        session_user_name=session.get("user_name")
    )
# ============================================================
# END MEETING
# ============================================================

@app.route("/admin/meetings/<meeting_id>/end", methods=["POST"])
def end_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    admin_email = session.get("user_id")

    try:

        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        })

    except Exception:

        meeting = None

    if not meeting:

        flash(
            "Meeting not found or you are not authorized to manage it.",
            "error"
        )

        return redirect(
            url_for("admin_meetings")
        )

    # Only live meetings can be ended
    if meeting.get("status") != "live":

        flash(
            "This meeting is not currently live.",
            "error"
        )

        return redirect(
            url_for(
                "meeting_details",
                meeting_id=meeting_id
            )
        )

    meeting_ended_at = datetime.utcnow()

    meetings_collection.update_one(
        {
            "_id": ObjectId(meeting_id),
            "created_by_email": admin_email
        },
        {
            "$set": {
                "status": "completed",
                "ended_at": meeting_ended_at
            }
        }
    )

    # Finalize everyone who is still inside the meeting.
    finalize_meeting_attendance(
        meeting_id,
        meeting_ended_at
    )

    socketio.emit(
        "meeting_ended",
        {"message": "The host ended this meeting."},
        room=f"meeting_{meeting_id}"
    )

    # Run the AI in the background so "End Meeting" doesn't freeze.
    socketio.start_background_task(generate_meeting_ai_summary, meeting_id)

    flash(
        "Meeting ended successfully. The AI summary is being generated "
        "and will appear shortly.",
        "success"
    )

    return redirect(
        url_for(
            "meeting_details",
            meeting_id=meeting_id
        )
    )
# =========================================================
# EDIT MEETING
# =========================================================

@app.route("/admin/meetings/<meeting_id>/edit", methods=["GET", "POST"])
def edit_meeting(meeting_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    meeting = get_admin_meeting(meeting_id)

    if not meeting:
        flash("Meeting not found or you are not authorized to edit it.", "error")
        return redirect(url_for("admin_meetings"))

    if request.method == "GET":
        return render_template("admin/edit_meeting.html", meeting=meeting)

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    date = request.form.get("date", "").strip()
    time = request.form.get("time", "").strip()
    duration = request.form.get("duration", "").strip()
    meeting_type = request.form.get("meeting_type", "").strip()
    access = request.form.get("access", "").strip()

    if not title or not date or not time:
        flash("Title, date and time are required.", "error")
        return redirect(url_for("edit_meeting", meeting_id=meeting_id))

    meetings_collection.update_one(
        {
            "_id": meeting["_id"],
            "created_by_email": session.get("user_id")
        },
        {
            "$set": {
                "title": title,
                "description": description,
                "date": date,
                "formatted_date": format_meeting_date(date),
                "time": time,
                "formatted_time": format_meeting_time(time),
                "duration": duration,
                "meeting_type": meeting_type,
                "access": access
            }
        }
    )

    flash("Meeting updated successfully!", "success")
    return redirect(url_for("meeting_details", meeting_id=meeting_id))


# =========================================================
# DELETE MEETING
# =========================================================

@app.route("/admin/meetings/<meeting_id>/delete", methods=["POST"])
def delete_meeting(meeting_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    try:
        result = meetings_collection.delete_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": session.get("user_id")
        })
    except Exception:
        result = None

    if not result or result.deleted_count == 0:
        flash("Meeting not found or you are not authorized to delete it.", "error")
    else:
        flash("Meeting deleted successfully!", "success")

    return redirect(url_for("admin_meetings"))


# =========================================================
# REMOVE INVITATION
# =========================================================

@app.route("/admin/meetings/<meeting_id>/remove-invitation/<participant_id>", methods=["POST"])
def remove_invitation(meeting_id, participant_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    try:
        result = meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id),
                "created_by_email": session.get("user_id")
            },
            {
                "$pull": {
                    "invited_participants": participant_id
                }
            }
        )
    except Exception:
        result = None

    if not result or result.modified_count == 0:
        flash("Invitation not found or you are not authorized.", "error")
    else:
        flash("Invitation removed successfully!", "success")

    return redirect(url_for("meeting_details", meeting_id=meeting_id))


@app.route("/admin/meetings/<meeting_id>/remove-pending-invitation/<email>", methods=["POST"])
def remove_pending_invitation(meeting_id, email):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    normalized_email = email.strip().lower()
    try:
        result = meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id),
                "created_by_email": session.get("user_id"),
                "pending_invites.email": normalized_email
            },
            {
                "$pull": {
                    "pending_invites": {"email": normalized_email}
                }
            }
        )
    except Exception:
        result = None

    if not result or result.modified_count == 0:
        flash("Pending invitation not found or you are not authorized.", "error")
    else:
        flash("Pending invitation removed successfully!", "success")

    return redirect(url_for("meeting_details", meeting_id=meeting_id))


# =========================================================
# INVITE PARTICIPANTS
# =========================================================

@app.route("/admin/meetings/<meeting_id>/invite", methods=["GET", "POST"])
def invite_participants(meeting_id):

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    meeting = get_admin_meeting(meeting_id)

    if not meeting:
        flash("Meeting not found or you are not authorized to invite participants.", "error")
        return redirect(url_for("admin_meetings"))

    if request.method == "GET":
        return render_template(
            "admin/invite_participants.html",
            meeting=meeting
        )

    invitees = []
    try:
        if request.form.get("invite_method", "manual") == "upload":
            upload = request.files.get("participant_file")
            if not upload or not upload.filename:
                raise ValueError("Choose a CSV or Excel participant list.")
            invitees = parse_participant_upload(upload)
        else:
            names = request.form.getlist("invite_name")
            emails = request.form.getlist("invite_email")
            for name, email in zip(names, emails):
                name, email = name.strip(), email.strip().lower()
                if not name and not email:
                    continue
                if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    raise ValueError("Each participant needs a name and valid email address.")
                invitees.append({"name": name, "email": email})
        if len(invitees) > 500:
            raise ValueError("Add no more than 500 participants at a time.")
    except Exception as error:
        flash(str(error), "error")
        return redirect(url_for("invite_participants", meeting_id=meeting_id))

    if not invitees:
        flash("Add at least one participant before sending invitations.", "error")
        return redirect(url_for("invite_participants", meeting_id=meeting_id))

    existing_ids = meeting.get("invited_participants", [])
    invited_ids = list(existing_ids)
    pending_invites = list(meeting.get("pending_invites", []))
    pending_by_email = {item.get("email", "").lower(): item for item in pending_invites}
    seen_emails = set()
    newly_invited = []
    newly_pending = []
    pending_to_email = []
    for invitee in invitees:
        email = invitee["email"]
        if email in seen_emails or email == (session.get("user_id") or "").lower():
            continue
        seen_emails.add(email)
        participant = participants_collection.find_one({"email": email})
        if participant and participant.get("user_id"):
            participant_id = participant["user_id"]
            if participant_id not in invited_ids:
                invited_ids.append(participant_id)
                newly_invited.append(participant)
            pending_by_email.pop(email, None)
        elif email not in pending_by_email:
            pending_item = {"name": invitee["name"], "email": email}
            pending_by_email[email] = pending_item
            newly_pending.append(pending_item)
            pending_to_email.append(pending_item)
        else:
            pending_to_email.append(pending_by_email[email])

    pending_invites = list(pending_by_email.values())
    meetings_collection.update_one(
        {"_id": meeting["_id"], "created_by_email": session.get("user_id")},
        {"$set": {"invited_participants": invited_ids, "pending_invites": pending_invites}}
    )

    meeting_id_url = str(meeting["_id"])
    meeting_path = url_for("participant_meeting", meeting_id=meeting_id_url)
    meeting_link = external_app_url("participant_meeting", meeting_id=meeting_id_url)
    emails_sent = 0
    emails_failed = 0
    for participant in newly_invited:
        participant_email = participant.get("email")
        create_notification(
            participant_id=participant.get("user_id"),
            participant_email=participant_email,
            meeting_id=meeting_id_url,
            meeting_title=meeting.get("title", "Meeting"),
            meeting_date=meeting.get("formatted_date", meeting.get("date", "")),
            meeting_time=meeting.get("formatted_time", meeting.get("time", "")),
            message=f"You have been invited to {meeting.get('title', 'a meeting')}."
        )
        if participant_email:
            body = f"Hello {participant.get('name', 'Participant')},\n\nYou have been invited to {meeting.get('title', 'a meeting')}.\nDate: {meeting.get('formatted_date', meeting.get('date', ''))}\nTime: {meeting.get('formatted_time', meeting.get('time', ''))}\n\nSign in to your MeetIQ account to view the meeting."
            if send_invitation_email(
                participant_email,
                f"Meeting Invitation - {meeting.get('title', 'MeetIQ Meeting')}",
                body,
                meeting_link,
                "View meeting"
            ):
                emails_sent += 1
            else:
                emails_failed += 1

    for pending in pending_to_email:
        registration_link = external_app_url(
            "participant_register",
            email=pending["email"],
            next=meeting_path
        )
        body = f"Hello {pending['name']},\n\nYou have been invited to {meeting.get('title', 'a meeting')}.\nDate: {meeting.get('formatted_date', meeting.get('date', ''))}\nTime: {meeting.get('formatted_time', meeting.get('time', ''))}\n\nRegister with this email address to access the meeting."
        if send_invitation_email(
            pending["email"],
            f"Meeting Invitation - {meeting.get('title', 'MeetIQ Meeting')}",
            body,
            registration_link,
            "Register and view meeting"
        ):
            emails_sent += 1
        else:
            emails_failed += 1

    flash(
        f"Added {len(newly_invited) + len(newly_pending)} participant invitation(s); "
        f"sent {emails_sent} email(s).",
        "success"
    )
    if emails_failed:
        flash(f"{emails_sent} invitation email(s) sent; {emails_failed} could not be sent. Check the mail settings in backend/.env.", "error")
    return redirect(url_for("meeting_details", meeting_id=meeting_id_url))


# =========================================================
# CREATE MEETING
# =========================================================

@app.route("/admin/meetings/create", methods=["GET", "POST"])
def create_meeting():

    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "GET":
        return render_template("admin/create_meeting.html")

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    date = request.form.get("date", "").strip()
    time = request.form.get("time", "").strip()
    duration = request.form.get("duration", "60").strip()
    meeting_type = request.form.get("meeting_type", "team").strip()
    access = "selected"

    invitees = []
    invite_method = request.form.get("invite_method", "manual")
    try:
        if invite_method == "upload":
            upload = request.files.get("participant_file")
            if not upload or not upload.filename:
                raise ValueError("Choose a CSV or Excel participant list.")
            invitees = parse_participant_upload(upload)
        else:
            names = request.form.getlist("invite_name")
            emails = request.form.getlist("invite_email")
            for name, email in zip(names, emails):
                name, email = name.strip(), email.strip().lower()
                if not name and not email:
                    continue
                if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    raise ValueError("Each participant needs a name and valid email address.")
                invitees.append({"name": name, "email": email})
        if len(invitees) > 500:
            raise ValueError("Add no more than 500 participants at a time.")
    except (ValueError, UnicodeDecodeError, OSError, KeyError, IndexError, BadZipFile, ElementTree.ParseError) as error:
        flash(str(error), "error")
        return redirect(url_for("create_meeting"))

    if not title or not date or not time:
        flash("Please fill in the meeting title, date and time.", "error")
        return redirect(url_for("create_meeting"))

    admin_email = session.get("user_id")
    admin_name = session.get("user_name", "Administrator")

    meeting = create_meeting_record(
        title=title,
        description=description,
        date=date,
        formatted_date=format_meeting_date(date),
        time=time,
        formatted_time=format_meeting_time(time),
        duration=duration,
        meeting_type=meeting_type,
        access=access,
        created_by=admin_name,
        created_by_email=admin_email
    )

    invited_ids = []
    pending_invites = []
    known_invitees = []
    seen_emails = set()
    for invitee in invitees:
        email = invitee["email"]
        if email in seen_emails or email == (admin_email or "").lower():
            continue
        seen_emails.add(email)
        participant = participants_collection.find_one({"email": email})
        if participant:
            participant_id = participant.get("user_id")
            if participant_id:
                invited_ids.append(participant_id)
                known_invitees.append(participant)
        else:
            pending_invites.append({"name": invitee["name"], "email": email})

    if invited_ids or pending_invites:
        meetings_collection.update_one(
            {"_id": meeting["_id"]},
            {"$set": {
                "invited_participants": list(dict.fromkeys(invited_ids)),
                "pending_invites": pending_invites
            }}
        )

    meeting_id = str(meeting["_id"])
    meeting_path = url_for("participant_meeting", meeting_id=meeting_id)
    meeting_link = external_app_url("participant_meeting", meeting_id=meeting_id)
    emails_sent = 0
    emails_failed = 0
    for participant in known_invitees:
        participant_email = participant.get("email")
        create_notification(
            participant_id=participant.get("user_id"),
            participant_email=participant_email,
            meeting_id=meeting_id,
            meeting_title=title,
            meeting_date=meeting.get("formatted_date", date),
            meeting_time=meeting.get("formatted_time", time),
            message=f"You have been invited to {title}."
        )
        if participant_email:
            body = f"Hello {participant.get('name', 'Participant')},\n\nYou have been invited to {title}.\nDate: {meeting.get('formatted_date', date)}\nTime: {meeting.get('formatted_time', time)}\n\nSign in to your MeetIQ account to view the meeting."
            if send_invitation_email(
                participant_email,
                f"Meeting Invitation - {title}",
                body,
                meeting_link,
                "View meeting"
            ):
                emails_sent += 1
            else:
                emails_failed += 1

    for pending in pending_invites:
        registration_link = external_app_url(
            "participant_register",
            email=pending["email"],
            next=meeting_path
        )
        body = f"Hello {pending['name']},\n\nYou have been invited to {title}.\nDate: {meeting.get('formatted_date', date)}\nTime: {meeting.get('formatted_time', time)}\n\nCreate a MeetIQ participant account with this email address to access the meeting."
        if send_invitation_email(
            pending["email"],
            f"Meeting Invitation - {title}",
            body,
            registration_link,
            "Register and view meeting"
        ):
            emails_sent += 1
        else:
            emails_failed += 1

    invite_message = f" {len(invited_ids) + len(pending_invites)} participant invitation(s) added." if invitees else ""
    flash(
        f"Meeting created successfully!{invite_message} Sent {emails_sent} invitation email(s).",
        "success"
    )
    if emails_failed:
        flash(f"{emails_sent} invitation email(s) sent; {emails_failed} could not be sent. Check the mail settings in backend/.env.", "error")
    return redirect(url_for("admin_meetings"))

# =========================================================
# ADMIN PARTICIPANTS
# =========================================================

# =========================================================
# ADMIN ATTENDANCE
# =========================================================

INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


def to_india_time(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(INDIA_TIMEZONE)

@app.route("/admin/attendance")
def admin_attendance():

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(
            url_for("admin_login")
        )

    meetings = list(
        meetings_collection.find({
            "created_by_email": session.get("user_id")
        }).sort([
            ("date", -1),
            ("time", -1)
        ])
    )

    attendance_meetings = []

    for meeting in meetings:

        attendance_records = meeting.get(
            "attendance",
            []
        )

        participants = []

        for participant_id in meeting.get(
            "invited_participants",
            []
        ):

            participant = participants_collection.find_one({
                "user_id": participant_id
            })

            participant_name = (
                participant.get("name", "Participant")
                if participant
                else participant_id
            )

            participant_email = (
                participant.get("email", "")
                if participant
                else ""
            )

            sessions = [
                record
                for record in attendance_records
                if record.get("participant_id") == participant_id
            ]

            total_seconds = sum(
                int(record.get("duration_seconds", 0) or 0)
                for record in sessions
            )

            join_times = [
                to_india_time(record.get("join_time"))
                for record in sessions
                if record.get("join_time")
            ]

            leave_times = [
                to_india_time(record.get("leave_time"))
                for record in sessions
                if record.get("leave_time")
            ]

            participants.append({
                "participant_id": participant_id,
                "participant_name": participant_name,
                "participant_email": participant_email,
                "attended": participant_attended_meeting(meeting, participant_id),
                "pending": meeting.get("status") != "completed",
                "sessions": sessions,
                "session_count": len(sessions),
                "total_seconds": total_seconds,
                "first_join": min(join_times) if join_times else None,
                "last_leave": max(leave_times) if leave_times else None
            })

        attended_count = sum(
            1
            for participant in participants
            if participant["attended"]
        )

        attendance_meetings.append({
            "meeting": meeting,
            "participants": participants,
            "invited_count": len(participants),
            "attended_count": attended_count
        })

    return render_template(
        "admin/attendance.html",
        attendance_meetings=attendance_meetings
    )


@app.route("/admin/meetings/<meeting_id>/attendance.csv")
def download_admin_meeting_attendance(meeting_id):
    if session.get("role") != "admin":
        flash("Please sign in as an administrator.", "error")
        return redirect(url_for("admin_login"))

    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": session.get("user_id")
        })
    except Exception:
        meeting = None

    if not meeting:
        return "Meeting not found.", 404

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "Meeting", "Date", "Participant", "Email", "Participant ID",
        "Attendance Status", "Total Attendance Time (minutes)",
        "Sessions", "First Join (IST)", "Last Leave (IST)"
    ])

    def spreadsheet_safe(value):
        value = "" if value is None else str(value)
        if value.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value

    attendance = meeting.get("attendance") or []
    for participant_id in meeting.get("invited_participants") or []:
        participant = participants_collection.find_one({"user_id": participant_id}) or {}
        sessions = [
            record for record in attendance
            if record.get("participant_id") == participant_id
        ]
        total_seconds = sum(
            max(0, int(record.get("duration_seconds", 0) or 0))
            for record in sessions
        )
        if meeting.get("status") != "completed":
            attendance_status = "Pending"
        else:
            attendance_status = (
                "Attended"
                if participant_attended_meeting(meeting, participant_id)
                else "Not Attended"
            )
        join_times = [
            to_india_time(record.get("join_time"))
            for record in sessions if record.get("join_time")
        ]
        leave_times = [
            to_india_time(record.get("leave_time"))
            for record in sessions if record.get("leave_time")
        ]
        writer.writerow([
            spreadsheet_safe(meeting.get("title", "Meeting")),
            spreadsheet_safe(meeting.get("formatted_date", meeting.get("date", ""))),
            spreadsheet_safe(participant.get("name", "Participant")),
            spreadsheet_safe(participant.get("email", "")),
            spreadsheet_safe(participant_id),
            attendance_status,
            round(total_seconds / 60, 2),
            len(sessions),
            min(join_times).strftime("%d %b %Y, %I:%M:%S %p IST") if join_times else "",
            max(leave_times).strftime("%d %b %Y, %I:%M:%S %p IST") if leave_times else ""
        ])

    filename = secure_filename(meeting.get("title", "meeting")) or "meeting"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}-attendance.csv"'}
    )


@app.route("/admin/participants")
def admin_participants():

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    # Show only participants connected to meetings owned by this admin.
    owned_meetings = meetings_collection.find(
        {"created_by_email": session.get("user_id")},
        {"invited_participants": 1}
    )
    related_ids = list(dict.fromkeys(
        participant_id
        for meeting in owned_meetings
        for participant_id in meeting.get("invited_participants", [])
    ))
    participants = list(participants_collection.find({
        "user_id": {"$in": related_ids}
    }).sort("name", 1)) if related_ids else []

    # -----------------------------------------------------
    # GET UNIQUE DEPARTMENTS
    # -----------------------------------------------------

    departments = sorted(
        set(
            participant.get(
                "department",
                ""
            )
            for participant in participants
            if participant.get("department")
        )
    )

    # -----------------------------------------------------
    # SEND DATA TO PARTICIPANTS PAGE
    # -----------------------------------------------------

    return render_template(
        "admin/participants.html",
        participants=participants,
        departments=departments
    )

# =========================================================
# ATTENDANCE HELPERS
# =========================================================

def parse_duration_minutes(duration_value):
    """
    Convert common meeting-duration formats to minutes.
    Examples: 60, "60", "60 minutes", "1 hour", "1.5 hours".
    """
    if duration_value is None:
        return 0.0

    try:
        if isinstance(duration_value, (int, float)):
            return float(duration_value)
    except Exception:
        pass

    text = str(duration_value).strip().lower()

    if not text:
        return 0.0

    number_match = re.search(r"(\d+(?:\.\d+)?)", text)

    if not number_match:
        return 0.0

    value = float(number_match.group(1))

    if "hour" in text or "hr" in text:
        return value * 60.0

    return value


def participant_attended_meeting(meeting, participant_id):
    """A participant is attended only after completion and at least 50% presence."""
    if meeting.get("status") != "completed":
        return False

    duration_seconds = sum(
        max(0, int(record.get("duration_seconds", 0) or 0))
        for record in meeting.get("attendance", [])
        if record.get("participant_id") == participant_id
    )
    scheduled_seconds = parse_duration_minutes(meeting.get("duration")) * 60

    if scheduled_seconds <= 0:
        return duration_seconds > 0

    return duration_seconds >= scheduled_seconds / 2


def finalize_attendance_session(meeting_id, participant_id, end_time=None):
    """
    Close the latest open attendance session for a participant.
    Returns True when an open session was finalized.
    """
    from bson.objectid import ObjectId

    if end_time is None:
        end_time = datetime.utcnow()

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return False

    meeting = meetings_collection.find_one({
        "_id": meeting_object_id
    })

    if not meeting:
        return False

    attendance = meeting.get("attendance", [])

    open_indexes = [
        index
        for index, record in enumerate(attendance)
        if (
            record.get("participant_id") == participant_id
            and not record.get("leave_time")
        )
    ]

    if not open_indexes:
        return False

    target_index = open_indexes[-1]
    target = attendance[target_index]

    join_time = target.get("join_time")

    if not isinstance(join_time, datetime):
        join_time = end_time

    duration_seconds = max(
        0,
        int((end_time - join_time).total_seconds())
    )

    meetings_collection.update_one(
        {"_id": meeting_object_id},
        {
            "$set": {
                f"attendance.{target_index}.leave_time": end_time,
                f"attendance.{target_index}.duration_seconds": duration_seconds,
                f"attendance.{target_index}.duration_minutes": round(
                    duration_seconds / 60.0,
                    2
                ),
                f"attendance.{target_index}.status": "completed"
            }
        }
    )

    return True


def finalize_meeting_attendance(meeting_id, end_time=None):
    """
    Close every open attendance session when the host ends a meeting.
    """
    from bson.objectid import ObjectId

    if end_time is None:
        end_time = datetime.utcnow()

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return 0

    meeting = meetings_collection.find_one({
        "_id": meeting_object_id
    })

    if not meeting:
        return 0

    attendance = meeting.get("attendance", [])

    if not attendance:
        return 0

    changed = False

    for record in attendance:
        if record.get("leave_time"):
            continue

        join_time = record.get("join_time")

        if not isinstance(join_time, datetime):
            join_time = end_time

        duration_seconds = max(
            0,
            int((end_time - join_time).total_seconds())
        )

        record["leave_time"] = end_time
        record["duration_seconds"] = duration_seconds
        record["duration_minutes"] = round(
            duration_seconds / 60.0,
            2
        )
        record["status"] = "completed"
        changed = True

    if changed:
        meetings_collection.update_one(
            {"_id": meeting_object_id},
            {"$set": {"attendance": attendance}}
        )

    return sum(
        1
        for record in attendance
        if record.get("status") == "completed"
    )


def get_participant_attendance_percentage(meetings, participant_id):
    """
    Attendance is counted only after completion and requires at least half
    of the scheduled meeting duration.
    """

    eligible_meetings = [
        meeting
        for meeting in meetings
        if meeting.get("status") == "completed"
    ]

    if not eligible_meetings:
        return 0

    attended_meetings = 0

    for meeting in eligible_meetings:
        if participant_attended_meeting(meeting, participant_id):
            attended_meetings += 1

    return round(
        (attended_meetings / len(eligible_meetings)) * 100
    )



# =========================================================
# PARTICIPANT DASHBOARD
# =========================================================


@app.route("/participant/dashboard")
def participant_dashboard():

    if session.get("role") != "participant":

        flash(
            "Please sign in as a participant.",
            "error"
        )

        return redirect(
            url_for("participant_login")
        )

    # Get the logged-in participant's notifications
    participant_id = session.get("user_id")

    notifications = list(
        notifications_collection.find({
            "participant_id": participant_id
        }).sort(
            "_id",
            -1
        )
    )

    # Count unread notifications
    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    # -----------------------------------------------------
    # GET THE LOGGED-IN PARTICIPANT'S MEETINGS
    # -----------------------------------------------------
    participant_meetings_list = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort([
            ("date", 1),
            ("time", 1)
        ])
    )

    # Keep live sessions separate from meetings that have not started.
    live_meetings = [
        meeting
        for meeting in participant_meetings_list
        if meeting.get("status") == "live"
    ][:5]

    upcoming_meetings = [
        meeting
        for meeting in participant_meetings_list
        if meeting.get("status", "upcoming") == "upcoming"
    ][:5]

    # Number shown in the Upcoming Meetings card.
    upcoming_meetings_count = sum(
        1
        for meeting in participant_meetings_list
        if meeting.get("status", "upcoming") == "upcoming"
    )

    # Number of invited meetings that already have an AI summary.
    ai_summary_count = sum(
        1
        for meeting in participant_meetings_list
        if meeting.get("ai_summary")
    )

    attendance_percentage = get_participant_attendance_percentage(
        participant_meetings_list,
        participant_id
    )

    return render_template(
        "participants/dashboards.html",
        notifications=notifications,
        unread_notifications=unread_notifications,
        live_meetings=live_meetings,
        meetings=upcoming_meetings,
        upcoming_meetings_count=upcoming_meetings_count,
        ai_summary_count=ai_summary_count,
        attendance_percentage=attendance_percentage
    )
@app.route("/api/participant/notifications/<notification_id>/read", methods=["POST"])
def mark_participant_notification_read(notification_id):

    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        notification_object_id = ObjectId(notification_id)
    except Exception:
        return jsonify({
            "success": False,
            "message": "Invalid notification ID."
        }), 400

    notification = notifications_collection.find_one({
        "_id": notification_object_id,
        "participant_id": participant_id
    })

    if not notification:
        return jsonify({
            "success": False,
            "message": "Notification not found."
        }), 404

    notifications_collection.update_one(
        {
            "_id": notification_object_id,
            "participant_id": participant_id
        },
        {
            "$set": {
                "read": True
            }
        }
    )

    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    return jsonify({
        "success": True,
        "unread_notifications": unread_notifications
    })


# =========================================================
# PARTICIPANT ATTENDANCE
# =========================================================

@app.route("/participant/attendance")
def participant_attendance():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort([
            ("date", -1),
            ("time", -1)
        ])
    )

    attendance_rows = []

    for meeting in meetings:

        participant_sessions = [
            record
            for record in meeting.get("attendance", [])
            if record.get("participant_id") == participant_id
        ]

        total_seconds = sum(
            int(record.get("duration_seconds", 0) or 0)
            for record in participant_sessions
        )

        last_session = (
            participant_sessions[-1]
            if participant_sessions
            else None
        )

        attendance_rows.append({
            "meeting": meeting,
            "sessions": participant_sessions,
            "session_count": len(participant_sessions),
            "pending": meeting.get("status") != "completed",
            "attended": participant_attended_meeting(meeting, participant_id),
            "total_seconds": total_seconds,
            "last_session": last_session
        })

    attendance_percentage = get_participant_attendance_percentage(
        meetings,
        participant_id
    )

    completed_meetings = [
        meeting
        for meeting in meetings
        if meeting.get("status") == "completed"
    ]

    attended_meetings = sum(
        1
        for meeting in completed_meetings
        if participant_attended_meeting(meeting, participant_id)
    )

    return render_template(
        "participants/attendance.html",
        attendance_rows=attendance_rows,
        attendance_percentage=attendance_percentage,
        attended_meetings=attended_meetings,
        eligible_meetings=len(completed_meetings)
    )


# =========================================================
# ATTENDANCE: JOIN
# =========================================================

@app.route("/api/attendance/<meeting_id>/join", methods=["POST"])
def attendance_join(meeting_id):

    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")
    participant_name = session.get(
        "user_name",
        "Participant"
    )

    try:
        meeting_object_id = ObjectId(meeting_id)
    except Exception:
        return jsonify({
            "success": False,
            "message": "Invalid meeting ID."
        }), 400

    meeting = meetings_collection.find_one({
        "_id": meeting_object_id,
        "invited_participants": participant_id
    })

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found or you are not authorized."
        }), 404

    if meeting.get("status") != "live":
        return jsonify({
            "success": False,
            "message": "Attendance can only be recorded while the meeting is live."
        }), 400

    # Do not create duplicate open sessions if the join request
    # is accidentally sent twice.
    open_session = next(
        (
            record
            for record in meeting.get("attendance", [])
            if (
                record.get("participant_id") == participant_id
                and not record.get("leave_time")
            )
        ),
        None
    )

    if open_session:
        return jsonify({
            "success": True,
            "already_recorded": True,
            "session_id": open_session.get("session_id"),
            "join_time": open_session.get("join_time")
        })

    join_time = datetime.utcnow()
    session_id = str(uuid.uuid4())

    attendance_record = {
        "session_id": session_id,
        "participant_id": participant_id,
        "participant_name": participant_name,
        "join_time": join_time,
        "leave_time": None,
        "duration_seconds": 0,
        "duration_minutes": 0,
        "status": "active"
    }

    meetings_collection.update_one(
        {
            "_id": meeting_object_id,
            "invited_participants": participant_id,
            "status": "live"
        },
        {
            "$push": {
                "attendance": attendance_record
            }
        }
    )

    print(
        "ATTENDANCE JOIN:",
        participant_id,
        meeting_id,
        join_time
    )

    return jsonify({
        "success": True,
        "already_recorded": False,
        "session_id": session_id,
        "join_time": join_time.isoformat()
    })


# =========================================================
# ATTENDANCE: LEAVE
# =========================================================

@app.route("/api/attendance/<meeting_id>/leave", methods=["POST"])
def attendance_leave(meeting_id):

    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    participant_id = session.get("user_id")

    finalized = finalize_attendance_session(
        meeting_id,
        participant_id,
        datetime.utcnow()
    )

    print(
        "ATTENDANCE LEAVE:",
        participant_id,
        meeting_id,
        finalized
    )

    return jsonify({
        "success": True,
        "recorded": finalized
    })


@app.route("/participant/meetings")
def participant_meetings():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    notifications = list(
        notifications_collection.find({
            "participant_id": participant_id
        }).sort("_id", -1)
    )

    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort([("date", -1), ("time", -1)])
    )
    meeting_counts = {
        "live": sum(1 for meeting in meetings if meeting.get("status") == "live"),
        "upcoming": sum(1 for meeting in meetings if meeting.get("status", "upcoming") == "upcoming"),
        "completed": sum(1 for meeting in meetings if meeting.get("status") == "completed")
    }

    return render_template(
        "participants/meetings.html",
        meetings=meetings,
        meeting_counts=meeting_counts,
        notifications=notifications,
        unread_notifications=unread_notifications
    )
@app.route("/participant/meetings/<meeting_id>")
def participant_meeting(meeting_id):

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for(
                "participant_login",
                next=url_for("participant_meeting", meeting_id=meeting_id)
            )
        )

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "Meeting not found.",
            "error"
        )
        return redirect(
            url_for("participant_dashboard")
        )

    invited_participants = meeting.get(
        "invited_participants",
        []
    )

    if participant_id not in invited_participants:
        flash(
            "You are not authorized to access this meeting.",
            "error"
        )
        return redirect(
            url_for("participant_dashboard")
        )

    return render_template(
        "participants/meeting.html",
        meeting=meeting
    )
@app.route("/participant/summary/<meeting_id>")
def participant_summary(meeting_id):

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id
        })
    except Exception:
        meeting = None

    if not meeting:
        flash(
            "Meeting not found or you are not authorized to view it.",
            "error"
        )
        return redirect(
            url_for("participant_meetings")
        )

    ai_summary = meeting.get(
        "ai_summary",
        {
            "discussion_points": [],
            "decisions": [],
            "action_items": []
        }
    )

    return render_template(
        "participants/summary.html",
        meeting=meeting,
        ai_summary=ai_summary
    )
@app.route("/participant/summaries")
def participant_summaries():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id,
            "ai_summary": {
                "$ne": None
            }
        }).sort("date", -1)
    )

    meetings = [
        m for m in meetings
        if summary_has_content(m.get("ai_summary"))
    ]

    return render_template(
        "participants/summaries.html",
        meetings=meetings
    )
# =========================================================
# PARTICIPANT AI INSIGHTS
# =========================================================

@app.route("/participant/insights")
def participant_insights():

    if session.get("role") != "participant":
        flash(
            "Please sign in as a participant.",
            "error"
        )
        return redirect(
            url_for("participant_login")
        )

    participant_id = session.get("user_id")

    notifications = list(
        notifications_collection.find({
            "participant_id": participant_id
        }).sort("_id", -1)
    )

    unread_notifications = notifications_collection.count_documents({
        "participant_id": participant_id,
        "read": False
    })

    meetings = list(
        meetings_collection.find({
            "invited_participants": participant_id
        }).sort(
            "date",
            -1
        )
    )

    insights = []

    for meeting in meetings:

        attention_alerts = meeting.get(
            "attention_alerts",
            []
        )

        participant_alerts = [
            alert
            for alert in attention_alerts
            if alert.get("participant_id") == participant_id
        ]

        ai_summary = meeting.get(
            "ai_summary",
            {}
        )

        insights.append({
            "meeting": meeting,
            "attention_alerts": participant_alerts,
            "alert_count": len(participant_alerts),
            "summary_available": summary_has_content(ai_summary),
            "discussion_count": len(
                ai_summary.get("discussion_points", [])
            ) if isinstance(ai_summary, dict) else 0,
            "decision_count": len(
                ai_summary.get("decisions", [])
            ) if isinstance(ai_summary, dict) else 0,
            "action_count": len(
                ai_summary.get("action_items", [])
            ) if isinstance(ai_summary, dict) else 0
        })

    total_meetings = len(insights)

    meetings_with_summary = sum(
        1
        for item in insights
        if item["summary_available"]
    )

    total_attention_alerts = sum(
        item["alert_count"]
        for item in insights
    )

    return render_template(
        "participants/insights.html",
        insights=insights,
        total_meetings=total_meetings,
        meetings_with_summary=meetings_with_summary,
        total_attention_alerts=total_attention_alerts,
        notifications=notifications,
        unread_notifications=unread_notifications
    )


# =========================================================
# ATTENTION MONITORING API
# =========================================================

@app.route("/api/attention/<meeting_id>", methods=["POST"])
def attention_api(meeting_id):

    if session.get("role") != "participant":
        return {
            "success": False,
            "message": "Unauthorized"
        }, 401

    from bson.objectid import ObjectId
    from datetime import datetime

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id
        })
    except Exception:
        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found or participant is not authorized"
        }, 404

    # Attention monitoring is allowed only while the meeting is live
    if meeting.get("status") != "live":
        return {
            "success": False,
            "message": "Attention monitoring is available only during a live meeting"
        }, 400

    data = request.get_json(silent=True) or {}

    score = data.get("score", 0)

    try:
        score = int(score)
    except (ValueError, TypeError):
        score = 0

    # Keep score between 0 and 100
    score = max(0, min(100, score))

    if score == 100:
        status = "Attentive"

    elif score == 75:
        status = "Partially Attentive"

    else:
        status = "Not Attentive"

    now = datetime.utcnow()
    tracking = meeting.get("attention_tracking", [])
    state = next(
        (item for item in tracking if item.get("participant_id") == participant_id),
        None
    )

    # A partially attentive score still counts toward the host alert. Only a
    # fully attentive score clears the continuous inattention timer.
    if score == 100:
        if state:
            meetings_collection.update_one(
                {"_id": ObjectId(meeting_id)},
                {"$pull": {"attention_tracking": {"participant_id": participant_id}}}
            )
        return {
            "success": True,
            "meeting_id": meeting_id,
            "score": score,
            "status": status,
            "alert_sent": False
        }

    if not state:
        state = {
            "participant_id": participant_id,
            "participant_name": session.get("user_name", "Participant"),
            "not_attentive_since": now,
            "last_update_at": now,
            "alert_sent": False
        }
        meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id),
                "attention_tracking.participant_id": {"$ne": participant_id}
            },
            {"$push": {"attention_tracking": state}}
        )
    else:
        last_update = state.get("last_update_at", state.get("not_attentive_since"))
        if isinstance(last_update, str):
            try:
                last_update = datetime.fromisoformat(last_update)
            except ValueError:
                last_update = now
        if not last_update or now - last_update > timedelta(seconds=30):
            state["not_attentive_since"] = now
            state["alert_sent"] = False
        state["last_update_at"] = now
        meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id),
                "attention_tracking.participant_id": participant_id
            },
            {"$set": {
                "attention_tracking.$.not_attentive_since": state["not_attentive_since"],
                "attention_tracking.$.last_update_at": now,
                "attention_tracking.$.alert_sent": state["alert_sent"]
            }}
        )

    since = state.get("not_attentive_since")
    if isinstance(since, str):
        try:
            since = datetime.fromisoformat(since)
        except ValueError:
            since = now

    alert_sent = bool(state.get("alert_sent"))
    if since and not alert_sent and now - since >= timedelta(minutes=1):
        result = meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id),
                "attention_tracking": {
                    "$elemMatch": {
                        "participant_id": participant_id,
                        "alert_sent": {"$ne": True}
                    }
                }
            },
            {"$set": {"attention_tracking.$.alert_sent": True}}
        )
        if result.modified_count:
            socketio.emit(
                "attention_alert",
                {
                    "participant_name": state.get("participant_name") or session.get("user_name", "Participant"),
                    "score": score,
                    "message": f"{state.get('participant_name') or session.get('user_name', 'A participant')} has been inattentive for 1 minute.",
                    "time": now.strftime("%H:%M:%S")
                },
                room=f"meeting_{meeting_id}"
            )
            alert_sent = True

    return {
        "success": True,
        "meeting_id": meeting_id,
        "score": score,
        "status": status,
        "alert_sent": alert_sent
    }
# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    flash(
        "You have been logged out successfully.",
        "success"
    )

    return redirect(
        url_for("role")
    )

@app.route("/test-email")
def test_email():

    msg = Message(
        subject="MeetIQ Email Test",
        recipients=["YOUR_TEST_EMAIL@gmail.com"]
    )

    msg.body = """
Hello!

This is a test email from MeetIQ.

If you received this email, the MeetIQ email system is working correctly.

Regards,
MeetIQ
"""

    mail.send(msg)

    return "Test email sent successfully!"
@app.route("/api/participant/meeting-status/<meeting_id>")
def participant_meeting_status(meeting_id):

    if session.get("role") != "participant":
        return {
            "success": False,
            "message": "Unauthorized"
        }, 401

    from bson.objectid import ObjectId

    participant_id = session.get("user_id")

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id
        })
    except Exception:
        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found or unauthorized"
        }, 404

    return {
        "success": True,
        "status": meeting.get("status", "upcoming")
    }
@socketio.on("join_meeting")
def handle_join_meeting(data):
    meeting_id = data.get("meeting_id")
    user_id = session.get("user_id")
    role = session.get("role")

    if not meeting_id or not user_id:
        return

    from bson.objectid import ObjectId

    try:
        if role == "admin":
            meeting = meetings_collection.find_one({
                "_id": ObjectId(meeting_id),
                "created_by_email": user_id
            })
        elif role == "participant":
            meeting = meetings_collection.find_one({
                "_id": ObjectId(meeting_id),
                "invited_participants": user_id,
                "status": "live"
            })
        else:
            meeting = None
    except Exception:
        meeting = None

    if not meeting:
        return

    room = f"meeting_{meeting_id}"
    join_room(room)
    socket_meetings[request.sid] = meeting_id

    if role == "participant":
        emit(
            "participant_joined",
            {
                "participant_id": user_id,
                "participant_name": session.get("user_name", "Participant"),
                "message": "A participant joined the meeting."
            },
            room=room,
            include_self=False
        )
        return

    # If participants entered before the host page connected, tell the host
    # about the participants already in this live meeting room.
    for participant_id in meeting.get("invited_participants", []):
        participant_socket = socket_users.get(participant_id)
        if not participant_socket or socket_meetings.get(participant_socket) != meeting_id:
            continue
        participant = participants_collection.find_one({"user_id": participant_id}) or {}
        socketio.emit(
            "participant_joined",
            {
                "participant_id": participant_id,
                "participant_name": participant.get("name", "Participant"),
                "message": "A participant is already in the meeting."
            },
            to=request.sid
        )

@socketio.on("webrtc_offer")
def handle_webrtc_offer(data):

    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    offer = data.get("offer")

    if not meeting_id or not target_id or not offer:
        return

    target_socket_id = socket_users.get(target_id)

    if not target_socket_id:
        print(
            "Target participant is not connected:",
            target_id
        )
        return

    emit(
        "webrtc_offer",
        {
            "sender_id": session.get("user_id"),
            "offer": offer
        },
        to=target_socket_id
    )

@socketio.on("webrtc_ice_candidate")
def handle_webrtc_ice_candidate(data):

    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    candidate = data.get("candidate")

    if not meeting_id or not target_id or not candidate:
        return

    target_socket_id = socket_users.get(target_id)

    if not target_socket_id:
        print(
            "Target user is not connected:",
            target_id
        )
        return

    emit(
        "webrtc_ice_candidate",
        {
            "sender_id": session.get("user_id"),
            "candidate": candidate
        },
        to=target_socket_id
    )


@socketio.on("webrtc_answer")
def handle_webrtc_answer(data):

    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    answer = data.get("answer")

    if not meeting_id or not target_id or not answer:
        return

    target_socket_id = socket_users.get(target_id)

    if not target_socket_id:
        print(
            "Target user is not connected:",
            target_id
        )
        return

    emit(
        "webrtc_answer",
        {
            "sender_id": session.get("user_id"),
            "answer": answer
        },
        to=target_socket_id
    )
@socketio.on("leave_meeting")
def handle_leave_meeting(data):

    meeting_id = data.get("meeting_id")

    if not meeting_id:
        return

    room = f"meeting_{meeting_id}"

    leave_room(room)
    socket_meetings.pop(request.sid, None)

    emit(
        "participant_left",
        {
            "participant_id": session.get("user_id"),
            "participant_name": session.get("user_name", "Participant"),
            "role": session.get("role"),
            "message": "The host left the meeting." if session.get("role") == "admin" else "A participant left the meeting."
        },
        room=room,
        include_self=False
    )
    return {"success": True}


@socketio.on("participant_media_state")
def handle_participant_media_state(data):
    meeting_id = data.get("meeting_id")
    if (session.get("role") != "participant" or not meeting_id
            or socket_meetings.get(request.sid) != meeting_id):
        return
    emit(
        "participant_media_state",
        {
            "participant_id": session.get("user_id"),
            "participant_name": session.get("user_name", "Participant"),
            "mic_enabled": bool(data.get("mic_enabled")),
            "camera_enabled": bool(data.get("camera_enabled")),
            "screen_share_allowed": bool(data.get("screen_share_allowed", True))
        },
        room=f"meeting_{meeting_id}",
        include_self=False
    )


@socketio.on("host_media_state")
def handle_host_media_state(data):
    meeting_id = data.get("meeting_id")
    if (session.get("role") != "admin" or not meeting_id
            or socket_meetings.get(request.sid) != meeting_id):
        return
    socketio.emit(
        "host_media_state",
        {"mic_enabled": bool(data.get("mic_enabled"))},
        room=f"meeting_{meeting_id}",
        include_self=False
    )


@socketio.on("participant_control")
def handle_participant_control(data):
    meeting_id = data.get("meeting_id")
    target_id = data.get("target_id")
    action = data.get("action")
    if (session.get("role") != "admin" or not meeting_id or not target_id
            or action not in {"mute_participant_mic", "turn_camera_off", "request_mic_on", "request_camera_on", "toggle_screen_permission"}
            or socket_meetings.get(request.sid) != meeting_id):
        return

    from bson.objectid import ObjectId
    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "created_by_email": session.get("user_id")
        })
    except Exception:
        meeting = None
    if not meeting or target_id not in meeting.get("invited_participants", []):
        return

    target_socket = socket_users.get(target_id)
    if target_socket and socket_meetings.get(target_socket) == meeting_id:
        socketio.emit(
            "participant_control",
            {"action": action},
            to=target_socket
        )


@socketio.on("participant_control_response")
def handle_participant_control_response(data):
    meeting_id = data.get("meeting_id")
    if (session.get("role") != "participant" or not meeting_id
            or socket_meetings.get(request.sid) != meeting_id):
        return
    socketio.emit(
        "participant_control_response",
        {
            "participant_id": session.get("user_id"),
            "participant_name": session.get("user_name", "Participant"),
            "action": data.get("action"),
            "accepted": bool(data.get("accepted"))
        },
        room=f"meeting_{meeting_id}",
        include_self=False
    )


@socketio.on("screen_share_status")
def handle_screen_share_status(data):
    meeting_id = data.get("meeting_id")
    if not meeting_id or socket_meetings.get(request.sid) != meeting_id:
        return
    if session.get("role") not in ("admin", "participant"):
        return
    socketio.emit(
        "screen_share_status",
        {
            "sharing": bool(data.get("sharing")),
            "participant_name": session.get("user_name", "Host"),
            "participant_id": session.get("user_id"),
            "role": session.get("role")
        },
        room=f"meeting_{meeting_id}",
        include_self=False
    )
@socketio.on("send_chat_message")
def handle_chat_message(data):

    meeting_id = data.get("meeting_id")
    message = data.get("message")

    if not meeting_id or not message:
        return

    message = message.strip()

    if not message or len(message) > 2000:
        return

    user_id = session.get("user_id")
    user_name = session.get("user_name")

    if not user_id or socket_meetings.get(request.sid) != meeting_id:
        return

    from bson.objectid import ObjectId
    try:
        if session.get("role") == "admin":
            meeting = meetings_collection.find_one({
                "_id": ObjectId(meeting_id),
                "created_by_email": user_id,
                "status": "live"
            })
        elif session.get("role") == "participant":
            meeting = meetings_collection.find_one({
                "_id": ObjectId(meeting_id),
                "invited_participants": user_id,
                "status": "live"
            })
        else:
            meeting = None
    except Exception:
        meeting = None
    if not meeting:
        return

    toxicity_score = 0.0
    try:
        toxicity = detect_text_toxicity(message)
        toxicity_score = toxicity_risk_score(toxicity)
    except Exception as error:
        print("MeetIQ: Chat toxicity analysis failed:", error)
        toxicity = {}

    if toxicity_score >= 0.60:
        warning = "Your message was blocked for abusive language. Please keep the chat respectful."
        socketio.emit(
            "toxicity_moderation",
            {"action": "warning", "source": "chat", "message": warning,
             "toxicity_score": round(toxicity_score, 4)},
            to=request.sid
        )
        if session.get("role") == "participant":
            meetings_collection.update_one(
                {"_id": ObjectId(meeting_id)},
                {"$push": {"toxicity_events": {
                    "participant_id": user_id,
                    "participant_name": user_name or "Participant",
                    "transcript": message,
                    "source": "chat",
                    "toxicity_score": round(toxicity_score, 4),
                    "categories": toxicity,
                    "action": "warning",
                    "message": warning,
                    "timestamp": datetime.utcnow()
                }}}
            )
            socketio.emit(
                "toxicity_alert",
                {"participant_name": user_name or "Participant", "source": "chat",
                 "message": f"{user_name or 'A participant'} sent a chat message flagged for abusive language."},
                room=f"meeting_{meeting_id}"
            )
        # Keep the flagged text out of the meeting chat room.
        return

    room = f"meeting_{meeting_id}"

    emit(
        "receive_chat_message",
        {
            "sender_id": user_id,
            "sender_name": user_name or "User",
            "message": message,
            "toxicity_score": round(toxicity_score, 4),
            "flagged": toxicity_score >= 0.60
        },
        room=room
    )
# ============================================================
# WHISPER SPEECH-TO-TEXT
# ============================================================

whisper_path = (
    Path(__file__).resolve().parent.parent
    / "ai-services"
    / "speech-to-text"
)

if str(whisper_path) not in sys.path:
    sys.path.insert(0, str(whisper_path))

import speech_to_text

# Reuse the Whisper model already loaded by speech_to_text.py.
model = speech_to_text.model

print("MeetIQ: Whisper module connected (shared model).")
# ============================================================
# =========================================================
# AI MEETING SUMMARY
# =========================================================

def build_meeting_transcript(meeting):
    """Build a readable transcript from saved Whisper segments."""
    segments = meeting.get("transcript_segments", [])

    lines = []

    for segment in segments:
        speaker = segment.get("participant_name", "Participant")
        spoken_text = str(segment.get("text", "")).strip()

        if spoken_text:
            lines.append(f"{speaker}: {spoken_text}")

    return "\n".join(lines)


def generate_meeting_ai_summary(meeting_id):
    """Generate and save the AI summary using the local Ollama summarizer."""
    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        return {
            "success": False,
            "message": "Meeting not found."
        }

    transcript = build_meeting_transcript(meeting)

    if not transcript.strip():
        return {
            "success": False,
            "message": "No meeting transcript is available yet."
        }

    print("\nMeetIQ: Generating AI meeting summary...")
    print("MeetIQ: Transcript length:", len(transcript))

    try:
        ai_summary = generate_summary(transcript)

        if not isinstance(ai_summary, dict):
            raise ValueError(
                "The summarizer returned an invalid summary format."
            )
        if not summary_has_content(ai_summary):
            return {
                "success": False,
                "message": "The AI found no discussion points, decisions or "
                           "action items in the transcript."
            }
        ai_summary.setdefault("discussion_points", [])
        ai_summary.setdefault("decisions", [])
        ai_summary.setdefault("action_items", [])

        meetings_collection.update_one(
            {"_id": ObjectId(meeting_id)},
            {
                "$set": {
                    "ai_summary": ai_summary,
                    "transcript": transcript,
                    "summary_generated_at": datetime.utcnow()
                }
            }
        )

        print("MeetIQ: AI meeting summary saved successfully.")

        return {
            "success": True,
            "summary": ai_summary
        }

    except Exception as error:
        print("MeetIQ: AI summary generation failed:", error)

        return {
            "success": False,
            "message": str(error)
        }


# TOXICITY DETECTION
# ============================================================

toxicity_path = (
    Path(__file__).resolve().parent.parent
    / "ai-services"
    / "toxicity-detection"
)

if str(toxicity_path) not in sys.path:
    sys.path.insert(0, str(toxicity_path))

from toxicity_detector import (
    detect_text_toxicity,
    analyze_voice,
    moderation_decision
)


def toxicity_risk_score(scores):
    """Use the strongest harmful-language category, including obscene terms."""
    categories = (
        "toxicity", "severe_toxicity", "obscene", "threat", "insult",
        "identity_attack"
    )
    try:
        return max(float(scores.get(category, 0) or 0) for category in categories)
    except (AttributeError, TypeError, ValueError):
        return 0.0

print("MeetIQ: Toxicity detection module connected.")
@app.route("/api/meeting-summary/<meeting_id>", methods=["POST"])
def create_meeting_summary(meeting_id):
    """Generate the AI summary from Whisper transcript segments."""
    if session.get("role") not in ["admin", "participant"]:
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    if session.get("role") == "admin":
        authorized = (
            meeting.get("created_by_email")
            == session.get("user_id")
        )
    else:
        authorized = (
            session.get("user_id")
            in meeting.get("invited_participants", [])
        )

    if not authorized:
        return jsonify({
            "success": False,
            "message": "You are not authorized to summarize this meeting."
        }), 403

    result = generate_meeting_ai_summary(meeting_id)

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


@app.route("/api/meeting-summary/<meeting_id>", methods=["GET"])
def get_meeting_summary(meeting_id):
    """Return the saved AI summary and transcript status."""
    if session.get("role") not in ["admin", "participant"]:
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        meeting = None

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found."
        }), 404

    if session.get("role") == "admin":
        authorized = (
            meeting.get("created_by_email")
            == session.get("user_id")
        )
    else:
        authorized = (
            session.get("user_id")
            in meeting.get("invited_participants", [])
        )

    if not authorized:
        return jsonify({
            "success": False,
            "message": "You are not authorized to view this summary."
        }), 403

    return jsonify({
        "success": True,
        "meeting_id": meeting_id,
        "summary": meeting.get(
            "ai_summary",
            {
                "discussion_points": [],
                "decisions": [],
                "action_items": []
            }
        ),
        "transcript_available": bool(
            meeting.get("transcript", "").strip()
        ),
        "transcript_segments": len(
            meeting.get("transcript_segments", [])
        ),
        "generated_at": meeting.get("summary_generated_at")
    })


@app.route("/api/meeting-audio/realtime", methods=["POST"])
def realtime_meeting_audio():
    if session.get("role") != "participant":
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    participant_id = session.get("user_id")
    meeting_id = request.form.get("meeting_id")
    audio = request.files.get("audio")

    if not meeting_id or not audio:
        return jsonify({
            "success": False,
            "message": "Meeting ID or audio is missing."
        }), 400

    from bson.objectid import ObjectId

    try:
        meeting = meetings_collection.find_one({
            "_id": ObjectId(meeting_id),
            "invited_participants": participant_id,
            "status": "live"
        })
    except Exception:
        meeting = None

    if not meeting:
        return jsonify({
            "success": False,
            "message": "Meeting not found, unauthorized, or not live."
        }), 403

    recordings_path = (
        Path(__file__).resolve().parent.parent
        / "ai-services"
        / "speech-to-text"
        / "recordings"
    )

    recordings_path.mkdir(
        parents=True,
        exist_ok=True
    )

    import uuid

    chunk_filename = (
        f"realtime_{uuid.uuid4().hex}.webm"
    )

    chunk_path = recordings_path / chunk_filename

    try:

        # -----------------------------------------
        # 1. Save temporary audio chunk
        # -----------------------------------------

        audio.save(str(chunk_path))

        print(
            "\nMeetIQ: Processing real-time audio chunk..."
        )

        # -----------------------------------------
        # 2. Whisper speech-to-text
        # -----------------------------------------

        print("MeetIQ: Transcribing real-time audio...")

        configured_language = os.getenv("MEETIQ_SPEECH_LANGUAGE", "").strip()
        result = model.transcribe(
            str(chunk_path),
            language=configured_language or None,
            task="transcribe",
            condition_on_previous_text=False,
            no_speech_threshold=0.8,
            temperature=0,
            fp16=False
        )

        # Whisper can assign a high no-speech probability to a short, quiet
        # utterance while still returning usable text. Do not discard that
        # text based on the probability alone; moderation needs to see it.
        transcript = str(result.get("text", "")).strip()

        print(
            "Real-time Transcript:",
            transcript
        )

        # Run transcript moderation before the heavier audio tone classifier.
        # This lets us warn/mute on clearly abusive words without waiting for
        # the audio model to finish analyzing the same chunk.
        toxicity = detect_text_toxicity(transcript) if transcript else {}
        text_toxicity_score = toxicity_risk_score(toxicity)
        early_moderation_sent = False

        if text_toxicity_score >= 0.60:
            early_message = "Your microphone was muted because offensive language was detected. Ask the host to request unmuting before speaking again."
            participant_socket = socket_users.get(participant_id)
            if participant_socket:
                socketio.emit(
                    "toxicity_moderation",
                    {
                        "action": "mute",
                        "message": early_message,
                        "toxicity_score": round(text_toxicity_score, 4)
                    },
                    to=participant_socket
                )
                early_moderation_sent = True

        # Analyze the original audio too, so moderation can use vocal delivery
        # and acoustic evidence even when Whisper returns no transcript.
        try:
            from voice_safety import detect_audio_toxicity
            voice_toxicity = detect_audio_toxicity(chunk_path)
        except Exception as voice_error:
            print("MeetIQ: Voice safety model failed:", voice_error)
            voice_toxicity = {}

        voice_toxicity_score = max(
            (float(value) for value in voice_toxicity.values()),
            default=0.0
        )

        if not transcript and voice_toxicity_score < 0.60:
            return jsonify({
                "success": True,
                "transcript": "",
                "toxicity_score": round(voice_toxicity_score, 4),
                "action": "none",
                "message": "No speech detected."
            })

        # Save readable transcript segments when Whisper found speech.
        if transcript:
            transcript_segment = {
                "participant_id": participant_id,
                "participant_name": session.get("user_name", "Participant"),
                "text": transcript,
                "timestamp": datetime.utcnow()
            }
            meetings_collection.update_one(
                {"_id": ObjectId(meeting_id)},
                {"$push": {"transcript_segments": transcript_segment}}
            )
            print("MeetIQ: Transcript segment saved for AI summary.")

        # -----------------------------------------
        # 3. Detect toxicity from transcript + voice
        # -----------------------------------------
        print("MeetIQ: Combining transcript and voice toxicity results...")
        toxicity["voice_safety"] = voice_toxicity
        toxicity_score = max(text_toxicity_score, voice_toxicity_score)

        print(
            "Real-time Toxicity Score:",
            toxicity_score
        )

        print(
            "Real-time Toxicity Categories:",
            toxicity
        )

        # -----------------------------------------
        # 4. Count previous toxic events
        # -----------------------------------------

        previous_toxic_events = meetings_collection.count_documents({
            "_id": ObjectId(meeting_id),
            "toxicity_events": {
                "$elemMatch": {
                    "participant_id": participant_id,
                    "toxicity_score": {
                        "$gte": 0.60
                    }
                }
            }
        })

        # -----------------------------------------
        # 5. Moderation decision
        # -----------------------------------------

        moderation = moderation_decision(
            toxicity_score,
            previous_toxic_events
        )

        action = moderation["action"]
        message = moderation["message"]
        if toxicity_score >= 0.60:
            action = "mute"
            message = "Your microphone was muted because offensive language was detected. Ask the host to request unmuting before speaking again."

        print(
            "Previous toxic events:",
            previous_toxic_events
        )

        print(
            "Moderation Action:",
            action
        )

        print(
            "Moderation Message:",
            message
        )

        # -----------------------------------------
        # 6. Save toxicity event
        # -----------------------------------------

        if toxicity_score >= 0.60:

            toxicity_event = {
                "participant_id": participant_id,
                "transcript": transcript,
                "source": "audio",
                "toxicity_score": round(
                    toxicity_score,
                    4
                ),
                "categories": toxicity,
                "action": action,
                "message": message,
                "timestamp": datetime.utcnow()
            }

            meetings_collection.update_one(
                {
                    "_id": ObjectId(meeting_id)
                },
                {
                    "$push": {
                        "toxicity_events":
                        toxicity_event
                    }
                }
            )

            socketio.emit(
                "toxicity_alert",
                {
                    "participant_name": session.get("user_name", "Participant"),
                    "source": "audio",
                    "message": f"{session.get('user_name', 'A participant')} used language flagged for abusive content in audio."
                },
                room=f"meeting_{meeting_id}"
            )

            print(
                "MeetIQ: Real-time toxicity event saved."
            )

        # -----------------------------------------
        # 7. Send warning / mute command
        #    to participant browser
        # -----------------------------------------

        if action in ["warning", "mute"] and not early_moderation_sent:

            participant_socket = socket_users.get(
                participant_id
            )

            if participant_socket:

                socketio.emit(
                    "toxicity_moderation",
                    {
                        "action": action,
                        "message": message,
                        "toxicity_score": round(
                            toxicity_score,
                            4
                        )
                    },
                    to=participant_socket
                )

                print(
                    "MeetIQ: Moderation event sent to participant."
                )

            else:

                print(
                    "MeetIQ: Participant socket not found."
                )

        # -----------------------------------------
        # 8. Delete temporary audio
        # -----------------------------------------

        if chunk_path.exists():
            chunk_path.unlink()

        return jsonify({
            "success": True,
            "transcript": transcript,
            "toxicity_score": round(
                toxicity_score,
                4
            ),
            "action": action,
            "message": message
        })

    except Exception as error:

        print(
            "Real-time audio processing error:",
            error
        )

        if chunk_path.exists():
            chunk_path.unlink()

        return jsonify({
            "success": False,
            "message": str(error)
        }), 500
# =========================================================
# RUN APPLICATION
# =========================================================
if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=not IS_PRODUCTION,

    )
