import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/auth_state.dart';
import 'material_models.dart';

class MaterialsRepository {
  const MaterialsRepository(this._ref);

  final Ref _ref;

  Future<List<InventoryItem>> searchInventory(String query) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get(
          '/api/v1/field/inventory/items',
          queryParameters: {
            if (query.trim().isNotEmpty) 'q': query.trim(),
            'limit': 30,
          },
        );
    return _items(response.data).map(InventoryItem.fromJson).toList();
  }

  Future<List<InventoryLocation>> fetchLocations() async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get(
          '/api/v1/field/inventory/locations',
          queryParameters: {'limit': 100},
        );
    return _items(response.data).map(InventoryLocation.fromJson).toList();
  }

  Future<List<MaterialRequest>> fetchRequests() async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get(
          '/api/v1/field/material-requests',
          queryParameters: {'limit': 100},
        );
    return _items(response.data).map(MaterialRequest.fromJson).toList();
  }

  Future<MaterialRequest> fetchRequest(String id) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get('/api/v1/field/material-requests/$id');
    return MaterialRequest.fromJson(
      (response.data as Map).cast<String, dynamic>(),
    );
  }

  Future<MaterialRequest> createRequest({
    required String priority,
    required List<MaterialRequestItemDraft> items,
    String? notes,
    String? workOrderId,
    String? projectId,
    String? ticketId,
    String? sourceLocationId,
    String? destinationLocationId,
    bool submit = true,
  }) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .post(
          '/api/v1/field/material-requests',
          data: {
            'priority': priority,
            if (notes != null && notes.trim().isNotEmpty) 'notes': notes.trim(),
            if (workOrderId != null && workOrderId.trim().isNotEmpty)
              'work_order_id': workOrderId.trim(),
            if (projectId != null && projectId.trim().isNotEmpty)
              'project_id': projectId.trim(),
            if (ticketId != null && ticketId.trim().isNotEmpty)
              'ticket_id': ticketId.trim(),
            'source_location_id': ?sourceLocationId,
            'destination_location_id': ?destinationLocationId,
            'items': items.map((item) => item.toJson()).toList(),
            'submit': submit,
          },
        );
    return MaterialRequest.fromJson(
      (response.data as Map).cast<String, dynamic>(),
    );
  }

  Future<MaterialRequest> submitRequest(String id) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .post('/api/v1/field/material-requests/$id/submit');
    return MaterialRequest.fromJson(
      (response.data as Map).cast<String, dynamic>(),
    );
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

final materialsRepositoryProvider = Provider<MaterialsRepository>(
  MaterialsRepository.new,
);

final materialRequestsProvider = FutureProvider<List<MaterialRequest>>(
  (ref) => ref.watch(materialsRepositoryProvider).fetchRequests(),
);

final materialRequestProvider = FutureProvider.family<MaterialRequest, String>(
  (ref, id) => ref.watch(materialsRepositoryProvider).fetchRequest(id),
);

final inventorySearchQueryProvider = StateProvider.autoDispose<String>(
  (ref) => '',
);

final inventorySearchProvider = FutureProvider.autoDispose<List<InventoryItem>>(
  (ref) {
    final query = ref.watch(inventorySearchQueryProvider);
    return ref.watch(materialsRepositoryProvider).searchInventory(query);
  },
);

final inventoryLocationsProvider = FutureProvider<List<InventoryLocation>>(
  (ref) => ref.watch(materialsRepositoryProvider).fetchLocations(),
);
