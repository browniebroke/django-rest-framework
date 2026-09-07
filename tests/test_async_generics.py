"""
Tests for asynchronous support in generic views, mixins, viewsets, pagination,
filter backends and serializers.
"""
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import include, path

from rest_framework import filters, generics, pagination, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.test import AsyncAPIClient
from rest_framework.viewsets import (
    AsyncModelViewSet, AsyncReadOnlyModelViewSet, GenericViewSet, ModelViewSet,
    ViewSet
)
from tests.models import BasicModel, ForeignKeySource, ForeignKeyTarget


class BasicSerializer(serializers.ModelSerializer):
    class Meta:
        model = BasicModel
        fields = '__all__'


class ForeignKeySourceSerializer(serializers.ModelSerializer):
    target_name = serializers.CharField(source='target.name', read_only=True)

    class Meta:
        model = ForeignKeySource
        fields = ['id', 'name', 'target', 'target_name']


class SyncOnlyFilterBackend(filters.BaseFilterBackend):
    """
    A third-party style filter backend performing a blocking database query
    while filtering, as django-filter does when validating filter values.
    """
    def filter_queryset(self, request, queryset, view):
        text = request.query_params.get('exact')
        if text and BasicModel.objects.filter(text=text).exists():
            return queryset.filter(text=text)
        return queryset


class PageNumberPaginationWithSize(pagination.PageNumberPagination):
    page_size = 2
    page_size_query_param = 'page_size'


class LimitOffsetPaginationWithDefault(pagination.LimitOffsetPagination):
    default_limit = 2


class CursorPaginationBySize(pagination.CursorPagination):
    page_size = 2
    ordering = 'text'


class SyncOnlyPagination(pagination.BasePagination):
    """
    A third-party style pagination class only implementing the sync API.
    """
    def paginate_queryset(self, queryset, request, view=None):
        self.count = queryset.count()
        return list(queryset[:1])

    def get_paginated_response(self, data):
        return Response({'count': self.count, 'results': data})


class BasicListCreateView(generics.AsyncListCreateAPIView):
    queryset = BasicModel.objects.all()
    serializer_class = BasicSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, SyncOnlyFilterBackend]
    search_fields = ['text']
    ordering_fields = ['text']
    pagination_class = None
    authentication_classes = []
    permission_classes = []


class BasicDetailView(generics.AsyncRetrieveUpdateDestroyAPIView):
    queryset = BasicModel.objects.all()
    serializer_class = BasicSerializer
    authentication_classes = []
    permission_classes = []


class ForeignKeySourceListView(generics.AsyncListCreateAPIView):
    queryset = ForeignKeySource.objects.all()
    serializer_class = ForeignKeySourceSerializer
    pagination_class = None
    authentication_classes = []
    permission_classes = []


def make_paginated_view(pagination_class):
    class View(generics.AsyncListAPIView):
        queryset = BasicModel.objects.order_by('pk')
        serializer_class = BasicSerializer
        authentication_classes = []
        permission_classes = []
    View.pagination_class = pagination_class
    return View


class BasicViewSet(AsyncModelViewSet):
    queryset = BasicModel.objects.all()
    serializer_class = BasicSerializer
    authentication_classes = []
    permission_classes = []

    @action(detail=False)
    async def count(self, request):
        return Response({'count': await self.get_queryset().acount(), 'action': self.action})

    @action(detail=True, methods=['post'])
    async def shout(self, request, pk=None):
        instance = await self.aget_object()
        instance.text = instance.text.upper()
        await instance.asave()
        return Response({'text': instance.text, 'action': self.action})


class ReadOnlyViewSet(AsyncReadOnlyModelViewSet):
    queryset = BasicModel.objects.all()
    serializer_class = BasicSerializer
    authentication_classes = []
    permission_classes = []


router = SimpleRouter()
router.register('viewset', BasicViewSet, basename='basic')
router.register('readonly', ReadOnlyViewSet, basename='readonly')

urlpatterns = [
    path('basic/', BasicListCreateView.as_view()),
    path('basic/<int:pk>/', BasicDetailView.as_view()),
    path('sources/', ForeignKeySourceListView.as_view()),
    path('page-number/', make_paginated_view(PageNumberPaginationWithSize).as_view()),
    path('limit-offset/', make_paginated_view(LimitOffsetPaginationWithDefault).as_view()),
    path('cursor/', make_paginated_view(CursorPaginationBySize).as_view()),
    path('sync-only/', make_paginated_view(SyncOnlyPagination).as_view()),
    path('', include(router.urls)),
]


@override_settings(ROOT_URLCONF='tests.test_async_generics')
class AsyncGenericViewTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()
        self.items = [BasicModel.objects.create(text=text) for text in ['a', 'b', 'c']]

    async def test_list(self):
        response = await self.client.get('/basic/')
        assert response.status_code == status.HTTP_200_OK
        assert [item['text'] for item in response.data] == ['a', 'b', 'c']

    async def test_list_with_filter_backends(self):
        response = await self.client.get('/basic/', {'search': 'b'})
        assert [item['text'] for item in response.data] == ['b']

        response = await self.client.get('/basic/', {'ordering': '-text'})
        assert [item['text'] for item in response.data] == ['c', 'b', 'a']

        # The sync-only backend is run in a thread.
        response = await self.client.get('/basic/', {'exact': 'c'})
        assert [item['text'] for item in response.data] == ['c']

    async def test_create(self):
        response = await self.client.post('/basic/', {'text': 'd'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['text'] == 'd'
        assert await BasicModel.objects.filter(text='d').aexists()

    async def test_create_invalid(self):
        response = await self.client.post('/basic/', {'text': ''}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'text' in response.data

    async def test_retrieve(self):
        response = await self.client.get('/basic/%d/' % self.items[0].pk)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'id': self.items[0].pk, 'text': 'a'}

    async def test_retrieve_missing(self):
        response = await self.client.get('/basic/999/')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_update(self):
        response = await self.client.put('/basic/%d/' % self.items[0].pk, {'text': 'z'}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['text'] == 'z'
        instance = await BasicModel.objects.aget(pk=self.items[0].pk)
        assert instance.text == 'z'

    async def test_partial_update(self):
        response = await self.client.patch('/basic/%d/' % self.items[0].pk, {}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['text'] == 'a'

    async def test_destroy(self):
        response = await self.client.delete('/basic/%d/' % self.items[0].pk)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not await BasicModel.objects.filter(pk=self.items[0].pk).aexists()

    async def test_related_fields_are_serialized(self):
        # Related field access would be a blocking operation from the event
        # loop, but the serializer runs in a thread.
        target = await ForeignKeyTarget.objects.acreate(name='target')
        await ForeignKeySource.objects.acreate(name='source', target=target)

        response = await self.client.get('/sources/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]['target'] == target.pk
        assert response.data[0]['target_name'] == 'target'

        # Validating the related field also requires a database lookup.
        response = await self.client.post('/sources/', {'name': 'new', 'target': target.pk}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['target_name'] == 'target'

        response = await self.client.post('/sources/', {'name': 'new', 'target': 999}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@override_settings(ROOT_URLCONF='tests.test_async_generics')
class AsyncPaginationTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()
        for text in ['a', 'b', 'c', 'd', 'e']:
            BasicModel.objects.create(text=text)

    async def test_page_number_pagination(self):
        response = await self.client.get('/page-number/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 5
        assert [item['text'] for item in response.data['results']] == ['a', 'b']
        assert response.data['previous'] is None
        assert response.data['next'].endswith('/page-number/?page=2')

        response = await self.client.get('/page-number/', {'page': 'last'})
        assert [item['text'] for item in response.data['results']] == ['e']

        response = await self.client.get('/page-number/', {'page': 4})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_page_number_pagination_browsable_api(self):
        response = await self.client.get('/page-number/', {'format': 'api'})
        assert response.status_code == status.HTTP_200_OK
        assert b'class="pagination"' in response.content

    async def test_limit_offset_pagination(self):
        response = await self.client.get('/limit-offset/', {'offset': 2})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 5
        assert [item['text'] for item in response.data['results']] == ['c', 'd']

        response = await self.client.get('/limit-offset/', {'offset': 10})
        assert response.data['results'] == []

    async def test_cursor_pagination(self):
        response = await self.client.get('/cursor/')
        assert response.status_code == status.HTTP_200_OK
        assert [item['text'] for item in response.data['results']] == ['a', 'b']
        assert response.data['previous'] is None

        response = await self.client.get(response.data['next'])
        assert [item['text'] for item in response.data['results']] == ['c', 'd']

        response = await self.client.get(response.data['next'])
        assert [item['text'] for item in response.data['results']] == ['e']
        assert response.data['next'] is None

        response = await self.client.get(response.data['previous'])
        assert [item['text'] for item in response.data['results']] == ['c', 'd']

    async def test_sync_only_pagination_runs_in_thread(self):
        response = await self.client.get('/sync-only/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 5
        assert [item['text'] for item in response.data['results']] == ['a']


@override_settings(ROOT_URLCONF='tests.test_async_generics')
class AsyncViewSetTests(TestCase):
    def setUp(self):
        self.client = AsyncAPIClient()
        self.items = [BasicModel.objects.create(text=text) for text in ['a', 'b']]

    def test_view_is_async(self):
        assert BasicViewSet.view_is_async is True
        assert ReadOnlyViewSet.view_is_async is True
        assert ModelViewSet.view_is_async is False
        assert GenericViewSet.view_is_async is False
        assert ViewSet.view_is_async is False

    def test_mixed_actions_are_rejected(self):
        class Mixed(AsyncModelViewSet):
            def list(self, request):
                return Response()

        with self.assertRaises(ImproperlyConfigured):
            Mixed.view_is_async

        class MixedExtraAction(AsyncReadOnlyModelViewSet):
            @action(detail=False)
            def extra(self, request):
                return Response()

        with self.assertRaises(ImproperlyConfigured):
            MixedExtraAction.as_view({'get': 'list'})

    def test_undecorated_custom_action_must_match(self):
        class Custom(ViewSet):
            async def custom(self, request):
                return Response()

        # Without `@action`, the custom action isn't taken into account when
        # determining whether the viewset is async.
        assert Custom.view_is_async is False
        with self.assertRaises(ImproperlyConfigured):
            Custom.as_view({'get': 'custom'})

        class SyncCustom(ViewSet):
            def custom(self, request):
                return Response()

        assert SyncCustom.as_view({'get': 'custom'})

    def test_extra_actions_are_registered_by_router(self):
        assert {a.__name__ for a in BasicViewSet.get_extra_actions()} == {'count', 'shout'}

    async def test_list(self):
        response = await self.client.get('/viewset/')
        assert response.status_code == status.HTTP_200_OK
        assert [item['text'] for item in response.data] == ['a', 'b']

    async def test_crud(self):
        response = await self.client.post('/viewset/', {'text': 'c'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        pk = response.data['id']

        response = await self.client.get('/viewset/%d/' % pk)
        assert response.data['text'] == 'c'

        response = await self.client.patch('/viewset/%d/' % pk, {'text': 'd'}, format='json')
        assert response.data['text'] == 'd'

        response = await self.client.put('/viewset/%d/' % pk, {'text': 'e'}, format='json')
        assert response.data['text'] == 'e'

        response = await self.client.delete('/viewset/%d/' % pk)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not await BasicModel.objects.filter(pk=pk).aexists()

    async def test_extra_actions(self):
        response = await self.client.get('/viewset/count/')
        assert response.data == {'count': 2, 'action': 'count'}

        response = await self.client.post('/viewset/%d/shout/' % self.items[0].pk)
        assert response.data == {'text': 'A', 'action': 'shout'}

    async def test_options(self):
        response = await self.client.options('/viewset/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['name'] == 'Basic List'
        assert 'POST' in response.data['actions']

    async def test_method_not_allowed(self):
        response = await self.client.post('/readonly/')
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    async def test_read_only(self):
        response = await self.client.get('/readonly/%d/' % self.items[0].pk)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['text'] == 'a'


class AsyncSerializerTests(TestCase):
    def test_ais_valid_asave_adata(self):
        target = ForeignKeyTarget.objects.create(name='target')

        async def run():
            serializer = ForeignKeySourceSerializer(data={'name': 'source', 'target': target.pk})
            assert await serializer.ais_valid() is True
            instance = await serializer.asave()
            assert instance.pk is not None
            assert instance.target == target
            data = await serializer.adata()
            assert data == {
                'id': instance.pk, 'name': 'source', 'target': target.pk, 'target_name': 'target'
            }

            serializer = ForeignKeySourceSerializer(data={'name': 'source', 'target': 999})
            assert await serializer.ais_valid() is False
            assert 'target' in serializer.errors

            serializer = ForeignKeySourceSerializer(data={'name': 'source', 'target': 999})
            with self.assertRaises(serializers.ValidationError):
                await serializer.ais_valid(raise_exception=True)

        async_to_sync(run)()

    def test_adata_for_many(self):
        for text in ['a', 'b']:
            BasicModel.objects.create(text=text)

        async def run():
            serializer = BasicSerializer(BasicModel.objects.all(), many=True)
            data = await serializer.adata()
            assert [item['text'] for item in data] == ['a', 'b']
            # The result is cached, as with `.data`.
            assert serializer.data == data

        async_to_sync(run)()
