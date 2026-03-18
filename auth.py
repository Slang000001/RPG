"""JWT authentication — verifies tokens against the shared Sparkwright Supabase project."""

import os
import jwt
from jwt import PyJWKClient
from flask import request, jsonify

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET')

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None and SUPABASE_URL:
        jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def get_authenticated_user():
    """Verify JWT and return (user_id, email).
    Tries ES256 (JWKS) first, falls back to HS256."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise AuthError('Missing authorization', 401)

    token = auth_header.replace('Bearer ', '')

    # ES256 via JWKS
    jwks_client = _get_jwks_client()
    if jwks_client:
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key,
                algorithms=['ES256'], audience='authenticated'
            )
            return _extract_user(payload)
        except jwt.ExpiredSignatureError:
            raise AuthError('Token expired', 401)
        except (jwt.InvalidTokenError, Exception) as e:
            print(f"ES256 failed, trying HS256: {e}")

    # HS256 fallback
    if SUPABASE_JWT_SECRET:
        try:
            payload = jwt.decode(
                token, SUPABASE_JWT_SECRET,
                algorithms=['HS256'], audience='authenticated'
            )
            return _extract_user(payload)
        except jwt.ExpiredSignatureError:
            raise AuthError('Token expired', 401)
        except jwt.InvalidTokenError:
            raise AuthError('Invalid token', 401)

    raise AuthError('Server authentication not configured', 500)


def _extract_user(payload):
    user_id = payload.get('sub')
    email = payload.get('email')
    if not user_id:
        raise AuthError('Invalid user', 401)
    return user_id, email


class AuthError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def to_response(self):
        return jsonify({'success': False, 'error': self.message}), self.status_code
