import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/auth_state.dart';
import 'sales_models.dart';

class SalesRepository {
  const SalesRepository(this._ref);

  final Ref _ref;

  Future<List<SalesCustomer>> searchCustomers(String query) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get(
          '/api/v1/field/sales-orders/customers/search',
          queryParameters: {'q': query.trim(), 'limit': 20},
        );
    return _items(response.data).map(SalesCustomer.fromJson).toList();
  }

  Future<List<SalesOrder>> fetchOrders() async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get('/api/v1/field/sales-orders', queryParameters: {'limit': 100});
    return _items(response.data).map(SalesOrder.fromJson).toList();
  }

  Future<SalesOrder> fetchOrder(String id) async {
    final orders = await fetchOrders();
    return orders.firstWhere((order) => order.id == id);
  }

  Future<SalesOrder> createOrder({
    required SalesCustomer customer,
    required List<SalesOrderLineDraft> lines,
    String? notes,
    String currency = 'NGN',
  }) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .post(
          '/api/v1/field/sales-orders',
          data: {
            'person_id': customer.id,
            'currency': currency,
            if (notes != null && notes.trim().isNotEmpty) 'notes': notes.trim(),
            'lines': lines.map((line) => line.toJson()).toList(),
          },
        );
    return SalesOrder.fromJson((response.data as Map).cast<String, dynamic>());
  }
}

List<Map<String, dynamic>> _items(Object? data) {
  if (data is Map && data['items'] is List) {
    return (data['items'] as List)
        .cast<Map>()
        .map((item) => item.cast<String, dynamic>())
        .toList();
  }
  if (data is List) {
    return data
        .cast<Map>()
        .map((item) => item.cast<String, dynamic>())
        .toList();
  }
  return const [];
}

final salesRepositoryProvider = Provider<SalesRepository>(SalesRepository.new);

final salesOrdersProvider = FutureProvider<List<SalesOrder>>(
  (ref) => ref.watch(salesRepositoryProvider).fetchOrders(),
);

final salesOrderProvider = FutureProvider.family<SalesOrder, String>(
  (ref, id) => ref.watch(salesRepositoryProvider).fetchOrder(id),
);

final customerSearchQueryProvider = StateProvider.autoDispose<String>(
  (ref) => '',
);

final customerSearchProvider = FutureProvider.autoDispose<List<SalesCustomer>>((
  ref,
) {
  final query = ref.watch(customerSearchQueryProvider);
  if (query.trim().isEmpty) return const [];
  return ref.watch(salesRepositoryProvider).searchCustomers(query);
});
