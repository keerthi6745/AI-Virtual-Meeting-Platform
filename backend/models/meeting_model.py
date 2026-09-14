from database import db


meetings_collection = db["meetings"]


def create_meeting(
    title,
    description,
    date,
    formatted_date,
    time,
    formatted_time,
    duration,
    meeting_type,
    access,
    created_by
):
    meeting = {
        "title": title,
        "description": description,
        "date": date,
        "formatted_date": formatted_date,
        "time": time,
        "formatted_time": formatted_time,
        "duration": duration,
        "meeting_type": meeting_type,
        "access": access,
        "created_by": created_by,

        # Participants invited to this meeting
        "invited_participants": [],

        # Meeting status
        "status": "upcoming",

        # AI-generated information will be added later
        "ai_summary": None,
        "ai_insights": []
    }

    result = meetings_collection.insert_one(meeting)

    meeting["_id"] = result.inserted_id

    return meeting