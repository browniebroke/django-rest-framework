# Asynchronous support

> Django has support for writing asynchronous ("async") views, along with an entirely async-enabled request stack if you are running under ASGI.
>
> &mdash; [Django documentation][django-async]

REST framework supports writing asynchronous views, following the same approach as Django's own [asynchronous support][django-async]. Async support is opt-in and per-view: existing synchronous views, serializers, and policies keep working unchanged, and sync remains the primary, default API.

## Overview

The design follows the principles laid out in Django's [DEP 0009][dep-0009]:

* **Sync is primary, async is opt-in.** Nothing changes for synchronous code. Async behavior is enabled by writing coroutine (`async def`) handler methods.
* **Explicit over implicit.** Wherever an operation may block, an asynchronous counterpart is provided using Django's `a` prefix naming convention (`aget_object()`, `ais_valid()`, `await request.auser()`, ...). There is no overloading of a single method name for both styles, so a missing `await` is always visible.
* **Safe by default.** Every extension point (authentication, permission, throttle, pagination, filter backend, and metadata classes) gains an async counterpart whose default implementation runs the existing synchronous method in a worker thread, using `asgiref.sync.sync_to_async`. Existing third-party classes therefore work unchanged from async views, without risking `SynchronousOnlyOperation` errors. The classes provided by REST framework implement native async versions where this is beneficial.
* **No mixing within a view.** As with Django's class-based views, all the handler methods on a view must either be sync or async. Mixing the two raises `ImproperlyConfigured`. For viewsets this applies per `.as_view()` binding, so sync and async actions may coexist on one viewset as long as they are routed to separate views.
* **Sync customizations are never bypassed.** If a subclass of a built-in class overrides a synchronous method (say `TokenAuthentication.authenticate_credentials()`) without overriding its async counterpart, the built-in native async implementation steps aside and runs the synchronous method in a thread. See [Overriding synchronous methods](#overriding-synchronous-methods).

Async views only provide a concurrency benefit when Django is run under [ASGI][django-asgi]. When run under WSGI, Django executes async views in a one-off event loop, with a small performance penalty.

---

## Writing async views

### Class-based views

Declare the handler methods of an `APIView` subclass using `async def`. REST framework detects this in the same way Django does, and runs the whole request lifecycle (authentication, permission checks, throttling, and the handler) asynchronously.

    import httpx
    from rest_framework.views import APIView
    from rest_framework.response import Response

    class WeatherView(APIView):
        async def get(self, request, *args, **kwargs):
            async with httpx.AsyncClient() as client:
                response = await client.get('https://weather.example.com/today')
            return Response(response.json())

All handlers on the view must be async. The `OPTIONS` method is handled automatically.

Within an async view you can use Django's asynchronous ORM interface (`aget()`, `acount()`, `async for`, ...), or run synchronous code in a thread using `sync_to_async`.

### Function-based views

The `@api_view` decorator supports coroutine functions:

    @api_view(['GET'])
    async def weather(request):
        ...
        return Response(data)

### Generic views and viewsets

Asynchronous equivalents of the generic views, mixins, and model viewsets are provided, prefixed with `Async`:

    from rest_framework import generics, viewsets

    class UserList(generics.AsyncListCreateAPIView):
        queryset = User.objects.all()
        serializer_class = UserSerializer

    class UserViewSet(viewsets.AsyncModelViewSet):
        queryset = User.objects.all()
        serializer_class = UserSerializer

Available classes:

* `mixins.AsyncCreateModelMixin`, `mixins.AsyncListModelMixin`, `mixins.AsyncRetrieveModelMixin`, `mixins.AsyncUpdateModelMixin`, `mixins.AsyncDestroyModelMixin`
* `generics.AsyncCreateAPIView`, `generics.AsyncListAPIView`, `generics.AsyncRetrieveAPIView`, `generics.AsyncDestroyAPIView`, `generics.AsyncUpdateAPIView`, `generics.AsyncListCreateAPIView`, `generics.AsyncRetrieveUpdateAPIView`, `generics.AsyncRetrieveDestroyAPIView`, `generics.AsyncRetrieveUpdateDestroyAPIView`
* `viewsets.AsyncReadOnlyModelViewSet`, `viewsets.AsyncModelViewSet`

The async mixins provide the same actions as their synchronous counterparts (`list()`, `create()`, `retrieve()`, `update()`, `partial_update()`, and `destroy()`), so routers, the `.action` attribute, and any code relying on action names keep working unchanged. The `perform_*()` hooks become coroutines named `aperform_create()`, `aperform_update()`, and `aperform_destroy()`:

    class UserViewSet(viewsets.AsyncModelViewSet):
        queryset = User.objects.all()
        serializer_class = UserSerializer

        async def aperform_create(self, serializer):
            await serializer.asave(owner=self.request.user)
            await notify_user_created()

`GenericAPIView` (and therefore `GenericViewSet`) also provides `aget_queryset()`, `aget_object()`, `afilter_queryset()`, and `apaginate_queryset()`. `get_serializer()` and `get_paginated_response()` do not perform any blocking operations and are shared between both styles.

By default `aget_queryset()` simply calls `get_queryset()`, which only builds up a lazy queryset. Override `aget_queryset()` when determining the queryset requires a query, such as looking up a parent object:

    class AlbumTrackList(generics.AsyncListAPIView):
        serializer_class = TrackSerializer

        async def aget_queryset(self):
            album = await aget_object_or_404(Album, pk=self.kwargs['album_pk'])
            return album.tracks.all()

If you override only `get_queryset()` and it performs a query, the view keeps working: the overridden method is run in a thread, as described under [Overriding synchronous methods](#overriding-synchronous-methods).

Extra actions on a viewset are declared with `@action` as usual, using `async def`:

    class UserViewSet(viewsets.AsyncModelViewSet):
        ...

        @action(detail=True, methods=['post'])
        async def activate(self, request, pk=None):
            user = await self.aget_object()
            user.is_active = True
            await user.asave()
            return Response({'status': 'activated'})

Whether a viewset's view is async is decided per `.as_view()` binding, from the actions being bound to it. Routers bind the list actions, the detail actions, and each extra action to separate views, so a viewset may mix async and sync actions as long as each binding is consistent. This allows viewsets to be converted incrementally, and lets a sync action live alongside async ones:

    class ReportViewSet(viewsets.AsyncModelViewSet):
        queryset = Report.objects.all()
        serializer_class = ReportSerializer

        @action(detail=True)
        def download(self, request, pk=None):
            # A synchronous action, bound to its own synchronous view.
            report = self.get_object()
            return FileResponse(report.file.open())

Binding a sync and an async action to the same view, for example `ReportViewSet.as_view({'get': 'list', 'post': 'sync_action'})`, raises `ImproperlyConfigured`. The class attribute `ViewSet.view_is_async` reports whether *all* of the viewset's actions are async, while during a request the instance attribute reflects the view that is actually running.

---

## Requests

`request.user` and `request.auth` are lazily populated by running the view's authentication classes, which may perform blocking operations. In async code, use `await request.auser()` instead, which resolves authentication asynchronously and populates `request.user`, `request.auth`, and `request.successful_authenticator` for subsequent access:

    class ProfileView(APIView):
        async def get(self, request):
            user = await request.auser()
            return Response({'username': user.username})

Async views authenticate the request before calling the handler (in `aperform_authentication()`), so `request.user` is already populated by the time the handler runs. Awaiting `request.auser()` is only required when authentication has been deferred, for example by overriding `aperform_authentication()`.

Note that `request.auser()` is distinct from the `auser()` method added by Django's `AuthenticationMiddleware` on the underlying `HttpRequest`. The REST framework method runs the view's authentication classes, whereas Django's only considers the session.

Parsing the request body with `request.data` or `request.FILES` happens synchronously. This is fine for JSON and other small bodies, which Django has already read into memory by the time the view runs. Large multipart uploads, however, may have been spooled to disk, and file upload handlers may write to disk. For those, use `await request.adata()`, which parses the request in a thread and populates `request.data` and `request.FILES`:

    class UploadView(APIView):
        async def post(self, request):
            data = await request.adata()
            upload = request.FILES['document']
            ...

The async generic mixins use `request.adata()`.

---

## Serializers

Serializers may perform blocking operations at several points: validators and related fields query the database during validation, `save()` writes to the database, and accessing `.data` may evaluate querysets and related objects. The following coroutines are provided on all serializers:

* `await serializer.ais_valid(raise_exception=False)`
* `await serializer.asave(**kwargs)`
* `await serializer.adata()`

These run their synchronous counterparts in a worker thread, in the same way Django's `Model.asave()` runs `Model.save()`. This keeps the whole serializer machinery, including third-party fields, validators, and your own `create()` and `update()` methods, safe to use from async views without any changes:

    class CommentCreateView(APIView):
        async def post(self, request):
            serializer = CommentSerializer(data=request.data)
            await serializer.ais_valid(raise_exception=True)
            await serializer.asave()
            return Response(await serializer.adata(), status=201)

`create()` and `update()` remain synchronous. If you need to await something as part of saving, do so in the view (or in `aperform_create()` and friends) rather than in the serializer.

Because serialization is performed in a thread rather than on the event loop, the usual advice about avoiding N+1 queries applies: use `select_related()` and `prefetch_related()` on the view's queryset when serializing related objects.

---

## Authentication, permissions and throttling

Each of the policy base classes provides an asynchronous counterpart of its main method, with a default implementation that runs the synchronous method in a worker thread:

| Base class           | Sync method                     | Async counterpart                |
|----------------------|---------------------------------|----------------------------------|
| `BaseAuthentication` | `authenticate()`                | `aauthenticate()`                |
| `BasePermission`     | `has_permission()`              | `ahas_permission()`              |
| `BasePermission`     | `has_object_permission()`       | `ahas_object_permission()`       |
| `BaseThrottle`       | `allow_request()`               | `aallow_request()`               |
| `BasePagination`     | `paginate_queryset()`           | `apaginate_queryset()`           |
| `BaseFilterBackend`  | `filter_queryset()`             | `afilter_queryset()`             |
| `BaseMetadata`       | `determine_metadata()`          | `adetermine_metadata()`          |

Existing classes, including third-party ones, work unchanged from async views. When writing a class that is used from async views, you may override the async counterpart to provide a native implementation, avoiding the thread switch:

    class IsOwner(permissions.BasePermission):
        def has_object_permission(self, request, view, obj):
            return obj.owner_id == request.user.pk

        async def ahas_object_permission(self, request, view, obj):
            # No blocking operations, so the sync implementation can be
            # called directly.
            return self.has_object_permission(request, view, obj)

**Always implement the synchronous method.** It is the primary API and remains in use by synchronous views, by the browsable API, and by anything else that isn't async-aware. The async counterpart is an optional optimization.

Classes which don't extend the REST framework base classes at all (for example django-filter's `DjangoFilterBackend`) are supported too: when an object lacks the async counterpart, its synchronous method is run in a thread.

The classes included with REST framework provide native async implementations:

* `SessionAuthentication` uses `HttpRequest.auser()`, `TokenAuthentication` uses `aget()`, and `BasicAuthentication` and `RemoteUserAuthentication` use `django.contrib.auth.aauthenticate()`. Note that `aauthenticate()` requires every configured authentication backend to provide an `aauthenticate()` method, which backends not inheriting from Django's `BaseBackend` may lack.
* `DjangoModelPermissions` and `DjangoObjectPermissions` use the `ahas_perms()` method of the user.
* `SimpleRateThrottle` and its subclasses use the asynchronous cache API.
* `PageNumberPagination`, `LimitOffsetPagination`, and `CursorPagination` use `acount()` and asynchronous queryset iteration.
* `SearchFilter` and `OrderingFilter` only build up lazy querysets, and never block.

Permission composition using `&`, `|`, and `~` is supported for the async counterparts, with the same semantics as the synchronous methods.

---

## Overriding synchronous methods

The built-in classes ship with native async implementations, which don't call the synchronous methods. To ensure that existing customizations are not silently bypassed when a class is used from an async view, every native async implementation first checks whether a subclass has overridden one of the synchronous methods it replaces, without also overriding the async counterpart. If so, the overridden synchronous method is run in a thread instead.

For example, a token authentication class customized for sync views keeps working as-is from async views, at the cost of a thread switch:

    class TenantTokenAuthentication(TokenAuthentication):
        def authenticate_credentials(self, key):
            user, token = super().authenticate_credentials(key)
            if user.tenant_id != get_current_tenant():
                raise AuthenticationFailed('Wrong tenant.')
            return (user, token)

To regain the native async behavior, override the async counterpart as well:

    class TenantTokenAuthentication(TokenAuthentication):
        def authenticate_credentials(self, key):
            ...

        async def aauthenticate_credentials(self, key):
            user, token = await super().aauthenticate_credentials(key)
            if user.tenant_id != get_current_tenant():
                raise AuthenticationFailed('Wrong tenant.')
            return (user, token)

This applies to the following methods. Overriding the method in the left column without the one in the right column makes the async view run the left column method in a thread.

| Class                      | Synchronous method                              | Async counterpart                                 |
|----------------------------|-------------------------------------------------|---------------------------------------------------|
| `APIView`                  | `initial()`                                     | `ainitial()`                                      |
| `APIView`                  | `perform_authentication()`                      | `aperform_authentication()`                       |
| `APIView`                  | `check_permissions()`                           | `acheck_permissions()`                            |
| `APIView`                  | `check_object_permissions()`                    | `acheck_object_permissions()`                     |
| `APIView`                  | `check_throttles()`                             | `acheck_throttles()`                              |
| `GenericAPIView`           | `get_queryset()`                                | `aget_queryset()`                                 |
| `GenericAPIView`           | `get_object()`                                  | `aget_object()`                                   |
| `GenericAPIView`           | `filter_queryset()`                             | `afilter_queryset()`                              |
| `GenericAPIView`           | `paginate_queryset()`                           | `apaginate_queryset()`                            |
| `Async*ModelMixin`         | `perform_create()`, `perform_update()`, `perform_destroy()` | `aperform_create()`, `aperform_update()`, `aperform_destroy()` |
| `BasicAuthentication`, `TokenAuthentication` | `authenticate()`, `authenticate_credentials()` | `aauthenticate()`, `aauthenticate_credentials()` |
| `SessionAuthentication`, `RemoteUserAuthentication` | `authenticate()`               | `aauthenticate()`                                 |
| `DjangoModelPermissions`   | `has_permission()`                              | `ahas_permission()`                               |
| `DjangoObjectPermissions`  | `has_object_permission()`                       | `ahas_object_permission()`                        |
| `SimpleRateThrottle`       | `allow_request()`, `throttle_success()`         | `aallow_request()`, `athrottle_success()`         |
| `ScopedRateThrottle`       | `allow_request()`                               | `aallow_request()`                                |
| `PageNumberPagination`, `CursorPagination` | `paginate_queryset()`             | `apaginate_queryset()`                            |
| `LimitOffsetPagination`    | `paginate_queryset()`, `get_count()`            | `apaginate_queryset()`, `aget_count()`            |
| `SimpleMetadata`           | `determine_metadata()`, `determine_actions()`   | `adetermine_metadata()`, `adetermine_actions()`   |

Methods not listed here, such as `get_cache_key()`, `get_serializer_class()`, or `enforce_csrf()`, are called by both the sync and the async implementations, so overriding them affects both.

---

## Transactions

Django's `ATOMIC_REQUESTS` setting doesn't apply to async views, and `transaction.atomic()` can't wrap `await` expressions. The idiom is to write the transactional part as a synchronous helper, and run it with `sync_to_async`. Do any awaiting, such as notifying external services, outside of the transaction:

    from asgiref.sync import sync_to_async
    from django.db import transaction

    class OrderViewSet(viewsets.AsyncModelViewSet):
        queryset = Order.objects.all()
        serializer_class = OrderSerializer

        @action(detail=True, methods=['post'])
        async def checkout(self, request, pk=None):
            order = await self.aget_object()
            serializer = CheckoutSerializer(data=await request.adata())
            await serializer.ais_valid(raise_exception=True)

            receipt = await sync_to_async(self._checkout)(order, serializer.validated_data)

            # Outside of the transaction, so a failure here can't roll it back.
            await payment_gateway.notify(receipt)
            return Response(ReceiptSerializer(receipt).data)

        def _checkout(self, order, data):
            with transaction.atomic():
                order.mark_paid(**data)
                receipt = Receipt.objects.create(order=order)
                order.items.update(shipped=False)
            return receipt

The same pattern applies to `create()` and `update()` methods on serializers, which are already synchronous: wrapping their body in `transaction.atomic()` works unchanged, since `asave()` runs them in a thread.

---

## Customizing the request lifecycle

The `dispatch()` method of an async view returns a coroutine, in the same way as Django's `View.dispatch()`. If you override `dispatch()` in an async view, declare it with `async def` and await the parent implementation:

    class LoggingView(APIView):
        async def dispatch(self, request, *args, **kwargs):
            response = await super().dispatch(request, *args, **kwargs)
            logger.info('%s %s -> %s', request.method, request.path, response.status_code)
            return response

The `initial()` hook and the policy checks it performs have async counterparts that are used by async views instead of the synchronous methods:

* `ainitial()`
* `aperform_authentication()`
* `acheck_permissions()`
* `acheck_object_permissions()`
* `acheck_throttles()`

`initialize_request()`, `finalize_response()`, and `handle_exception()` don't perform any blocking operations and are shared between the two styles. In particular, the [exception handler][exception-handler] is always a synchronous function.

---

## Testing

`AsyncAPIClient` and `AsyncAPIRequestFactory` are asynchronous counterparts of `APIClient` and `APIRequestFactory`, based on Django's `AsyncClient` and `AsyncRequestFactory`. Their request methods must be awaited. Django's `TestCase` supports `async def` test methods.

    from django.test import TestCase
    from rest_framework.test import AsyncAPIClient

    class UserTests(TestCase):
        async def test_list_users(self):
            client = AsyncAPIClient()
            client.force_authenticate(user=self.user)
            response = await client.get('/users/')
            self.assertEqual(response.status_code, 200)

`AsyncAPIClient.credentials()` accepts headers either using their `HTTP_` prefixed WSGI style names, as `APIClient` does, or as plain header names. Use `await client.alogout()` to clear credentials, forced authentication, and the session.

The synchronous `APIClient` and `APIRequestFactory` may also be used with async views. When called from a synchronous test client, Django runs the async view to completion in an event loop.

---

## Limitations and caveats

* **Response rendering is synchronous.** Renderers are invoked by Django after the view returns. Under ASGI, Django runs the rendering in a worker thread, so renderers (including the browsable API) may safely perform blocking operations. This means the browsable API uses the synchronous methods of your permission and pagination classes, which is why the synchronous methods must always be implemented.
* **`ATOMIC_REQUESTS` is not supported with async views.** This is a Django limitation. Use `transaction.atomic()` explicitly within synchronous code run via `sync_to_async`, or in your `create()`/`update()` methods.
* **Threads and database connections.** Code run using `sync_to_async` executes in a thread with `thread_sensitive=True`, so that within a single request all synchronous code shares the same thread and database connection. Concurrently awaiting several `sync_to_async` calls does not run them in parallel.
* **Async views do not make database-bound work faster.** The serializer and ORM operations still run synchronously in a thread. The benefit of async views is in awaiting other I/O concurrently, such as HTTP requests to other services, without tying up a worker thread.
* **Third party packages may not be async-aware.** Decorators and tools that wrap view methods, such as schema generators decorating handlers, may not preserve coroutine functions. Check that a wrapped async handler is still detected as a coroutine function (`inspect.iscoroutinefunction`), and report the issue to the package concerned.

[django-async]: https://docs.djangoproject.com/en/stable/topics/async/
[django-asgi]: https://docs.djangoproject.com/en/stable/howto/deployment/asgi/
[dep-0009]: https://github.com/django/deps/blob/main/accepted/0009-async.rst
[exception-handler]: ../api-guide/exceptions.md#custom-exception-handling
