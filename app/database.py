import os
from pymongo import MongoClient
from pymongo.database import Database
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI environment variable is not set. Check your .env file.")

# Create the MongoDB client (connection is lazy — connects on first operation)
client = MongoClient(MONGODB_URI)

# The database name is "facetracer"
mongo_db: Database = client["facetracer"]

# Collections — equivalent to SQLAlchemy tables
users_collection = mongo_db["users"]
missing_persons_collection = mongo_db["missing_persons"]
search_histories_collection = mongo_db["search_histories"]

# Create indexes for fast lookups
users_collection.create_index("email", unique=True)
missing_persons_collection.create_index("status")
missing_persons_collection.create_index("created_at")
search_histories_collection.create_index("search_date")


def get_db() -> Database:
    """FastAPI dependency that returns the MongoDB database object."""
    return mongo_db
