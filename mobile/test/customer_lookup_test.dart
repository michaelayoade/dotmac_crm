import 'package:dio/dio.dart';
import 'package:dotmac_field/core/api/api_client.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:dotmac_field/features/customers/customer_lookup_screen.dart';
import 'package:dotmac_field/features/customers/customer_models.dart';
import 'package:dotmac_field/features/customers/customer_providers.dart';
import 'package:flutter/material.dart';
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
              'phone': '+2348012345678',
              'address_text': '12 Fiber Street',
              'account_status': 'active',
              'service_plan': '100 Mbps',
              'recent_jobs': [
                {'title': 'Install router', 'status': 'completed'},
              ],
              'recent_tickets': [
                {'title': 'Slow internet', 'status': 'open'},
              ],
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
    expect(customers.single.phone, '+2348012345678');
    expect(customers.single.addressText, '12 Fiber Street');
    expect(customers.single.accountStatus, 'active');
    expect(customers.single.servicePlan, '100 Mbps');
    expect(customers.single.recentJobs.single.title, 'Install router');
    expect(customers.single.recentTickets.single.title, 'Slow internet');
  });

  testWidgets('customer detail shows profile fields and launches actions', (
    tester,
  ) async {
    final launched = <Uri>[];
    const customer = CustomerLookupResult(
      id: 'customer-1',
      label: 'Ada Customer',
      ref: 'person:customer-1',
      explicitEmail: 'ada@example.com',
      phone: '+234 801 234 5678',
      addressText: '12 Fiber Street',
      accountStatus: 'active',
      servicePlan: '100 Mbps',
      recentJobs: [
        CustomerRecentWork(title: 'Install router', status: 'completed'),
      ],
      recentTickets: [
        CustomerRecentWork(title: 'Slow internet', status: 'open'),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          customerUriLauncherProvider.overrideWithValue((uri) async {
            launched.add(uri);
            return true;
          }),
        ],
        child: const MaterialApp(
          home: CustomerDetailScreen(customer: customer),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Ada Customer'), findsOneWidget);
    expect(find.text('+234 801 234 5678'), findsOneWidget);
    expect(find.text('ada@example.com'), findsOneWidget);
    expect(find.text('12 Fiber Street'), findsOneWidget);
    expect(find.text('active'), findsOneWidget);
    expect(find.text('100 Mbps'), findsOneWidget);
    expect(find.text('Install router'), findsOneWidget);

    final customerDetail = find.byType(Scrollable).first;
    await tester.scrollUntilVisible(
      find.text('Slow internet'),
      120,
      scrollable: customerDetail,
    );
    expect(find.text('Slow internet'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.widgetWithText(FilledButton, 'Call'),
      -120,
      scrollable: customerDetail,
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Call'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'WhatsApp'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Email'));
    await tester.pumpAndSettle();

    expect(launched, [
      Uri.parse('tel:+234 801 234 5678'),
      Uri.parse('https://wa.me/2348012345678'),
      Uri.parse('mailto:ada@example.com'),
    ]);
  });
}
