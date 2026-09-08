"""
Provides a set of pluggable permission policies.
"""
from asgiref.sync import sync_to_async
from django.http import Http404

from rest_framework import exceptions
from rest_framework.utils.asyncio import overrides_sync_only

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


class OperationHolderMixin:
    def __and__(self, other):
        return OperandHolder(AND, self, other)

    def __or__(self, other):
        return OperandHolder(OR, self, other)

    def __rand__(self, other):
        return OperandHolder(AND, other, self)

    def __ror__(self, other):
        return OperandHolder(OR, other, self)

    def __invert__(self):
        return SingleOperandHolder(NOT, self)


class SingleOperandHolder(OperationHolderMixin):
    def __init__(self, operator_class, op1_class):
        self.operator_class = operator_class
        self.op1_class = op1_class

    def __call__(self, *args, **kwargs):
        op1 = self.op1_class(*args, **kwargs)
        return self.operator_class(op1)


class OperandHolder(OperationHolderMixin):
    def __init__(self, operator_class, op1_class, op2_class):
        self.operator_class = operator_class
        self.op1_class = op1_class
        self.op2_class = op2_class

    def __call__(self, *args, **kwargs):
        op1 = self.op1_class(*args, **kwargs)
        op2 = self.op2_class(*args, **kwargs)
        return self.operator_class(op1, op2)

    def __eq__(self, other):
        return (
            isinstance(other, OperandHolder) and
            self.operator_class == other.operator_class and
            self.op1_class == other.op1_class and
            self.op2_class == other.op2_class
        )

    def __hash__(self):
        return hash((self.operator_class, self.op1_class, self.op2_class))


class AND:
    def __init__(self, op1, op2):
        self.op1 = op1
        self.op2 = op2

    def has_permission(self, request, view):
        return (
            self.op1.has_permission(request, view) and
            self.op2.has_permission(request, view)
        )

    def has_object_permission(self, request, view, obj):
        return (
            self.op1.has_object_permission(request, view, obj) and
            self.op2.has_object_permission(request, view, obj)
        )

    async def ahas_permission(self, request, view):
        return (
            await self.op1.ahas_permission(request, view) and
            await self.op2.ahas_permission(request, view)
        )

    async def ahas_object_permission(self, request, view, obj):
        return (
            await self.op1.ahas_object_permission(request, view, obj) and
            await self.op2.ahas_object_permission(request, view, obj)
        )


class OR:
    def __init__(self, op1, op2):
        self.op1 = op1
        self.op2 = op2

    def has_permission(self, request, view):
        return (
            self.op1.has_permission(request, view) or
            self.op2.has_permission(request, view)
        )

    def has_object_permission(self, request, view, obj):
        return (
            self.op1.has_permission(request, view)
            and self.op1.has_object_permission(request, view, obj)
        ) or (
            self.op2.has_permission(request, view)
            and self.op2.has_object_permission(request, view, obj)
        )

    async def ahas_permission(self, request, view):
        return (
            await self.op1.ahas_permission(request, view) or
            await self.op2.ahas_permission(request, view)
        )

    async def ahas_object_permission(self, request, view, obj):
        return (
            await self.op1.ahas_permission(request, view)
            and await self.op1.ahas_object_permission(request, view, obj)
        ) or (
            await self.op2.ahas_permission(request, view)
            and await self.op2.ahas_object_permission(request, view, obj)
        )


class NOT:
    def __init__(self, op1):
        self.op1 = op1

    def has_permission(self, request, view):
        return not self.op1.has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        return not self.op1.has_object_permission(request, view, obj)

    async def ahas_permission(self, request, view):
        return not await self.op1.ahas_permission(request, view)

    async def ahas_object_permission(self, request, view, obj):
        return not await self.op1.ahas_object_permission(request, view, obj)


class BasePermissionMetaclass(OperationHolderMixin, type):
    pass


class BasePermission(metaclass=BasePermissionMetaclass):
    """
    A base class from which all permission classes should inherit.
    """

    def has_permission(self, request, view):
        """
        Return `True` if permission is granted, `False` otherwise.
        """
        return True

    def has_object_permission(self, request, view, obj):
        """
        Return `True` if permission is granted, `False` otherwise.
        """
        return True

    async def ahas_permission(self, request, view):
        """
        Asynchronous counterpart of `has_permission()`, used by async views.

        The default implementation runs `has_permission()` in a thread, so
        that permission classes performing blocking operations (such as
        database queries) remain safe to use from async views. Override this
        method to provide a native asynchronous implementation.
        """
        return await sync_to_async(self.has_permission)(request, view)

    async def ahas_object_permission(self, request, view, obj):
        """
        Asynchronous counterpart of `has_object_permission()`.

        The default implementation runs `has_object_permission()` in a thread.
        """
        return await sync_to_async(self.has_object_permission)(request, view, obj)


class _NonBlockingPermission(BasePermission):
    """
    Base class for permissions that never perform blocking operations, and
    can therefore call the synchronous implementation directly from async
    views without dispatching to a thread.
    """

    async def ahas_permission(self, request, view):
        return self.has_permission(request, view)

    async def ahas_object_permission(self, request, view, obj):
        return self.has_object_permission(request, view, obj)


class AllowAny(_NonBlockingPermission):
    """
    Allow any access.
    This isn't strictly required, since you could use an empty
    permission_classes list, but it's useful because it makes the intention
    more explicit.
    """

    def has_permission(self, request, view):
        return True


class IsAuthenticated(_NonBlockingPermission):
    """
    Allows access only to authenticated users.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class IsAdminUser(_NonBlockingPermission):
    """
    Allows access only to admin users.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)


class IsAuthenticatedOrReadOnly(_NonBlockingPermission):
    """
    The request is authenticated as a user, or is a read-only request.
    """

    def has_permission(self, request, view):
        return bool(
            request.method in SAFE_METHODS or
            request.user and
            request.user.is_authenticated
        )


class DjangoModelPermissions(BasePermission):
    """
    The request is authenticated using `django.contrib.auth` permissions.
    See: https://docs.djangoproject.com/en/dev/topics/auth/#permissions

    It ensures that the user is authenticated, and has the appropriate
    `add`/`change`/`delete` permissions on the model.

    This permission can only be applied against view classes that
    provide a `.queryset` attribute.
    """

    # Map methods into required permission codes.
    # Override this if you need to also provide 'view' permissions,
    # or if you want to provide custom permission codes.
    perms_map = {
        'GET': [],
        'OPTIONS': [],
        'HEAD': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    authenticated_users_only = True

    def get_required_permissions(self, method, model_cls):
        """
        Given a model and an HTTP method, return the list of permission
        codes that the user is required to have.
        """
        kwargs = {
            'app_label': model_cls._meta.app_label,
            'model_name': model_cls._meta.model_name
        }

        if method not in self.perms_map:
            raise exceptions.MethodNotAllowed(method)

        return [perm % kwargs for perm in self.perms_map[method]]

    def _queryset(self, view):
        assert hasattr(view, 'get_queryset') \
            or getattr(view, 'queryset', None) is not None, (
            'Cannot apply {} on a view that does not set '
            '`.queryset` or have a `.get_queryset()` method.'
        ).format(self.__class__.__name__)

        if hasattr(view, 'get_queryset'):
            queryset = view.get_queryset()
            assert queryset is not None, (
                f'{view.__class__.__name__}.get_queryset() returned None'
            )
            return queryset
        return view.queryset

    def _get_perms_to_check(self, request, view):
        """
        Return either a boolean, if the permission check can be decided
        without consulting the user's permissions, or the list of permission
        codes that the user is required to have.
        """
        if not request.user or (
           not request.user.is_authenticated and self.authenticated_users_only):
            return False

        # Workaround to ensure DjangoModelPermissions are not applied
        # to the root view when using DefaultRouter.
        if getattr(view, '_ignore_model_permissions', False):
            return True

        queryset = self._queryset(view)
        return self.get_required_permissions(request.method, queryset.model)

    def has_permission(self, request, view):
        perms = self._get_perms_to_check(request, view)
        if isinstance(perms, bool):
            return perms
        return request.user.has_perms(perms)

    async def ahas_permission(self, request, view):
        if overrides_sync_only(self, DjangoModelPermissions, 'has_permission'):
            return await super().ahas_permission(request, view)
        perms = await self._aget_perms_to_check(request, view)
        if isinstance(perms, bool):
            return perms
        return await request.user.ahas_perms(perms)

    async def _aget_perms_to_check(self, request, view):
        if not request.user or (
           not request.user.is_authenticated and self.authenticated_users_only):
            return False

        if getattr(view, '_ignore_model_permissions', False):
            return True

        queryset = await self._aqueryset(view)
        return self.get_required_permissions(request.method, queryset.model)

    async def _aqueryset(self, view):
        if hasattr(view, 'aget_queryset'):
            queryset = await view.aget_queryset()
            assert queryset is not None, (
                f'{view.__class__.__name__}.aget_queryset() returned None'
            )
            return queryset
        return self._queryset(view)

    async def ahas_object_permission(self, request, view, obj):
        return self.has_object_permission(request, view, obj)


class DjangoModelPermissionsOrAnonReadOnly(DjangoModelPermissions):
    """
    Similar to DjangoModelPermissions, except that anonymous users are
    allowed read-only access.
    """
    authenticated_users_only = False


class DjangoObjectPermissions(DjangoModelPermissions):
    """
    The request is authenticated using Django's object-level permissions.
    It requires an object-permissions-enabled backend, such as Django Guardian.

    It ensures that the user is authenticated, and has the appropriate
    `add`/`change`/`delete` permissions on the object using .has_perms.

    This permission can only be applied against view classes that
    provide a `.queryset` attribute.
    """
    perms_map = {
        'GET': [],
        'OPTIONS': [],
        'HEAD': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def get_required_object_permissions(self, method, model_cls):
        kwargs = {
            'app_label': model_cls._meta.app_label,
            'model_name': model_cls._meta.model_name
        }

        if method not in self.perms_map:
            raise exceptions.MethodNotAllowed(method)

        return [perm % kwargs for perm in self.perms_map[method]]

    def has_object_permission(self, request, view, obj):
        # authentication checks have already executed via has_permission
        queryset = self._queryset(view)
        model_cls = queryset.model
        user = request.user

        perms = self.get_required_object_permissions(request.method, model_cls)

        if not user.has_perms(perms, obj):
            # If the user does not have permissions we need to determine if
            # they have read permissions to see 403, or not, and simply see
            # a 404 response.

            if request.method in SAFE_METHODS:
                # Read permissions already checked and failed, no need
                # to make another lookup.
                raise Http404

            read_perms = self.get_required_object_permissions('GET', model_cls)
            if not user.has_perms(read_perms, obj):
                raise Http404

            # Has read permissions.
            return False

        return True

    async def ahas_object_permission(self, request, view, obj):
        if overrides_sync_only(self, DjangoObjectPermissions, 'has_object_permission'):
            return await sync_to_async(self.has_object_permission)(request, view, obj)

        # authentication checks have already executed via ahas_permission
        queryset = await self._aqueryset(view)
        model_cls = queryset.model
        user = request.user

        perms = self.get_required_object_permissions(request.method, model_cls)

        if not await user.ahas_perms(perms, obj):
            if request.method in SAFE_METHODS:
                raise Http404

            read_perms = self.get_required_object_permissions('GET', model_cls)
            if not await user.ahas_perms(read_perms, obj):
                raise Http404

            # Has read permissions.
            return False

        return True
