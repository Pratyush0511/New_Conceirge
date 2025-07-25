import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from auth import router as auth_router
from langchain.chains import ConversationChain
from langchain.memory import ConversationBufferMemory
from langchain_google_genai import ChatGoogleGenerativeAI
import logging
from datetime import datetime
from db import history_collection 


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
    allow_origins=["*"], # Consider restricting this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    logging.error("❌ GEMINI_API_KEY not set in .env. AI functionality will be limited.")

llm = None
conversation = None

if gemini_api_key:
    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash", # Use the correct model string
            google_api_key=gemini_api_key,
            temperature=0.7
        )
        conversation = ConversationChain(
            llm=llm,
            memory=ConversationBufferMemory(),
            verbose=True
        )
        # System prompt: Enforces strict adherence to hotel-related questions
        conversation.memory.chat_memory.add_ai_message("""
        You are a polite, professional, and highly specialized hotel concierge for **The Grand Horizon Hotel**, a 5-star luxury hotel located in Mumbai. Your sole purpose is to assist guests exclusively with inquiries directly related to the hotel and its services.
        
        **Hotel Information:**
        * **Name:** The Grand Horizon Hotel  
        * **Star Rating:** 5-star luxury  
        * **Location:** Marine Drive, Mumbai, Maharashtra  
        * **Phone:** +91-9876543210  
        * **Services:** Check-in/check-out info, restaurant hours, spa bookings, transport arrangements (including **cab booking**), **local weather inquiries**, **nearby sightseeing suggestions**, room amenities, and general hotel information.  
        * **Offerings:** Deluxe Rooms, Presidential Suites, Rooftop Dining, 24x7 Room Service, Free Wi-Fi, Airport Pickup, Wellness Spa.  
        * **Timings:** Check-in is 2 PM, check-out is 11 AM.  
        * **Wi-Fi Details:** The Wi-Fi network name is 'GrandHorizonGuest' and the password is 'HorizonStay2025'.  
        * **Dining Hours (Rooftop Dining):**  
            * **Breakfast:** 7:00 AM - 10:30 AM  
            * **Lunch:** 12:30 PM - 3:00 PM  
            * **Dinner:** 7:00 PM - 11:00 PM  
        
        **Strict Instruction:**  
        You MUST only answer questions that fall within the scope of a hotel concierge for The Grand Horizon Hotel, specifically covering the details provided above. If a user asks about *anything* else (e.g., personal opinions, general knowledge, news, other businesses, politics, or any topic unrelated to the hotel's services), you must respond with the following exact phrase and nothing more:
        
        "I'm sorry, I can only assist with inquiries related to The Grand Horizon Hotel and its services. How can I help you with your stay today?"
        """)

        logging.info("✅ Gemini LLM initialized successfully.")
    except Exception as e:
        logging.error(f"❌ Error initializing Gemini LLM: {e}")
        llm = None # Ensure llm is None if initialization fails
        conversation = None


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
    if conversation is None:
        raise HTTPException(status_code=503, detail="AI service not available.")
    if history_collection is None: # New: Check if history_collection is available
        raise HTTPException(status_code=503, detail="Database history collection not available.")

    try:
        data = await request.json()
        message = data.get("message")
        username = data.get("username", "guest") # Get username from request body, default to 'guest'
        if not message:
            raise HTTPException(status_code=400, detail="No message provided")

        reply = conversation.predict(input=message)

        # New: Save chat interaction to history collection
        timestamp = datetime.now()
        history_collection.insert_one({
            "username": username,
            "timestamp": timestamp,
            "user_message": message,
            "bot_response": reply
        })
        logging.info(f"Chat saved for user: {username}")

        return JSONResponse(content={"response": reply})

    except HTTPException as e:
        raise e # Re-raise FastAPI HTTPExceptions directly
    except Exception as e:
        import traceback
        traceback.print_exc()
        logging.error(f"Chat processing error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# New: Route for chat history page
@app.get("/history", response_class=HTMLResponse)
async def chat_history_page(request: Request):
    username = request.query_params.get("username")
    if not username:
        # Redirect to login if username is not provided (user not logged in)
        return RedirectResponse(url="/login", status_code=303)

    # Ensure history_collection is initialized before use
    from db import history_collection # Re-import to ensure it's the latest state
    if history_collection is None:
        raise HTTPException(status_code=503, detail="Database history collection not available.")

    try:
        # Fetch chat history for the specific user, sorted by timestamp
        raw_history = list(history_collection.find({"username": username}))

        # Sort history by timestamp in descending order (most recent first)
        sorted_history = sorted(raw_history, key=lambda x: x.get("timestamp", datetime.min), reverse=True)

        # Format timestamp for display
        for entry in sorted_history:
            if isinstance(entry.get("timestamp"), datetime):
                entry["timestamp_str"] = entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            else:
                entry["timestamp_str"] = "N/A" # Handle cases where timestamp might be missing or not datetime object

        return templates.TemplateResponse("history.html", {"request": request, "username": username, "history": sorted_history})
    except Exception as e:
        logging.error(f"Error fetching chat history for {username}: {e}")
        return JSONResponse(status_code=500, content={"error": "Could not retrieve chat history."})
    
@app.get("/chat/history")
async def get_chat_history(request: Request):
    username = request.query_params.get("username")
    if not username:
        return JSONResponse(status_code=400, content={"error": "Username is required"})

    try:
        raw_history = list(history_collection.find({"username": username}).sort("timestamp", 1))  # Sort oldest to newest

        formatted_history = [
            {
                "user_message": entry.get("user_message", ""),
                "bot_response": entry.get("bot_response", ""),
                "timestamp": entry.get("timestamp").strftime("%Y-%m-%d %H:%M:%S") if entry.get("timestamp") else "N/A"
            }
            for entry in raw_history
        ]
        return JSONResponse(content={"history": formatted_history})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
