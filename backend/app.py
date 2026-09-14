from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from pathlib import Path
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

# =========================================================
# SECRET KEY
# =========================================================

app.secret_key = "meetiq-development-secret-key"


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
        "auth/admin_register.html"
    )


# =========================================================
# PARTICIPANT LOGIN
# =========================================================

@app.route(
    "/participant/login",
    methods=["GET", "POST"]
)
def participant_login():

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
                url_for("participant_login")
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

            return redirect(
                url_for("participant_dashboard")
            )

        # Invalid credentials
        flash(
            "Invalid email/ID or password.",
            "error"
        )

        return redirect(
            url_for("participant_login")
        )

    return render_template(
        "auth/participant_login.html"
    )


# =========================================================
# PARTICIPANT REGISTER
# =========================================================

@app.route(
    "/participant/register",
    methods=["GET", "POST"]
)
def participant_register():

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
                url_for("participant_register")
            )

        # Password confirmation
        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return redirect(
                url_for("participant_register")
            )

        # Minimum password length
        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("participant_register")
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
                url_for("participant_register")
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
                url_for("participant_register")
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

       

        flash(
            "Participant account created successfully. Please sign in.",
            "success"
        )

        return redirect(
            url_for("participant_login")
        )

    return render_template(
        "auth/participant_register.html"
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin/dashboard")
def admin_dashboard():

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    return render_template(
        "admin/dashboard.html"
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

    meetings = list(
        meetings_collection.find().sort(
            "date",
            1
        )
    )

    # -----------------------------------------------------
    # MEETING STATISTICS
    # -----------------------------------------------------

    total_meetings = len(meetings)

    upcoming_meetings = 0
    total_participants = 0
    ai_insights = 0

    for meeting in meetings:

        # Count upcoming meetings
        if meeting.get("status") == "upcoming":

            upcoming_meetings += 1

        # Count invited participants
        invited = meeting.get(
            "invited_participants",
            []
        )

        total_participants += len(invited)

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

        total_participants=total_participants,

        ai_insights=ai_insights
    )
# =========================================================
# INVITE PARTICIPANTS TO MEETING
# =========================================================
@app.route("/admin/meetings/<meeting_id>")
def meeting_details(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(
            url_for("admin_login")
        )

    from bson.objectid import ObjectId

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
            url_for("admin_meetings")
        )

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
@app.route(
    "/admin/meetings/<meeting_id>/delete",
    methods=["POST"]
)
def delete_meeting(meeting_id):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(
            url_for("admin_login")
        )

    from bson.objectid import ObjectId

    try:
        result = meetings_collection.delete_one({
            "_id": ObjectId(meeting_id)
        })
    except Exception:
        result = None

    if not result or result.deleted_count == 0:
        flash(
            "Meeting not found.",
            "error"
        )
    else:
        flash(
            "Meeting deleted successfully!",
            "success"
        )

    return redirect(
        url_for("admin_meetings")
    )


@app.route(
    "/admin/meetings/<meeting_id>/remove-invitation/<participant_id>",
    methods=["POST"]
)
def remove_invitation(
    meeting_id,
    participant_id
):

    if session.get("role") != "admin":
        flash(
            "Please sign in as an administrator.",
            "error"
        )
        return redirect(
            url_for("admin_login")
        )

    from bson.objectid import ObjectId

    try:
        result = meetings_collection.update_one(
            {
                "_id": ObjectId(meeting_id)
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
        flash(
            "Invitation not found.",
            "error"
        )
    else:
        flash(
            "Invitation removed successfully!",
            "success"
        )

    return redirect(
        url_for(
            "meeting_details",
            meeting_id=meeting_id
        )
    )
@app.route(
    "/admin/meetings/<meeting_id>/invite",
    methods=["GET", "POST"]
)
def invite_participants(meeting_id):

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    # -----------------------------------------------------
    # FIND MEETING
    # -----------------------------------------------------

    from bson.objectid import ObjectId

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
            url_for("admin_meetings")
        )

    # -----------------------------------------------------
    # GET REGISTERED PARTICIPANTS
    # -----------------------------------------------------

    participants = list(
        participants_collection.find().sort(
            "name",
            1
        )
    )

    # -----------------------------------------------------
    # SHOW INVITATION PAGE
    # -----------------------------------------------------

    if request.method == "GET":

        return render_template(
            "admin/invite_participants.html",
            meeting=meeting,
            participants=participants
        )

    # -----------------------------------------------------
    # GET SELECTED PARTICIPANTS
    # -----------------------------------------------------

    selected_participants = request.form.getlist(
        "participant_ids"
    )

    # -----------------------------------------------------
    # SAVE INVITED PARTICIPANTS
    # -----------------------------------------------------

    meetings_collection.update_one(

        {
            "_id": ObjectId(meeting_id)
        },

        {
            "$set": {
                "invited_participants":
                    selected_participants
            }
        }

    )

    flash(
        "Participants invited successfully!",
        "success"
    )

    return redirect(
        url_for("admin_meetings")
    )
# =========================================================
# CREATE MEETING
# =========================================================

@app.route(
    "/admin/meetings/create",
    methods=["GET", "POST"]
)
def create_meeting():

    if session.get("role") != "admin":

        flash(
            "Please sign in as an administrator.",
            "error"
        )

        return redirect(
            url_for("admin_login")
        )

    # -----------------------------------------------------
    # SHOW CREATE MEETING PAGE
    # -----------------------------------------------------

    if request.method == "GET":

        return render_template(
            "admin/create_meeting.html"
        )

    # -----------------------------------------------------
    # GET FORM VALUES
    # -----------------------------------------------------

    title = request.form.get(
        "title",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    date = request.form.get(
        "date",
        ""
    ).strip()

    time = request.form.get(
        "time",
        ""
    ).strip()

    duration = request.form.get(
        "duration",
        "60"
    ).strip()

    meeting_type = request.form.get(
        "meeting_type",
        "team"
    ).strip()

    access = request.form.get(
        "access",
        "selected"
    ).strip()

    # -----------------------------------------------------
    # REQUIRED FIELD VALIDATION
    # -----------------------------------------------------

    if not title or not date or not time:

        flash(
            "Please fill in the meeting title, date and time.",
            "error"
        )

        return redirect(
            url_for("create_meeting")
        )

    # -----------------------------------------------------
    # FORMAT DATE
    # -----------------------------------------------------

    formatted_date = format_meeting_date(
        date
    )

    # -----------------------------------------------------
    # FORMAT TIME
    # -----------------------------------------------------

    formatted_time = format_meeting_time(
        time
    )

    # -----------------------------------------------------
    # CREATE MEETING RECORD
    # -----------------------------------------------------

    meeting = {

        "title": title,

        "description": description,

        # Original date
        "date": date,

        # Display date
        "formatted_date": formatted_date,

        # Original time
        "time": time,

        # Display time with AM/PM
        "formatted_time": formatted_time,

        "duration": duration,

        "meeting_type": meeting_type,

        "access": access,

        "created_by": session.get(
            "user_name",
            "Administrator"
        )
    }

        # -----------------------------------------------------
    # STORE MEETING IN MONGODB
    # -----------------------------------------------------

    create_meeting_record(
        title=title,
        description=description,
        date=date,
        formatted_date=formatted_date,
        time=time,
        formatted_time=formatted_time,
        duration=duration,
        meeting_type=meeting_type,
        access=access,
        created_by=session.get(
            "user_name",
            "Administrator"
        )
    )

    # -----------------------------------------------------
    # SUCCESS MESSAGE
    # -----------------------------------------------------

    flash(
        "Meeting created successfully!",
        "success"
    )

    # -----------------------------------------------------
    # GO TO MEETINGS PAGE
    # -----------------------------------------------------

    return redirect(
        url_for("admin_meetings")
    )

# =========================================================
# ADMIN PARTICIPANTS
# =========================================================

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

    # -----------------------------------------------------
    # GET ALL PARTICIPANTS FROM MONGODB
    # -----------------------------------------------------

    participants = list(
        participants_collection.find().sort(
            "name",
            1
        )
    )

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

    return render_template(
        "participants/dashboards.html"
    )


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


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )