import jwt
from rest_framework.throttling import SimpleRateThrottle

from helpers.utils import decode_jwt_token


_NOT_SET = object()


def _authenticated_user_id(request):
    """User id of a caller with a valid token, else None.

    Views behind user_authentication_required already decoded and verified
    the token and left request.user_id on the request (None for a guest or a
    stale token on a public endpoint). Views without that decorator decode it
    here; only a token with a valid signature counts, so a client can't pick
    someone else's id to dodge or burn their limit.
    """
    user_id = getattr(request, "user_id", _NOT_SET)

    if user_id is not _NOT_SET:
        return user_id

    header = request.headers.get("Authorization")

    if not header:
        return None

    try:
        return decode_jwt_token(header).get("user_id")
    except (ValueError, jwt.InvalidTokenError):
        return None


class JWTUserOrIPRateThrottle(SimpleRateThrottle):
    """Default throttle for every API view.

    DRF's own UserRateThrottle can't be used: auth is the custom JWT
    decorator, so DRF's request.user is always anonymous and every caller
    would be keyed by IP. Here a logged-in customer gets their own bucket
    ("user" rate), so many customers behind one shared IP (office Wi-Fi,
    mobile carrier NAT) no longer exhaust a single limit. Guests are still
    keyed by IP ("anon" rate).

    Views that set their own throttle_classes (login, OTP, help desk, custom
    requests) are unaffected.
    """

    scope = "anon"

    def get_cache_key(self, request, view):
        user_id = _authenticated_user_id(request)

        if user_id is not None:
            self.scope = "user"
            ident = f"user-{user_id}"
        else:
            self.scope = "anon"
            ident = self.get_ident(request)

        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)

        return self.cache_format % {"scope": self.scope, "ident": ident}
