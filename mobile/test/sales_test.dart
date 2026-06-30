import 'package:dio/dio.dart';
import 'package:dotmac_field/core/api/api_client.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:dotmac_field/features/materials/material_models.dart';
import 'package:dotmac_field/features/sales/sales_models.dart';
import 'package:dotmac_field/features/sales/sales_providers.dart';
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

  test('searchCustomers reads field customer suggestions', () async {
    adapter.on('GET', '/api/v1/field/sales-orders/customers/search', (options) {
      expect(options.queryParameters['q'], 'ada');
      return (
        200,
        {
          'items': [
            {
              'id': 'customer-1',
              'type': 'person',
              'label': 'Ada Customer (ada@example.com)',
              'ref': 'person:customer-1',
            },
          ],
        },
      );
    });

    final customers = await container
        .read(salesRepositoryProvider)
        .searchCustomers('ada');

    expect(customers.single.label, contains('Ada Customer'));
  });

  test('createOrder posts customer and lines', () async {
    adapter.on('POST', '/api/v1/field/sales-orders', (options) {
      final data = (options.data as Map).cast<String, dynamic>();
      expect(data['person_id'], 'customer-1');
      expect(data['notes'], 'New install');
      expect(data['lines'], [
        {
          'inventory_item_id': 'item-1',
          'description': 'Router',
          'quantity': 2.0,
          'unit_price': 15000.0,
        },
      ]);
      return (
        201,
        {
          'id': 'order-1',
          'order_number': 'SO-000001',
          'status': 'draft',
          'payment_status': 'pending',
          'currency': 'NGN',
          'total': '30000.00',
          'balance_due': '30000.00',
          'lines': [
            {
              'id': 'line-1',
              'inventory_item_id': 'item-1',
              'description': 'Router',
              'quantity': '2.000',
              'unit_price': '15000.00',
              'amount': '30000.00',
            },
          ],
        },
      );
    });

    final order = await container
        .read(salesRepositoryProvider)
        .createOrder(
          customer: const SalesCustomer(
            id: 'customer-1',
            label: 'Ada Customer',
            ref: 'person:customer-1',
          ),
          notes: 'New install',
          lines: [
            const SalesOrderLineDraft(
              item: InventoryItem(id: 'item-1', name: 'Router'),
              description: 'Router',
              quantity: 2,
              unitPrice: 15000,
            ),
          ],
        );

    expect(order.orderNumber, 'SO-000001');
    expect(order.total, 30000);
    expect(order.lines.single.description, 'Router');
  });
}
