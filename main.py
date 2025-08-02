import os
from fastapi import FastAPI, Request, HTTPException, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
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
from db import history_collection, hotels_collection, users_collection
from twilio.twiml.messaging_response import MessagingResponse

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    logging.error("❌ GEMINI_API_KEY not set in .env. AI functionality will be limited.")

llm = None
conversation = None
selected_hotels = {}

if gemini_api_key:
    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            google_api_key=gemini_api_key,
            temperature=0.7
        )
        conversation = ConversationChain(
            llm=llm,
            memory=ConversationBufferMemory(),
            verbose=True
        )
        logging.info("✅ Gemini LLM initialized successfully.")
    except Exception as e:
        logging.error(f"❌ Error initializing Gemini LLM: {e}")
        llm = None
        conversation = None

def build_hotel_prompt(hotel_data):
    hotel_name = hotel_data.get("hotel_name", "Unknown Hotel")
    details = hotel_data.get("details", "No details available.")
    return hotel_name, details

def initialize_conversation(username, hotel_data, past_chats):
    """
    Initializes the AI conversation with a constrained prompt for a specific hotel.
    """
    conversation.memory.clear()
    hotel_name, hotel_details = build_hotel_prompt(hotel_data)
    
    # Define the system prompt to constrain the AI's role
    system_prompt = (
        f"You are a helpful and polite concierge for {hotel_name}. Your sole purpose is to "
        f"assist guests with inquiries related to the hotel, its amenities, and services. "
        f"You have access to the following information about the hotel: {hotel_details}. "
        f"Do not answer any questions unrelated to your role or the hotel. If a user asks "
        f"a non-concierge question, politely state that you can only assist with "
        f"hotel-related inquiries. Keep your responses concise and professional."
    )
    
    # Add the constrained prompt as a user message to guide the AI
    conversation.memory.chat_memory.add_user_message(system_prompt)
    
    # Optionally add a summary of past chats to the context
    if past_chats:
        summary = "\n\n".join([f"User said: {c['user_message']}\nBot replied: {c['bot_response']}" for c in past_chats[-5:]])
        conversation.memory.chat_memory.add_user_message(
            f"Here's a brief summary of past chats with the user:\n{summary}"
        )
    
    # Get the AI's first response based on the new constraints
    initial_ai_response = conversation.predict(input=f"A guest has selected {hotel_name} and is ready to chat.")
    selected_hotels[username] = hotel_name
    
    return initial_ai_response

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

@app.post("/chat")
async def chat(request: Request):
    if conversation is None:
        raise HTTPException(status_code=503, detail="AI service not available.")
    if history_collection is None:
        raise HTTPException(status_code=503, detail="Database history collection not available.")

    try:
        data = await request.json()
        message = data.get("message")
        username = data.get("username", "guest")
        if not message:
            raise HTTPException(status_code=400, detail="No message provided")

        user = users_collection.find_one({"username": username})
        ai_enabled = user.get("ai_enabled", True) if user else True

        if not ai_enabled:
            manual_msg = "The admin will respond to your message shortly."
            history_collection.insert_one({
                "username": username,
                "hotel": selected_hotels.get(username, "N/A"),
                "timestamp": datetime.now(),
                "user_message": message,
                "bot_response": manual_msg
            })
            logging.info(f"[DB] Inserted chat history for manual response.")
            return JSONResponse(content={"response": manual_msg})

        reply = ""
        if username not in selected_hotels:
            hotel_data = hotels_collection.find_one({"hotel_name": {"$regex": f"^{message}$", "$options": "i"}})
            if hotel_data:
                # Initialize conversation with the new, constrained prompt
                reply = initialize_conversation(username, hotel_data, list(history_collection.find({"username": username})))
            else:
                hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
                hotel_names = [h["hotel_name"] for h in hotels]
                hotel_list = "\n- ".join(hotel_names)
                reply = f"Please choose a hotel from the following list:\n- {hotel_list}"

        reset_phrases = ["change hotel", "different hotel", "try another hotel", "switch hotel", "reset hotel", "choose hotel again"]
        if any(phrase in message.lower() for phrase in reset_phrases):
            conversation.memory.clear()
            selected_hotels.pop(username, None)
            
            hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
            hotel_names = [h["hotel_name"] for h in hotels]
            hotel_list = "\n- ".join(hotel_names)
            
            return JSONResponse(content={"response": f"Sure! Please choose a hotel from the following list:\n- {hotel_list}"})
        
        if not reply:
            reply = conversation.predict(input=message)

        history_collection.insert_one({
            "username": username,
            "hotel": selected_hotels.get(username, "N/A"),
            "timestamp": datetime.now(),
            "user_message": message,
            "bot_response": reply
        })

        users_collection.update_one(
            {"username": username},
            {
                "$set": {"last_active": datetime.utcnow()},
                "$setOnInsert": {"ai_enabled": True}
            },
            upsert=True
        )

        return JSONResponse(content={"response": reply})

    except Exception as e:
        logging.error(f"Chat error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    From: str = Form(...),
    Body: str = Form(...)
):
    user_message = Body.strip()
    username = From

    if history_collection is None:
        resp = MessagingResponse()
        resp.message("History system is currently unavailable.")
        return Response(content=str(resp), media_type="text/xml")

    try:
        user = users_collection.find_one({"username": username})
        ai_enabled = user.get("ai_enabled", True) if user else True

        if not ai_enabled:
            manual_msg = "Thank you for your message. The admin will respond to you shortly."
            history_collection.insert_one({
                "username": username,
                "timestamp": datetime.now(),
                "user_message": user_message,
                "bot_response": manual_msg
            })
            resp = MessagingResponse()
            resp.message(manual_msg)
            return Response(content=str(resp), media_type="text/xml")

        if conversation is None:
            resp = MessagingResponse()
            resp.message("AI service is currently unavailable.")
            return Response(content=str(resp), media_type="text/xml")

        bot_reply = ""
        if username not in selected_hotels:
            hotel_data = hotels_collection.find_one({"hotel_name": {"$regex": f"^{user_message}$", "$options": "i"}})
            if hotel_data:
                # Initialize conversation with the new, constrained prompt
                bot_reply = initialize_conversation(username, hotel_data, list(history_collection.find({"username": username})))
                resp = MessagingResponse()
                resp.message(bot_reply)
                return Response(content=str(resp), media_type="text/xml")
            else:
                hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
                hotel_names = [h["hotel_name"] for h in hotels]
                hotel_list = "\n- ".join(hotel_names)
                bot_reply = f"Please choose a hotel from the following list:\n- {hotel_list}"
                resp = MessagingResponse()
                resp.message(bot_reply)
                return Response(content=str(resp), media_type="text/xml")

        reset_phrases = ["change hotel", "different hotel", "try another hotel", "switch hotel", "reset hotel", "choose hotel again"]
        if any(phrase in user_message.lower() for phrase in reset_phrases):
            conversation.memory.clear()
            selected_hotels.pop(username, None)
            
            hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
            hotel_names = [h["hotel_name"] for h in hotels]
            hotel_list = "\n- ".join(hotel_names)
            
            bot_reply = f"Sure! Please choose a hotel from the following list:\n- {hotel_list}"
            resp = MessagingResponse()
            resp.message(bot_reply)
            return Response(content=str(resp), media_type="text/xml")

        if not bot_reply:
            bot_reply = conversation.predict(input=user_message)

        history_collection.insert_one({
            "username": username,
            "hotel": selected_hotels.get(username, "N/A"),
            "timestamp": datetime.now(),
            "user_message": user_message,
            "bot_response": bot_reply
        })

        users_collection.update_one(
            {"username": username},
            {
                "$set": {"last_active": datetime.utcnow()},
                "$setOnInsert": {"ai_enabled": True}
            },
            upsert=True
        )

        resp = MessagingResponse()
        resp.message(bot_reply)
        return Response(content=str(resp), media_type="text/xml")

    except Exception as e:
        logging.error(f"WhatsApp message error: {e}")
        resp = MessagingResponse()
        resp.message("An error occurred. Please try again later.")
        return Response(content=str(resp), media_type="text/xml")
