import os
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from bson.objectid import ObjectId
from db import users_collection

router = APIRouter()

class AuthForm(BaseModel):
    username: str
    password: str

@router.post("/signup")
def signup(username: str = Form(...), password: str = Form(...)):
    try:
        # Check if user already exists
        existing_user = users_collection.find_one({"username": username})
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists")
            return RedirectResponse(url="/login", status_code=303)

        # Insert new user
        users_collection.insert_one({
            "username": username,
            "password": password  # For production, always hash passwords!
        })

        return RedirectResponse(url="/chat", status_code=303)

    except Exception as e:
        print("Signup error:", e)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    try:
        # Look for a matching username + password
        user = users_collection.find_one({"username": username, "password": password})
        if user:
            return RedirectResponse(url=f"/chat?username={username}", status_code=303)
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    except Exception as e:
        print("Login error:", e)
        raise HTTPException(status_code=500, detail="Internal server error")


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme)):
    # Dummy user for now
    return {"username": "guest"}
