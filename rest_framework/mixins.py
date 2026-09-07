"""
Basic building blocks for generic class based views.

We don't bind behavior to http method handlers yet,
which allows mixin classes to be composed in interesting ways.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.settings import api_settings


class CreateModelMixin:
    """
    Create a model instance.
    """
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        serializer.save()

    def get_success_headers(self, data):
        try:
            return {'Location': str(data[api_settings.URL_FIELD_NAME])}
        except (TypeError, KeyError):
            return {}


class ListModelMixin:
    """
    List a queryset.
    """
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class RetrieveModelMixin:
    """
    Retrieve a model instance.
    """
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class UpdateModelMixin:
    """
    Update a model instance.
    """
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def perform_update(self, serializer):
        serializer.save()

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)


class DestroyModelMixin:
    """
    Destroy a model instance.
    """
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        instance.delete()


# Asynchronous counterparts of the mixins above, for use with async views.
#
# The action names (`create()`, `list()`, ...) are deliberately kept the same
# as the synchronous mixins, so that routers, the `.action` attribute, and any
# code relying on action names keep working unchanged.

class AsyncCreateModelMixin:
    """
    Create a model instance, asynchronously.
    """
    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        await serializer.ais_valid(raise_exception=True)
        await self.aperform_create(serializer)
        data = await serializer.adata()
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    async def aperform_create(self, serializer):
        await serializer.asave()

    def get_success_headers(self, data):
        try:
            return {'Location': str(data[api_settings.URL_FIELD_NAME])}
        except (TypeError, KeyError):
            return {}


class AsyncListModelMixin:
    """
    List a queryset, asynchronously.
    """
    async def list(self, request, *args, **kwargs):
        queryset = await self.afilter_queryset(self.get_queryset())

        page = await self.apaginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(await serializer.adata())

        serializer = self.get_serializer(queryset, many=True)
        return Response(await serializer.adata())


class AsyncRetrieveModelMixin:
    """
    Retrieve a model instance, asynchronously.
    """
    async def retrieve(self, request, *args, **kwargs):
        instance = await self.aget_object()
        serializer = self.get_serializer(instance)
        return Response(await serializer.adata())


class AsyncUpdateModelMixin:
    """
    Update a model instance, asynchronously.
    """
    async def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = await self.aget_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        await serializer.ais_valid(raise_exception=True)
        await self.aperform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(await serializer.adata())

    async def aperform_update(self, serializer):
        await serializer.asave()

    async def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return await self.update(request, *args, **kwargs)


class AsyncDestroyModelMixin:
    """
    Destroy a model instance, asynchronously.
    """
    async def destroy(self, request, *args, **kwargs):
        instance = await self.aget_object()
        await self.aperform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    async def aperform_destroy(self, instance):
        await instance.adelete()
