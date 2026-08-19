import json
import uuid
from typing import Optional
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.config import settings
from app.schemas.models import UserContext

security = HTTPBearer()

# Yerel Hızlı Kullanıcı Rol Haritası (Fallback & Local Auth)
LOCAL_USER_METADATA = {
    "admin": {
        "role": "super_admin",
        "department": "genel",
        "clearance_level": 100,
        "email": "admin@northwestern.edu"
    },
    "staff": {
        "role": "user-genel",
        "department": "genel",
        "clearance_level": 10,
        "email": "staff@northwestern.edu"
    },
    "ik_admin": {
        "role": "admin-ik",
        "department": "ik",
        "clearance_level": 50,
        "email": "ik_admin@northwestern.edu"
    },
    "hukuk_admin": {
        "role": "admin-hukuk",
        "department": "hukuk",
        "clearance_level": 50,
        "email": "hukuk_admin@northwestern.edu"
    },
    "finans_admin": {
        "role": "admin-finans",
        "department": "finans",
        "clearance_level": 50,
        "email": "finans_admin@northwestern.edu"
    }
}

def create_jwt_token(username: str, metadata: dict) -> str:
    """Kullanıcı için GoTrue uyumlu app_metadata içeren JWT üretir."""
    payload = {
        "sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, username)),
        "email": metadata.get("email", f"{username}@northwestern.edu"),
        "role": "authenticated",
        "app_metadata": {
            "role": metadata.get("role", "user-genel"),
            "department": metadata.get("department", "genel"),
            "clearance_level": metadata.get("clearance_level", 10),
            "username": username
        }
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)) -> UserContext:
    """Gelen Bearer Token'ı doğrular ve UserContext modeline dönüştürür."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
        app_meta = payload.get("app_metadata", {})
        
        # Standart veya GoTrue JWT yapısına uyum
        user_id = str(payload.get("sub") or uuid.uuid4())
        email = str(payload.get("email") or "user@northwestern.edu")
        role = str(app_meta.get("role") or payload.get("role") or "user-genel")
        department = str(app_meta.get("department") or "genel")
        clearance = int(app_meta.get("clearance_level") or 10)
        
        return UserContext(
            user_id=user_id,
            email=email,
            role=role,
            department=department,
            clearance_level=clearance
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Geçersiz veya süresi dolmuş token: {str(e)}")

def require_super_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if user.role != "super_admin" and user.clearance_level < 100:
        raise HTTPException(status_code=403, detail="Bu işlem için 'super_admin' yetkisi gereklidir.")
    return user

def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if "admin" not in user.role and user.clearance_level < 50:
        raise HTTPException(status_code=403, detail="Bu işlem için Admin yetkisi gereklidir.")
    return user
