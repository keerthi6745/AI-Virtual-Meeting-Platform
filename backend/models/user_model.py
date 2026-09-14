from database import db
from werkzeug.security import generate_password_hash


admins_collection = db["admins"]
participants_collection = db["participants"]


def create_admin(name, email, password):
    admin = {
        "name": name,
        "email": email.lower().strip(),
        "password": generate_password_hash(password)
    }

    admins_collection.insert_one(admin)

    return admin


def create_participant(
    name,
    email,
    department,
    year,
    user_id,
    password
):
    participant = {
        "name": name,
        "email": email.lower().strip(),
        "department": department,
        "year": year,
        "user_id": user_id,
        "password": generate_password_hash(password)
    }

    participants_collection.insert_one(participant)

    return participant