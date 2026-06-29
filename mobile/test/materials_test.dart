import 'package:dio/dio.dart';
import 'package:dotmac_field/core/api/api_client.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:dotmac_field/features/materials/material_models.dart';
import 'package:dotmac_field/features/materials/materials_providers.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'helpers/fake_http.dart';

void main() {
  late ProviderContainer container;
  late FakeHttpAdapter adapter;

  setUp(() async {
    adapter = FakeHttpAdapter();
    final store = InMemoryTokenStore();
    await store.save(
      accessToken: fakeJwt(
        expiry: DateTime.now().toUtc().add(const Duration(minutes: 15)),
      ),
      refreshToken: 'refresh',
    );
    final dio = Dio(BaseOptions(baseUrl: 'https://test.local'))
      ..httpClientAdapter = adapter;
    final client = ApiClient(
      baseUrl: 'https://test.local',
      tokenStore: store,
      dio: dio,
    );
    container = ProviderContainer(
      overrides: [apiClientProvider.overrideWithValue(client)],
    );
  });

  tearDown(() => container.dispose());

  test('searchInventory reads field inventory items', () async {
    adapter.on('GET', '/api/v1/field/inventory/items', (options) {
      expect(options.queryParameters['q'], 'cable');
      return (
        200,
        {
          'items': [
            {
              'id': 'item-1',
              'name': 'Drop cable',
              'sku': 'DC-100',
              'unit': 'm',
              'available_quantity': 50,
            },
          ],
        },
      );
    });

    final items = await container
        .read(materialsRepositoryProvider)
        .searchInventory('cable');

    expect(items.single.name, 'Drop cable');
    expect(items.single.availableQuantity, 50);
  });

  test('createRequest posts request payload with items', () async {
    adapter.on('POST', '/api/v1/field/material-requests', (options) {
      final data = (options.data as Map).cast<String, dynamic>();
      expect(data['priority'], 'high');
      expect(data['work_order_id'], 'wo-1');
      expect(data['submit'], isTrue);
      expect(data['items'], [
        {'item_id': 'item-1', 'quantity': 2},
      ]);
      return (
        201,
        {
          'id': 'mr-1',
          'number': 'MR-0001',
          'status': 'submitted',
          'priority': 'high',
          'items': [
            {
              'id': 'line-1',
              'item_id': 'item-1',
              'quantity': 2,
              'item_name': 'Drop cable',
            },
          ],
        },
      );
    });

    final request = await container
        .read(materialsRepositoryProvider)
        .createRequest(
          priority: 'high',
          workOrderId: 'wo-1',
          items: [
            const MaterialRequestItemDraft(
              item: InventoryItem(id: 'item-1', name: 'Drop cable'),
              quantity: 2,
            ),
          ],
        );

    expect(request.number, 'MR-0001');
    expect(request.items.single.itemName, 'Drop cable');
  });
}
