import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../auth/auth_state.dart';
import 'customer_models.dart';

typedef UriLauncher = Future<bool> Function(Uri uri);

class CustomerLookupRepository {
  const CustomerLookupRepository(this._ref);

  final Ref _ref;

  Future<List<CustomerLookupResult>> search(String query) async {
    final term = query.trim();
    if (term.isEmpty) return const [];
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get(
          '/api/v1/field/sales-orders/customers/search',
          queryParameters: {'q': term, 'limit': 30},
        );
    return _items(response.data).map(CustomerLookupResult.fromJson).toList();
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

final customerLookupRepositoryProvider = Provider<CustomerLookupRepository>(
  CustomerLookupRepository.new,
);

final customerUriLauncherProvider = Provider<UriLauncher>((ref) => launchUrl);

final customerLookupQueryProvider = StateProvider.autoDispose<String>(
  (ref) => '',
);

final customerLookupResultsProvider =
    FutureProvider.autoDispose<List<CustomerLookupResult>>((ref) {
      final query = ref.watch(customerLookupQueryProvider);
      return ref.watch(customerLookupRepositoryProvider).search(query);
    });
