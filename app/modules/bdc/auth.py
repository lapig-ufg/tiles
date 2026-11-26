import hmac
import os
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


def service_token():
    token = os.environ.get('BDC_SERVICE_TOKEN')
    if token is not None:
        return token
    path = os.environ.get('BDC_SERVICE_TOKEN_FILE')
    if path:
        try:
            value = Path(path).read_text()
            return value.strip() if len(value) <= 4096 else None
        except (OSError, UnicodeError):
            return None
    return None


async def verify_bdc_service(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None:
        raise HTTPException(401, 'Credencial de serviço BDC obrigatória', headers={'WWW-Authenticate': 'Bearer'})
    expected = service_token()
    if not isinstance(expected, str) or len(expected) < 32 or any(char.isspace() for char in expected):
        raise HTTPException(503, {'code': 'bdc_service_auth_unconfigured'})
    if not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(401, 'Credencial de serviço BDC inválida', headers={'WWW-Authenticate': 'Bearer'})
    return 'tvi-bdc'
