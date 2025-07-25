import certifi
import os
from pymongo import MongoClient
from dotenv import load_dotenv
import logging
import sys


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()


MONGO_URI = os.getenv("MONGO_URI")
if MONGO_URI:
    MONGO_URI = MONGO_URI.strip()

MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")


client = None
db = None
users_collection = None
history_collection = None 

if not MONGO_URI:
    logging.error("❌ MONGO_URI environment variable not set or is empty. Cannot connect to MongoDB. Exiting.")
    sys.exit(1)
elif not MONGO_DB_NAME:
    logging.error("❌ MONGO_DB_NAME environment variable not set. Cannot connect to MongoDB. Exiting.")
    sys.exit(1)
else:
    try:
        
        client = MongoClient(
            MONGO_URI,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000 
        )
       
        client.admin.command('ismaster')
        db = client[MONGO_DB_NAME]
        users_collection = db["users"]
        history_collection = db["history"] 
        logging.info("✅ Connected to MongoDB Atlas")
    except Exception as e:
        logging.error(f"❌ MongoDB connection error: {e}. Please check your MONGO_URI, IP Access List, and network connectivity.")
        sys.exit(1)

