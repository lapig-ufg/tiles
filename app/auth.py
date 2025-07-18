"""
Authentication middleware for TVI cache management endpoints
"""
import hashlib
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.mongodb import get_users_collection
from app.config import logger

security = HTTPBasic()

async def verify_super_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """
    Verify if user has super-admin role
    """
    try:
        users_collection = await get_users_collection()
        
        # Find user by username
        user = await users_collection.find_one({"username": credentials.username})
        
        if not user:
            # Try by _id for admin user
            user = await users_collection.find_one({"_id": credentials.username})
        
        if not user:
            logger.warning(f"Authentication failed: user {credentials.username} not found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Verify password (assuming it's hashed with SHA256 or plain text)
        stored_password = user.get("password", "")
        
        # Try direct comparison first (for plain text passwords)
        password_valid = stored_password == credentials.password
        
        # If direct comparison fails, try SHA256 hash
        if not password_valid:
            hashed_password = hashlib.sha256(credentials.password.encode()).hexdigest()
            password_valid = stored_password == hashed_password
        
        if not password_valid:
            logger.warning(f"Authentication failed: invalid password for user {credentials.username}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Check role
        user_role = user.get("role")
        user_type = user.get("type")
        
        if user_role != "super-admin" and user_type != "admin":
            logger.warning(f"Authorization failed: user {credentials.username} does not have super-admin role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super-admin role required",
            )
        
        logger.info(f"User {credentials.username} authenticated successfully with role {user_role or user_type}")
        return user
        
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