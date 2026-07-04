import 'dart:ffi' hide Size;

import 'package:dio/dio.dart';
import 'package:dotmac_field/core/api/api_client.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/core/offline/database.dart';
import 'package:dotmac_field/core/offline/draft_store.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:dotmac_field/features/materials/material_models.dart';
import 'package:dotmac_field/features/materials/materials_providers.dart';
import 'package:dotmac_field/features/sales/sales_models.dart';
import 'package:dotmac_field/features/sales/sales_providers.dart';
import 'package:dotmac_field/features/sales/sales_screen.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqlite3/open.dart';

import 'helpers/fake_http.dart';

void main() {
  late ProviderContainer container;
  late FakeHttpAdapter adapter;

  setUpAll(() {
    open.overrideFor(
      OperatingSystem.linux,
      () => DynamicLibrary.open('libsqlite3.so.0'),
    );
  });

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
          'person_id': 'customer-1',
          'order_number': 'SO-000001',
          'status': 'draft',
          'payment_status': 'pending',
          'currency': 'NGN',
          'subtotal': '30000.00',
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
    expect(order.personId, 'customer-1');
    expect(order.total, 30000);
    expect(order.lines.single.description, 'Router');
  });

  test(
    'fetchOrder resolves an order from the field sales order list',
    () async {
      adapter.on('GET', '/api/v1/field/sales-orders', (_) {
        return (
          200,
          {
            'items': [
              {
                'id': 'order-1',
                'person_id': 'customer-1',
                'customer_label': 'Ada Customer',
                'order_number': 'SO-000001',
                'status': 'draft',
                'payment_status': 'pending',
                'currency': 'NGN',
                'subtotal': '30000.00',
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
            ],
          },
        );
      });

      final order = await container
          .read(salesRepositoryProvider)
          .fetchOrder('order-1');

      expect(order.customerDisplay, 'Ada Customer');
      expect(order.subtotal, 30000);
      expect(order.lines.single.amount, 30000);
    },
  );

  testWidgets('sales order detail shows customer, status, payment and lines', (
    tester,
  ) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          salesOrderProvider('order-1').overrideWith(
            (ref) async => SalesOrder.fromJson({
              'id': 'order-1',
              'person_id': 'customer-1',
              'customer_label': 'Ada Customer',
              'order_number': 'SO-000001',
              'status': 'draft',
              'payment_status': 'pending',
              'currency': 'NGN',
              'subtotal': '30000.00',
              'total': '30000.00',
              'balance_due': '30000.00',
              'notes': 'New install',
              'lines': [
                {
                  'id': 'line-1',
                  'description': 'Router',
                  'quantity': '2',
                  'unit_price': '15000.00',
                  'amount': '30000.00',
                },
              ],
            }),
          ),
        ],
        child: const MaterialApp(home: SalesOrderDetailScreen(id: 'order-1')),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('SO-000001'), findsOneWidget);
    expect(find.text('Ada Customer'), findsOneWidget);
    expect(find.text('draft'), findsOneWidget);
    expect(find.text('pending'), findsOneWidget);
    expect(find.text('Router'), findsOneWidget);
    expect(find.text('New install'), findsOneWidget);
  });

  testWidgets('new sales order form renders on phone width', (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          customerSearchProvider.overrideWith((ref) async => const []),
          inventorySearchProvider.overrideWith((ref) async => const []),
        ],
        child: const MaterialApp(home: NewSalesOrderScreen()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('New sales order'), findsOneWidget);
    expect(find.text('Submit sales order'), findsOneWidget);
    expect(find.text('Save draft'), findsOneWidget);
  });

  testWidgets('new sales order draft lines can be edited and removed', (
    tester,
  ) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          customerSearchProvider.overrideWith((ref) async => const []),
          inventorySearchProvider.overrideWith((ref) async => const []),
        ],
        child: const MaterialApp(home: NewSalesOrderScreen()),
      ),
    );
    await tester.pumpAndSettle();
    final orderForm = find.byType(Scrollable).first;

    await tester.enterText(
      find.widgetWithText(TextField, 'Description'),
      'Router',
    );
    await tester.enterText(find.widgetWithText(TextField, 'Quantity'), '2');
    await tester.enterText(
      find.widgetWithText(TextField, 'Unit price'),
      '15000',
    );
    final addLine = find.byKey(const Key('add-sales-line-action'));
    await tester.scrollUntilVisible(addLine, 120, scrollable: orderForm);
    await tester.drag(orderForm, const Offset(0, -120));
    await tester.pumpAndSettle();
    await tester.tap(addLine);
    await tester.pumpAndSettle();

    expect(find.text('Router'), findsOneWidget);
    expect(find.text('NGN 30000.00'), findsWidgets);

    await tester.tap(find.byTooltip('Edit line'));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Router'), findsOneWidget);
    expect(find.text('NGN 30000.00'), findsNothing);

    await tester.scrollUntilVisible(addLine, 120, scrollable: orderForm);
    await tester.drag(orderForm, const Offset(0, -120));
    await tester.pumpAndSettle();
    await tester.tap(addLine);
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Remove line'));
    await tester.pumpAndSettle();

    expect(find.text('Router'), findsNothing);
  });

  test('DraftStore saves, loads and deletes a sales order draft', () async {
    final db = AppDatabase(NativeDatabase.memory());
    addTearDown(db.close);
    final store = DraftStore(db);

    await store.save(
      id: salesOrderDraftId,
      type: 'sales_order',
      payload: {
        'customer': {
          'id': 'customer-1',
          'label': 'Ada Customer',
          'ref': 'person:customer-1',
        },
        'notes': 'Install tomorrow',
        'lines': [
          {'description': 'Router', 'quantity': 1, 'unit_price': 15000},
        ],
      },
    );

    final draft = await store.load(salesOrderDraftId);
    expect((draft?['customer'] as Map?)?['label'], 'Ada Customer');
    expect(draft?['notes'], 'Install tomorrow');

    await store.delete(salesOrderDraftId);
    expect(await store.load(salesOrderDraftId), isNull);
  });
}
