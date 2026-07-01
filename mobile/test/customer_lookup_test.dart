import 'package:dio/dio.dart';
import 'package:dotmac_field/core/api/api_client.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:dotmac_field/features/customers/customer_providers.dart';
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

  test('search reads CRM customer lookup results', () async {
    adapter.on('GET', '/api/v1/field/sales-orders/customers/search', (options) {
      expect(options.queryParameters['q'], 'ada');
      expect(options.queryParameters['limit'], 30);
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
        .read(customerLookupRepositoryProvider)
        .search('ada');

    expect(customers.single.label, 'Ada Customer (ada@example.com)');
    expect(customers.single.email, 'ada@example.com');
  });
}
