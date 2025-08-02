import os
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from bson.objectid import ObjectId
from db import users_collection

router = APIRouter()

@router.post("/signup")
async def signup(username: str = Form(...), password: str = Form(...)):
    if users_collection is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not connected.")

    try:
        existing_user = users_collection.find_one({"username": username})
        if existing_user:
            return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

        users_collection.insert_one({
            "username": username,
            "password": password
        })

        return RedirectResponse(url=f"/chat?username={username}", status_code=status.HTTP_303_SEE_OTHER)
        
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Signup error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error during signup")


@router.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if users_collection is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not connected.")

    try:
        user = users_collection.find_one({"username": username, "password": password})
        if user:
            return RedirectResponse(url=f"/chat?username={username}", status_code=status.HTTP_303_SEE_OTHER)
        else:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Login error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error during login")


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    return {"username": "guest"}
