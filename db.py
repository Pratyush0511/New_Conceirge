import certifi
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")


client = MongoClient(
    MONGO_URI,
    tls=True,
    tlsAllowInvalidCertificates=False,
    tlsCAFile=certifi.where()
)

MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")
db = client[MONGO_DB_NAME]
users_collection = db["users"]
