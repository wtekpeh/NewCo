import requests
from jose import jwt
from django.conf import settings
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed
from accounts.models import StaffProfile


class KeycloakAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return None

        try:
            prefix, token = auth_header.split()
            if prefix.lower() != "bearer":
                raise AuthenticationFailed("Invalid token prefix")
        except ValueError:
            raise AuthenticationFailed("Invalid Authorization header format")

        keycloak_config = settings.KEYCLOAK_CONFIG

        certs_url = (
            f"{keycloak_config['SERVER_URL']}/realms/"
            f"{keycloak_config['REALM']}/protocol/openid-connect/certs"
        )

        response = requests.get(certs_url)
        jwks = response.json()

        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            raise AuthenticationFailed("Invalid token header")

        rsa_key = {}
        for key in jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"],
                }

        if not rsa_key:
            raise AuthenticationFailed("Unable to find matching key")

        try:
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=["RS256"],
                audience=keycloak_config["CLIENT_ID"],
                issuer=f"{keycloak_config['SERVER_URL']}/realms/{keycloak_config['REALM']}",
            )
        except Exception as e:
            raise AuthenticationFailed(f"Token validation failed: {str(e)}")

        keycloak_sub = payload.get("sub")
        if not keycloak_sub:
            raise AuthenticationFailed("Token missing subject")

        email = payload.get("email", "")
        username = payload.get("preferred_username", "")
        full_name = payload.get("name", "")

        staff_profile, _ = StaffProfile.objects.get_or_create(
            keycloak_sub=keycloak_sub,
            defaults={
                "email": email,
                "username": username,
                "full_name": full_name,
            },
        )

        updated = False

        if email and staff_profile.email != email:
            staff_profile.email = email
            updated = True

        if username and staff_profile.username != username:
            staff_profile.username = username
            updated = True

        if full_name and staff_profile.full_name != full_name:
            staff_profile.full_name = full_name
            updated = True

        if updated:
            staff_profile.save(
                update_fields=["email", "username", "full_name", "updated_at"]
            )

        if not staff_profile.is_active:
            raise AuthenticationFailed("User is inactive")

        return (staff_profile, token)
