"""
Provides various authentication policies.
"""
import base64
import binascii

from asgiref.sync import sync_to_async
from django.contrib.auth import aauthenticate, authenticate, get_user_model
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.translation import gettext_lazy as _

from rest_framework import HTTP_HEADER_ENCODING, exceptions
from rest_framework.utils.asyncio import overrides_sync_only


def get_authorization_header(request):
    """
    Return request's 'Authorization:' header, as a bytestring.

    Hide some test client ickyness where the header can be unicode.
    """
    auth = request.headers.get('authorization', b'')
    if isinstance(auth, str):
        # Work around django test client oddness
        auth = auth.encode(HTTP_HEADER_ENCODING)
    return auth


class CSRFCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        # Return the failure reason instead of an HttpResponse
        return reason


class BaseAuthentication:
    """
    All authentication classes should extend BaseAuthentication.
    """

    def authenticate(self, request):
        """
        Authenticate the request and return a two-tuple of (user, token).
        """
        raise NotImplementedError(".authenticate() must be overridden.")

    async def aauthenticate(self, request):
        """
        Asynchronous counterpart of `authenticate()`, used by async views.

        The default implementation runs `authenticate()` in a thread, so that
        existing synchronous authentication classes keep working when used
        from async views. Override this method to provide a native
        asynchronous implementation.

        The native implementations of the built-in authentication classes
        fall back to this behavior when a subclass overrides one of the
        synchronous methods without also overriding its asynchronous
        counterpart, so that the customization isn't bypassed.
        """
        return await sync_to_async(self.authenticate)(request)

    def authenticate_header(self, request):
        """
        Return a string to be used as the value of the `WWW-Authenticate`
        header in a `401 Unauthenticated` response, or `None` if the
        authentication scheme should return `403 Permission Denied` responses.
        """
        pass


class BasicAuthentication(BaseAuthentication):
    """
    HTTP Basic authentication against username/password.
    """
    www_authenticate_realm = 'api'

    def authenticate(self, request):
        """
        Returns a `User` if a correct username and password have been supplied
        using HTTP Basic authentication.  Otherwise returns `None`.
        """
        credentials = self._get_credentials(request)
        if credentials is None:
            return None
        userid, password = credentials
        return self.authenticate_credentials(userid, password, request)

    async def aauthenticate(self, request):
        if overrides_sync_only(self, BasicAuthentication, 'authenticate', 'authenticate_credentials'):
            return await sync_to_async(self.authenticate)(request)

        credentials = self._get_credentials(request)
        if credentials is None:
            return None
        userid, password = credentials
        return await self.aauthenticate_credentials(userid, password, request)

    def _get_credentials(self, request):
        """
        Parse the `Authorization` header, returning a `(userid, password)`
        two-tuple, or `None` if basic authentication was not attempted.
        """
        auth = get_authorization_header(request).split()

        if not auth or auth[0].lower() != b'basic':
            return None

        if len(auth) == 1:
            msg = _('Invalid basic header. No credentials provided.')
            raise exceptions.AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = _('Invalid basic header. Credentials string should not contain spaces.')
            raise exceptions.AuthenticationFailed(msg)

        try:
            try:
                auth_decoded = base64.b64decode(auth[1]).decode('utf-8')
            except UnicodeDecodeError:
                auth_decoded = base64.b64decode(auth[1]).decode('latin-1')

            userid, password = auth_decoded.split(':', 1)
        except (TypeError, ValueError, UnicodeDecodeError, binascii.Error):
            msg = _('Invalid basic header. Credentials not correctly base64 encoded.')
            raise exceptions.AuthenticationFailed(msg)

        return userid, password

    def _get_django_credentials(self, userid, password):
        return {
            get_user_model().USERNAME_FIELD: userid,
            'password': password
        }

    def _validate_user(self, user):
        if user is None:
            raise exceptions.AuthenticationFailed(_('Invalid username/password.'))

        if not user.is_active:
            raise exceptions.AuthenticationFailed(_('User inactive or deleted.'))

        return (user, None)

    def authenticate_credentials(self, userid, password, request=None):
        """
        Authenticate the userid and password against username and password
        with optional request for context.
        """
        credentials = self._get_django_credentials(userid, password)
        user = authenticate(request=request, **credentials)
        return self._validate_user(user)

    async def aauthenticate_credentials(self, userid, password, request=None):
        """
        Asynchronous counterpart of `authenticate_credentials()`.
        """
        credentials = self._get_django_credentials(userid, password)
        user = await aauthenticate(request=request, **credentials)
        return self._validate_user(user)

    def authenticate_header(self, request):
        return 'Basic realm="%s"' % self.www_authenticate_realm


class SessionAuthentication(BaseAuthentication):
    """
    Use Django's session framework for authentication.
    """

    def authenticate(self, request):
        """
        Returns a `User` if the request session currently has a logged in user.
        Otherwise returns `None`.
        """

        # Get the session-based user from the underlying HttpRequest object
        user = getattr(request._request, 'user', None)

        return self._authenticate_user(request, user)

    async def aauthenticate(self, request):
        """
        Asynchronous counterpart of `authenticate()`.

        Uses `HttpRequest.auser()`, as provided by Django's
        `AuthenticationMiddleware`, to avoid blocking database access from
        the event loop.
        """
        if overrides_sync_only(self, SessionAuthentication, 'authenticate'):
            return await sync_to_async(self.authenticate)(request)

        if hasattr(request._request, '_cached_user'):
            # `request.user` has already been evaluated, typically by
            # middleware. Django caches the result of `request.user` and
            # `request.auser()` separately, so reuse it to avoid a query.
            user = request._request._cached_user
        else:
            auser = getattr(request._request, 'auser', None)
            if auser is not None:
                user = await auser()
            else:
                user = getattr(request._request, 'user', None)

        return self._authenticate_user(request, user)

    def _authenticate_user(self, request, user):
        # Unauthenticated, CSRF validation not required
        if not user or not user.is_active:
            return None

        self.enforce_csrf(request)

        # CSRF passed with authenticated user
        return (user, None)

    def enforce_csrf(self, request):
        """
        Enforce CSRF validation for session based authentication.
        """
        def dummy_get_response(request):  # pragma: no cover
            return None

        check = CSRFCheck(dummy_get_response)
        # populates request.META['CSRF_COOKIE'], which is used in process_view()
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            # CSRF failed, bail with explicit error message
            raise exceptions.PermissionDenied('CSRF Failed: %s' % reason)


class TokenAuthentication(BaseAuthentication):
    """
    Simple token based authentication.

    Clients should authenticate by passing the token key in the "Authorization"
    HTTP header, prepended with the string "Token ".  For example:

        Authorization: Token 401f7ac837da42b97f613d789819ff93537bee6a
    """

    keyword = 'Token'
    model = None

    def get_model(self):
        if self.model is not None:
            return self.model
        from rest_framework.authtoken.models import Token
        return Token

    """
    A custom token model may be used, but must have the following properties.

    * key -- The string identifying the token
    * user -- The user to which the token belongs
    """

    def authenticate(self, request):
        token = self._get_token(request)
        if token is None:
            return None
        return self.authenticate_credentials(token)

    async def aauthenticate(self, request):
        if overrides_sync_only(self, TokenAuthentication, 'authenticate', 'authenticate_credentials'):
            return await sync_to_async(self.authenticate)(request)

        token = self._get_token(request)
        if token is None:
            return None
        return await self.aauthenticate_credentials(token)

    def _get_token(self, request):
        """
        Parse the `Authorization` header, returning the token key, or `None`
        if token authentication was not attempted.
        """
        auth = get_authorization_header(request).split()

        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None

        if len(auth) == 1:
            msg = _('Invalid token header. No credentials provided.')
            raise exceptions.AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = _('Invalid token header. Token string should not contain spaces.')
            raise exceptions.AuthenticationFailed(msg)

        try:
            return auth[1].decode()
        except UnicodeError:
            msg = _('Invalid token header. Token string should not contain invalid characters.')
            raise exceptions.AuthenticationFailed(msg)

    def _validate_token(self, token):
        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(_('User inactive or deleted.'))

        return (token.user, token)

    def authenticate_credentials(self, key):
        model = self.get_model()
        try:
            token = model.objects.select_related('user').get(key=key)
        except model.DoesNotExist:
            raise exceptions.AuthenticationFailed(_('Invalid token.'))

        return self._validate_token(token)

    async def aauthenticate_credentials(self, key):
        """
        Asynchronous counterpart of `authenticate_credentials()`.
        """
        model = self.get_model()
        try:
            token = await model.objects.select_related('user').aget(key=key)
        except model.DoesNotExist:
            raise exceptions.AuthenticationFailed(_('Invalid token.'))

        return self._validate_token(token)

    def authenticate_header(self, request):
        return self.keyword


class RemoteUserAuthentication(BaseAuthentication):
    """
    REMOTE_USER authentication.

    To use this, set up your web server to perform authentication, which will
    set the REMOTE_USER environment variable. You will need to have
    'django.contrib.auth.backends.RemoteUserBackend in your
    AUTHENTICATION_BACKENDS setting
    """

    # Name of request header to grab username from.  This will be the key as
    # used in the request.META dictionary, i.e. the normalization of headers to
    # all uppercase and the addition of "HTTP_" prefix apply.
    header = "REMOTE_USER"

    def authenticate(self, request):
        user = authenticate(request=request, remote_user=request.META.get(self.header))
        if user and user.is_active:
            return (user, None)

    async def aauthenticate(self, request):
        if overrides_sync_only(self, RemoteUserAuthentication, 'authenticate'):
            return await sync_to_async(self.authenticate)(request)

        user = await aauthenticate(request=request, remote_user=request.META.get(self.header))
        if user and user.is_active:
            return (user, None)
