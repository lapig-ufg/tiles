"""
Authentication middleware for TVI cache management endpoints
"""
import hashlib
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.core.mongodb import get_users_collection
from app.core.config import logger

security = HTTPBasic()

async def verify_super_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """
    Verify if user has super-admin role
    """
    try:
        users_collection = await get_users_collection()
        
        # Find users by username (pode haver múltiplos)
        users_cursor = users_collection.find({"username": credentials.username})
        users_list = await users_cursor.to_list(length=None)
        
        if not users_list:
            # Debug: log available usernames to help diagnose the issue
            all_users_cursor = users_collection.find({}, {"username": 1})
            all_users = await all_users_cursor.to_list(length=10)  # Limit to 10 for safety
            available_usernames = [u.get("username") for u in all_users if u.get("username")]
            logger.debug(f"Available usernames in database: {available_usernames}")
            
            logger.warning(f"Authentication failed: user {credentials.username} not found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Tentar autenticar com cada usuário encontrado
        authenticated_user = None
        for user in users_list:
            stored_password = user.get("password", "")
            
            # Try direct comparison first (for plain text passwords)
            password_valid = stored_password == credentials.password
            
            # If direct comparison fails, try SHA256 hash
            if not password_valid:
                hashed_password = hashlib.sha256(credentials.password.encode()).hexdigest()
                password_valid = stored_password == hashed_password
            
            if password_valid:
                authenticated_user = user
                break
        
        if not authenticated_user:
            logger.warning(f"Authentication failed: invalid password for user {credentials.username}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Check role
        user_role = authenticated_user.get("role")
        user_type = authenticated_user.get("type")
        
        if user_role != "super-admin" and user_type != "admin":
            logger.warning(f"Authorization failed: user {credentials.username} does not have super-admin role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super-admin role required",
            )
        
        return authenticated_user
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error during authentication: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service error"
        )

# Dependency for protected endpoints
SuperAdminRequired = Depends(verify_super_admin)