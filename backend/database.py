import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")

if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in .env")

client = MongoClient(MONGODB_URI)

try:
    client.admin.command("ping")
    print("MongoDB connected successfully!")

    db = client["meetiq"]

    print("Database selected: meetiq")

except Exception as error:
    print("MongoDB connection failed:")
    print(error)