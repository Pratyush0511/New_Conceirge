import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from pymongo import MongoClient
from auth import router as auth_router
from langchain.chains import ConversationChain
from langchain.memory import ConversationBufferMemory
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

app = FastAPI()

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="static")

# Include auth routes
app.include_router(auth_router)

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Gemini Flash 2.0 (gemini-1.5-flash)
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    raise RuntimeError("GEMINI_API_KEY not set in .env")

llm = ChatGoogleGenerativeAI(
    model="models/gemini-1.5-flash",
    google_api_key=gemini_api_key,
    temperature=0.7
)

conversation = ConversationChain(
    llm=llm,
    memory=ConversationBufferMemory(),
    verbose=True
)

# System prompt
conversation.memory.chat_memory.add_ai_message(
    "You are a polite, professional hotel concierge for **The Grand Horizon Hotel**, "
    "a 5-star luxury hotel located in Mumbai. Help guests with check-in/check-out info, "
    "restaurant hours, spa bookings, transport arrangements, sightseeing suggestions, and more. "
    "The hotel offers: Deluxe Rooms, Presidential Suites, Rooftop Dining, 24x7 Room Service, "
    "Free Wi-Fi, Airport Pickup, and a Wellness Spa. Check-in is 2 PM, check-out is 11 AM. "
    "Address: Marine Drive, Mumbai, Maharashtra. Phone: +91-9876543210."
)

# MongoDB connection
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI not set in .env")

client = MongoClient(MONGO_URI)
db = client["chatbot_db"]
users_collection = db["users"]

# Routes
@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    username = request.query_params.get("username")
    return templates.TemplateResponse("chat.html", {"request": request, "username": username})

@app.post("/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        message = data.get("message")
        if not message:
            raise HTTPException(status_code=400, detail="No message provided")

        reply = conversation.predict(input=message)
        return JSONResponse(content={"response": reply})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
