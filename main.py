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
        return RedirectResponse(url="/login", status_code=303)

    if history_collection is None:
        raise HTTPException(status_code=503, detail="Database history collection not available.")

    try:
        raw_history = list(history_collection.find({"username": username}))
        sorted_history = sorted(raw_history, key=lambda x: x.get("timestamp", datetime.min), reverse=True)

        for entry in sorted_history:
            if isinstance(entry.get("timestamp"), datetime):
                entry["timestamp_str"] = entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            else:
                entry["timestamp_str"] = "N/A"

        return templates.TemplateResponse("history.html", {"request": request, "username": username, "history": sorted_history})
    except Exception as e:
        logging.error(f"Error fetching chat history for {username}: {e}")
        return JSONResponse(status_code=500, content={"error": "Could not retrieve chat history."})

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
                "timestamp": datetime.now(),
                "user_message": message,
                "bot_response": manual_msg
            })
            return JSONResponse(content={"response": manual_msg})

        if username not in selected_hotels:
            hotel_data = hotels_collection.find_one({"hotel_name": {"$regex": f"^{message}$", "$options": "i"}})
            if hotel_data:
                conversation.memory.clear()
                hotel_name, prompt = build_hotel_prompt(hotel_data)
                conversation.memory.chat_memory.add_ai_message(prompt)

                past_chats = list(history_collection.find({"username": username}))
                if past_chats:
                    summary = "\n\n".join([f"User said: {c['user_message']}\nBot replied: {c['bot_response']}" for c in past_chats[-5:]])
                    conversation.memory.chat_memory.add_user_message(
                        f"Here's a brief summary of past chats with the user:\n{summary}"
                    )

                selected_hotels[username] = hotel_name
                return JSONResponse(content={"response": f"You're now chatting with {hotel_name} concierge. How can I help you?"})
            else:
                hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
                hotel_names = [h["hotel_name"] for h in hotels]
                hotel_list = "\n- ".join(hotel_names)
                return JSONResponse(content={"response": f"Please choose a hotel from the following list:\n- {hotel_list}"})

        reset_phrases = ["change hotel", "different hotel", "try another hotel", "switch hotel", "reset hotel", "choose hotel again"]
        if any(phrase in message.lower() for phrase in reset_phrases):
            conversation.memory.clear()
            selected_hotels.pop(username, None)
            
            hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
            hotel_names = [h["hotel_name"] for h in hotels]
            hotel_list = "\n- ".join(hotel_names)
            
            return JSONResponse(content={"response": f"Sure! Please choose a hotel from the following list:\n- {hotel_list}"})
        
        reply = conversation.predict(input=message)

        history_collection.insert_one({
            "username": username,
            "hotel": selected_hotels[username],
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

        if username not in selected_hotels:
            hotel_data = hotels_collection.find_one({"name": {"$regex": f"^{user_message}$", "$options": "i"}})
            if hotel_data:
                conversation.memory.clear()
                hotel_name, prompt = build_hotel_prompt(hotel_data)
                conversation.memory.chat_memory.add_ai_message(prompt)

                past_chats = list(history_collection.find({"username": username}))
                if past_chats:
                    summary = "\n\n".join([f"User said: {c['user_message']}\nBot replied: {c['bot_response']}" for c in past_chats[-5:]])
                    conversation.memory.chat_memory.add_user_message(
                        f"Here's a brief summary of past chats with the user:\n{summary}"
                    )

                selected_hotels[username] = hotel_name
                resp = MessagingResponse()
                resp.message(f"You're now chatting with {hotel_name} concierge. How can I help you?")
                return Response(content=str(resp), media_type="text/xml")
            else:
                hotels = list(hotels_collection.find({}, {"name": 1}))
                hotel_names = [h["hotel_name"] for h in hotels]
                hotel_list = "\n- ".join(hotel_names)
                resp = MessagingResponse()
                resp.message(f"Please choose a hotel from the following list:\n- {hotel_list}")
                return Response(content=str(resp), media_type="text/xml")

        reset_phrases = ["change hotel", "different hotel", "try another hotel", "switch hotel", "reset hotel", "choose hotel again"]
        if any(phrase in user_message.lower() for phrase in reset_phrases):
            conversation.memory.clear()
            selected_hotels.pop(username, None)
            
            hotels = list(hotels_collection.find({}, {"hotel_name": 1}))
            hotel_names = [h["hotel_name"] for h in hotels]
            hotel_list = "\n- ".join(hotel_names)
            
            resp = MessagingResponse()
            resp.message(f"Sure! Please choose a hotel from the following list:\n- {hotel_list}")
            return Response(content=str(resp), media_type="text/xml")

        
        bot_reply = conversation.predict(input=user_message)

        history_collection.insert_one({
            "username": username,
            "hotel": selected_hotels[username],
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
