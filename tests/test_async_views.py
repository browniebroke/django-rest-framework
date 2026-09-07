"""
Tests for asynchronous support in APIView, function based views, and the
authentication, permission, throttling and metadata policies.
"""
import base64

from asgiref.sync import sync_to_async
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.core.exceptions import (
    ImproperlyConfigured, SynchronousOnlyOperation
)
from django.test import TestCase, override_settings
from django.urls import path

from rest_framework import permissions, status
from rest_framework.authentication import (
    BaseAuthentication, BasicAuthentication, SessionAuthentication,
    TokenAuthentication
)
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import BasePermission
from rest_framework.renderers import AdminRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.serializers import ModelSerializer
from rest_framework.test import (
    APIClient, APIRequestFactory, AsyncAPIClient, AsyncAPIRequestFactory
)
from rest_framework.throttling import (
    AnonRateThrottle, BaseThrottle, ScopedRateThrottle, UserRateThrottle
)
from rest_framework.views import APIView
from tests.models import BasicModel

# Views
# -----


class AsyncView(APIView):
    permission_classes = [permissions.AllowAny]

    async def get(self, request, *args, **kwargs):
        return Response({'method': 'GET', 'user': str(await request.auser())})

    async def post(self, request, *args, **kwargs):
        return Response({'method': 'POST', 'data': request.data})


class MixedView(APIView):
    async def get(self, request):
        return Response()

    def post(self, request):
        return Response()


class AsyncErrorView(APIView):
    permission_classes = [permissions.AllowAny]

    async def get(self, request):
        raise APIException('Boom')


class AsyncSyncORMView(APIView):
    """
    An async view naively performing a synchronous database query, which
    Django refuses to do from within an event loop.
    """
    permission_classes = [permissions.AllowAny]

    async def get(self, request):
        return Response({'count': BasicModel.objects.count()})


class AsyncORMView(APIView):
    permission_classes = [permissions.AllowAny]

    async def get(self, request):
        return Response({'count': await BasicModel.objects.acount()})


@api_view(['GET', 'POST'])
@permission_classes([permissions.AllowAny])
async def async_function_view(request):
    return Response({'method': request.method, 'user': str(await request.auser())})


# Authentication
# --------------

class SyncOnlyAuthentication(BaseAuthentication):
    """
    A third-party style authentication class, only implementing the sync API
    and hitting the database. Runs in a thread when used from async views.
    """
    def authenticate(self, request):
        username = request.headers.get('x-user')
        if not username:
            return None
        return (User.objects.get(username=username), 'sync-only')


class DuckTypedAuthentication:
    """
    An authentication class that doesn't extend `BaseAuthentication`.
    """
    def authenticate(self, request):
        username = request.headers.get('x-user')
        if not username:
            return None
        return (User.objects.get(username=username), 'duck-typed')


class NativeAsyncAuthentication(BaseAuthentication):
    def authenticate(self, request):
        raise AssertionError('The sync implementation must not be used.')

    async def aauthenticate(self, request):
        username = request.headers.get('x-user')
        if not username:
            return None
        return (await User.objects.aget(username=username), 'native-async')


def make_auth_view(*authentication):
    class AuthView(APIView):
        authentication_classes = authentication
        permission_classes = [permissions.IsAuthenticated]

        async def get(self, request):
            return Response({
                'user': request.user.username,
                'auth': str(request.auth),
                'authenticator': type(request.successful_authenticator).__name__,
            })
    return AuthView


# Permissions
# -----------

class SyncOnlyDBPermission(BasePermission):
    """
    A third-party style permission class hitting the database, only
    implementing the sync API. Runs in a thread when used from async views.
    """
    message = 'No basic models yet.'

    def has_permission(self, request, view):
        return BasicModel.objects.exists()


class NativeAsyncPermission(BasePermission):
    def has_permission(self, request, view):
        raise AssertionError('The sync implementation must not be used.')

    async def ahas_permission(self, request, view):
        return request.headers.get('x-allow') == 'yes'


class DenyPermission(BasePermission):
    message = 'Denied.'

    def has_permission(self, request, view):
        return False


def make_permission_view(*permission):
    class PermissionView(APIView):
        authentication_classes = []
        permission_classes = permission

        async def get(self, request):
            return Response({'ok': True})
    return PermissionView


class BasicModelSerializer(ModelSerializer):
    class Meta:
        model = BasicModel
        fields = '__all__'


class ModelPermissionsView(GenericAPIView):
    queryset = BasicModel.objects.all()
    serializer_class = BasicModelSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [permissions.DjangoModelPermissions]

    async def post(self, request):
        return Response({'ok': True}, status=status.HTTP_201_CREATED)


class AdminRendererView(GenericAPIView):
    """
    The admin renderer re-invokes the view's `get()` handler when rendering
    validation errors, which must work for async handlers too.
    """
    queryset = BasicModel.objects.all()
    serializer_class = BasicModelSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    renderer_classes = [AdminRenderer, JSONRenderer]

    async def get(self, request):
        return Response({'listing': [item.text async for item in self.get_queryset()]})

    async def post(self, request):
        serializer = self.get_serializer(data=request.data)
        await serializer.ais_valid(raise_exception=True)
        await serializer.asave()
        return Response(await serializer.adata(), status=status.HTTP_201_CREATED)


class ObjectPermission(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.text != 'forbidden'


class ObjectPermissionsView(GenericAPIView):
    queryset = BasicModel.objects.all()
    serializer_class = BasicModelSerializer
    authentication_classes = []
    permission_classes = [ObjectPermission]

    async def get(self, request, pk):
        instance = await self.aget_object()
        return Response({'text': instance.text})


# Throttling
# ----------

class Anon3PerMinuteThrottle(AnonRateThrottle):
    rate = '3/min'


class User3PerMinuteThrottle(UserRateThrottle):
    rate = '3/min'


class SyncOnlyThrottle(BaseThrottle):
    """
    A third-party style throttle only implementing the sync API.
    """
    def allow_request(self, request, view):
        # Blocking operation which would be refused within the event loop.
        BasicModel.objects.exists()
        return request.headers.get('x-throttle') != 'yes'

    def wait(self):
        return 42


class AnonThrottledView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [Anon3PerMinuteThrottle]

    async def get(self, request):
        return Response({'ok': True})


class UserThrottledView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = []
    throttle_classes = [User3PerMinuteThrottle]

    async def get(self, request):
        return Response({'ok': True})


class ScopedThrottledView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'async-scope'

    async def get(self, request):
        return Response({'ok': True})


class SyncThrottledView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [SyncOnlyThrottle]

    async def get(self, request):
        return Response({'ok': True})


# Metadata
# --------

class MetadataListView(GenericAPIView):
    queryset = BasicModel.objects.all()
    serializer_class = BasicModelSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    async def get(self, request):
        return Response()

    async def post(self, request):
        return Response()


class MetadataDetailView(GenericAPIView):
    queryset = BasicModel.objects.all()
    serializer_class = BasicModelSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    async def get(self, request, pk):
        return Response()

    async def put(self, request, pk):
        return Response()


urlpatterns = [
    path('async/', AsyncView.as_view()),
    path('async-error/', AsyncErrorView.as_view()),
    path('async-sync-orm/', AsyncSyncORMView.as_view()),
    path('async-orm/', AsyncORMView.as_view()),
    path('async-function/', async_function_view),
    path('auth/sync-only/', make_auth_view(SyncOnlyAuthentication).as_view()),
    path('auth/duck-typed/', make_auth_view(DuckTypedAuthentication).as_view()),
    path('auth/native/', make_auth_view(NativeAsyncAuthentication).as_view()),
    path('auth/token/', make_auth_view(TokenAuthentication).as_view()),
    path('auth/basic/', make_auth_view(BasicAuthentication).as_view()),
    path('auth/session/', make_auth_view(SessionAuthentication).as_view()),
    path('auth/multiple/', make_auth_view(TokenAuthentication, SessionAuthentication).as_view()),
    path('perm/sync-only/', make_permission_view(SyncOnlyDBPermission).as_view()),
    path('perm/native/', make_permission_view(NativeAsyncPermission).as_view()),
    path('perm/and/', make_permission_view(NativeAsyncPermission & SyncOnlyDBPermission).as_view()),
    path('perm/or/', make_permission_view(NativeAsyncPermission | SyncOnlyDBPermission).as_view()),
    path('perm/not/', make_permission_view(~NativeAsyncPermission).as_view()),
    path('perm/deny/', make_permission_view(DenyPermission).as_view()),
    path('perm/model/', ModelPermissionsView.as_view()),
    path('admin-renderer/', AdminRendererView.as_view()),
    path('perm/object/<int:pk>/', ObjectPermissionsView.as_view()),
    path('throttle/anon/', AnonThrottledView.as_view()),
    path('throttle/user/', UserThrottledView.as_view()),
    path('throttle/scoped/', ScopedThrottledView.as_view()),
    path('throttle/sync/', SyncThrottledView.as_view()),
    path('metadata/', MetadataListView.as_view()),
    path('metadata/<int:pk>/', MetadataDetailView.as_view()),
]


@override_settings(ROOT_URLCONF='tests.test_async_views')
class AsyncAPIViewTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()

    def test_view_is_async(self):
        assert AsyncView.view_is_async is True
        assert AsyncErrorView.view_is_async is True
        assert APIView.view_is_async is False

    def test_mixed_handlers_are_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            MixedView.as_view()

    async def test_get(self):
        response = await self.client.get('/async/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'method': 'GET', 'user': 'AnonymousUser'}

    async def test_post_parses_data(self):
        response = await self.client.post('/async/', {'a': 1}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'method': 'POST', 'data': {'a': 1}}

    async def test_method_not_allowed(self):
        response = await self.client.put('/async/')
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    async def test_exception_handling(self):
        response = await self.client.get('/async-error/')
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data == {'detail': 'Boom'}

    async def test_options(self):
        response = await self.client.options('/async/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['name'] == 'Async'
        assert response['Allow'] == 'GET, POST, HEAD, OPTIONS'

    async def test_sync_orm_in_async_view_is_refused(self):
        # Sanity check that the test client really does run the view within
        # an event loop, so that the tests below are meaningful.
        with self.assertRaises(SynchronousOnlyOperation):
            await self.client.get('/async-sync-orm/')

    async def test_async_orm_in_async_view(self):
        await BasicModel.objects.acreate(text='x')
        response = await self.client.get('/async-orm/')
        assert response.data == {'count': 1}

    async def test_function_based_view(self):
        response = await self.client.get('/async-function/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'method': 'GET', 'user': 'AnonymousUser'}

        response = await self.client.post('/async-function/')
        assert response.data == {'method': 'POST', 'user': 'AnonymousUser'}

    def test_function_based_view_is_async(self):
        assert async_function_view.cls.view_is_async is True

    async def test_browsable_api(self):
        response = await self.client.get('/async/?format=api')
        assert response.status_code == status.HTTP_200_OK
        assert response['Content-Type'].startswith('text/html')
        assert b'<title>Async' in response.content

    async def test_admin_renderer_reinvokes_async_handler_on_errors(self):
        await BasicModel.objects.acreate(text='existing')
        response = await self.client.post('/admin-renderer/', {'text': ''}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response['Content-Type'].startswith('text/html')
        assert b'existing' in response.content

    def test_sync_client_can_call_async_view(self):
        # Django transparently runs async views from a synchronous handler.
        client = APIClient()
        response = client.get('/async/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'method': 'GET', 'user': 'AnonymousUser'}

    async def test_request_factory(self):
        factory = AsyncAPIRequestFactory()
        request = factory.post('/async/', {'a': 1}, format='json')
        response = await AsyncView.as_view()(request)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'method': 'POST', 'data': {'a': 1}}

    def test_sync_request_factory(self):
        factory = APIRequestFactory()
        request = factory.get('/async/')
        # The view returns a coroutine, as Django's async views do.
        from asgiref.sync import async_to_sync
        response = async_to_sync(AsyncView.as_view())(request)
        assert response.status_code == status.HTTP_200_OK


# Django's `aauthenticate()` requires all authentication backends to provide
# an `aauthenticate()` method, which third-party backends may lack.
@override_settings(
    ROOT_URLCONF='tests.test_async_views',
    AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'],
)
class AsyncAuthenticationTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()
        self.user = User.objects.create_user('alice', 'alice@example.com', 'password')
        self.token = Token.objects.create(user=self.user)

    async def test_unauthenticated(self):
        response = await self.client.get('/auth/token/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response['WWW-Authenticate'] == 'Token'

    async def test_sync_only_authentication_runs_in_thread(self):
        response = await self.client.get('/auth/sync-only/', headers={"x-user": 'alice'})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'user': 'alice', 'auth': 'sync-only', 'authenticator': 'SyncOnlyAuthentication'
        }

    async def test_duck_typed_authentication_runs_in_thread(self):
        response = await self.client.get('/auth/duck-typed/', headers={"x-user": 'alice'})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['auth'] == 'duck-typed'

    async def test_native_async_authentication(self):
        response = await self.client.get('/auth/native/', headers={"x-user": 'alice'})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['auth'] == 'native-async'

    async def test_token_authentication(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        response = await self.client.get('/auth/token/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'user': 'alice', 'auth': self.token.key, 'authenticator': 'TokenAuthentication'
        }

    async def test_token_authentication_invalid_token(self):
        response = await self.client.get('/auth/token/', headers={'Authorization': 'Token nope'})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data == {'detail': 'Invalid token.'}

    async def test_token_authentication_inactive_user(self):
        self.user.is_active = False
        await self.user.asave()
        response = await self.client.get('/auth/token/', headers={'Authorization': 'Token ' + self.token.key})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data == {'detail': 'User inactive or deleted.'}

    async def test_basic_authentication(self):
        credentials = base64.b64encode(b'alice:password').decode()
        response = await self.client.get('/auth/basic/', headers={'Authorization': 'Basic ' + credentials})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['user'] == 'alice'

    async def test_basic_authentication_invalid_password(self):
        credentials = base64.b64encode(b'alice:wrong').decode()
        response = await self.client.get('/auth/basic/', headers={'Authorization': 'Basic ' + credentials})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data == {'detail': 'Invalid username/password.'}

    async def test_session_authentication(self):
        await self.client.aforce_login(self.user)
        response = await self.client.get('/auth/session/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['user'] == 'alice'
        assert response.data['authenticator'] == 'SessionAuthentication'

    async def test_session_authentication_unauthenticated(self):
        response = await self.client.get('/auth/session/')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_multiple_authenticators_fall_through(self):
        await self.client.aforce_login(self.user)
        response = await self.client.get('/auth/multiple/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['authenticator'] == 'SessionAuthentication'

    async def test_force_authenticate(self):
        self.client.force_authenticate(user=self.user, token='forced')
        response = await self.client.get('/auth/token/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'user': 'alice', 'auth': 'forced', 'authenticator': 'ForcedAuthentication'
        }

    async def test_alogout_resets_force_authenticate(self):
        self.client.force_authenticate(user=self.user)
        await self.client.alogout()
        response = await self.client.get('/auth/token/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@override_settings(ROOT_URLCONF='tests.test_async_views')
class AsyncPermissionTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()

    async def test_sync_only_permission_runs_in_thread(self):
        response = await self.client.get('/perm/sync-only/')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data == {'detail': 'No basic models yet.'}

        await BasicModel.objects.acreate(text='x')
        response = await self.client.get('/perm/sync-only/')
        assert response.status_code == status.HTTP_200_OK

    async def test_native_async_permission(self):
        response = await self.client.get('/perm/native/')
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = await self.client.get('/perm/native/', headers={'X-Allow': 'yes'})
        assert response.status_code == status.HTTP_200_OK

    async def test_composed_permissions(self):
        response = await self.client.get('/perm/and/', headers={'X-Allow': 'yes'})
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response = await self.client.get('/perm/or/', headers={'X-Allow': 'yes'})
        assert response.status_code == status.HTTP_200_OK
        response = await self.client.get('/perm/or/')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response = await self.client.get('/perm/not/')
        assert response.status_code == status.HTTP_200_OK
        response = await self.client.get('/perm/not/', headers={'X-Allow': 'yes'})
        assert response.status_code == status.HTTP_403_FORBIDDEN

        await BasicModel.objects.acreate(text='x')
        response = await self.client.get('/perm/and/', headers={'X-Allow': 'yes'})
        assert response.status_code == status.HTTP_200_OK

    async def test_permission_message(self):
        response = await self.client.get('/perm/deny/')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data == {'detail': 'Denied.'}

    async def test_django_model_permissions(self):
        user = await User.objects.acreate_user('bob', 'bob@example.com', 'password')
        await self.client.aforce_login(user)

        response = await self.client.post('/perm/model/', {'text': 'x'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

        permission = await Permission.objects.aget(codename='add_basicmodel')
        await user.user_permissions.aadd(permission)
        # Log in again to get a user instance without cached permissions.
        await self.client.aforce_login(await User.objects.aget(pk=user.pk))
        response = await self.client.post('/perm/model/', {'text': 'x'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    async def test_django_model_permissions_unauthenticated(self):
        response = await self.client.post('/perm/model/', {'text': 'x'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_object_permissions(self):
        allowed = await BasicModel.objects.acreate(text='allowed')
        forbidden = await BasicModel.objects.acreate(text='forbidden')

        response = await self.client.get('/perm/object/%d/' % allowed.pk)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'text': 'allowed'}

        response = await self.client.get('/perm/object/%d/' % forbidden.pk)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = await self.client.get('/perm/object/999/')
        assert response.status_code == status.HTTP_404_NOT_FOUND


@override_settings(ROOT_URLCONF='tests.test_async_views')
class AsyncThrottlingTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()
        cache.clear()

    def tearDown(self):
        cache.clear()

    async def assert_throttled_after(self, url, allowed, **kwargs):
        for _ in range(allowed):
            response = await self.client.get(url, **kwargs)
            assert response.status_code == status.HTTP_200_OK
        response = await self.client.get(url, **kwargs)
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert 'Retry-After' in response

    async def test_anon_rate_throttle(self):
        await self.assert_throttled_after('/throttle/anon/', 3)

    async def test_user_rate_throttle(self):
        user = await User.objects.acreate_user('alice', 'alice@example.com', 'password')
        await self.client.aforce_login(user)
        await self.assert_throttled_after('/throttle/user/', 3)

    async def test_scoped_rate_throttle(self):
        old_rates = ScopedRateThrottle.THROTTLE_RATES
        ScopedRateThrottle.THROTTLE_RATES = {'async-scope': '2/min'}
        try:
            await self.assert_throttled_after('/throttle/scoped/', 2)
        finally:
            ScopedRateThrottle.THROTTLE_RATES = old_rates

    async def test_sync_only_throttle_runs_in_thread(self):
        response = await self.client.get('/throttle/sync/')
        assert response.status_code == status.HTTP_200_OK

        response = await self.client.get('/throttle/sync/', headers={'X-Throttle': 'yes'})
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response['Retry-After'] == '42'


@override_settings(ROOT_URLCONF='tests.test_async_views')
class AsyncMetadataTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()

    async def test_options_includes_actions(self):
        response = await self.client.options('/metadata/')
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data['actions']) == {'POST'}
        assert response.data['actions']['POST']['text']['type'] == 'string'

    async def test_options_for_instance(self):
        instance = await BasicModel.objects.acreate(text='x')
        response = await self.client.options('/metadata/%d/' % instance.pk)
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data['actions']) == {'PUT'}

    async def test_options_for_missing_instance(self):
        response = await self.client.options('/metadata/999/')
        assert response.status_code == status.HTTP_200_OK
        # The object lookup fails, so no PUT metadata is included.
        assert 'actions' not in response.data


class RequestAuserTests(TestCase):
    def test_auser_populates_user_and_auth(self):
        from asgiref.sync import async_to_sync

        from rest_framework.request import Request

        class Authenticator(BaseAuthentication):
            async def aauthenticate(self, request):
                return ('the-user', 'the-token')

        request = Request(APIRequestFactory().get('/'), authenticators=[Authenticator()])
        assert async_to_sync(request.auser)() == 'the-user'
        assert request.user == 'the-user'
        assert request.auth == 'the-token'
        assert isinstance(request.successful_authenticator, Authenticator)

    def test_auser_failure_resets_state(self):
        from asgiref.sync import async_to_sync

        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework.request import Request

        class Authenticator(BaseAuthentication):
            async def aauthenticate(self, request):
                raise AuthenticationFailed()

        request = Request(APIRequestFactory().get('/'), authenticators=[Authenticator()])
        with self.assertRaises(AuthenticationFailed):
            async_to_sync(request.auser)()
        assert request.successful_authenticator is None
        assert request.user.is_anonymous

    def test_auser_wraps_attribute_errors(self):
        from asgiref.sync import async_to_sync

        from rest_framework.request import Request, WrappedAttributeError

        class Authenticator(BaseAuthentication):
            async def aauthenticate(self, request):
                raise AttributeError('oops')

        request = Request(APIRequestFactory().get('/'), authenticators=[Authenticator()])
        with self.assertRaises(WrappedAttributeError):
            async_to_sync(request.auser)()


class SyncViewsStillWorkTests(TestCase):
    """
    The async additions must not change the behavior of synchronous views.
    """
    def test_sync_permission_view(self):
        class View(APIView):
            authentication_classes = []
            permission_classes = [SyncOnlyDBPermission]

            def get(self, request):
                return Response({'ok': True})

        request = APIRequestFactory().get('/')
        assert View.as_view()(request).status_code == status.HTTP_403_FORBIDDEN
        BasicModel.objects.create(text='x')
        assert View.as_view()(request).status_code == status.HTTP_200_OK

    def test_sync_throttle_view(self):
        cache.clear()

        class View(APIView):
            authentication_classes = []
            permission_classes = []
            throttle_classes = [Anon3PerMinuteThrottle]

            def get(self, request):
                return Response({'ok': True})

        request = APIRequestFactory().get('/')
        for _ in range(3):
            assert View.as_view()(request).status_code == status.HTTP_200_OK
        assert View.as_view()(request).status_code == status.HTTP_429_TOO_MANY_REQUESTS
        cache.clear()

    def test_sync_to_async_helper_is_not_used_for_sync_views(self):
        # Native async policies are never consulted by sync views.
        class View(APIView):
            authentication_classes = []
            permission_classes = [NativeAsyncPermission]

            def get(self, request):
                return Response({'ok': True})

        request = APIRequestFactory().get('/')
        with self.assertRaises(AssertionError):
            View.as_view()(request)


class AsyncThrottleUnitTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_aallow_request_uses_async_cache_api(self):
        request = APIRequestFactory().get('/')
        request.user = None
        throttle = Anon3PerMinuteThrottle()
        view = APIView()

        async def run():
            results = [await throttle.aallow_request(request, view) for _ in range(4)]
            return results, throttle.wait()

        from asgiref.sync import async_to_sync
        results, wait = async_to_sync(run)()
        assert results == [True, True, True, False]
        assert wait is not None

    def test_sync_only_throttle_default_aallow_request(self):
        request = APIRequestFactory().get('/', HTTP_X_THROTTLE='yes')
        throttle = SyncOnlyThrottle()

        async def run():
            return await throttle.aallow_request(request, APIView())

        from asgiref.sync import async_to_sync
        assert async_to_sync(run)() is False
        assert async_to_sync(sync_to_async(lambda: True))() is True
