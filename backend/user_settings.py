from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from firebase_admin import auth
from .auth_middleware import get_current_user

router = APIRouter()

class ProfileUpdateReq(BaseModel):
    password: str = None
    phone: str = None

@router.put("/api/settings/profile")
def update_profile(req: ProfileUpdateReq, user: dict = Depends(get_current_user)):
    try:
        update_data = {}
        if req.password:
            update_data["password"] = req.password
        if req.phone:
            update_data["phone_number"] = req.phone
            
        if update_data:
            auth.update_user(user["uid"], **update_data)
        
        return {"status": "success", "message": "Profile updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
