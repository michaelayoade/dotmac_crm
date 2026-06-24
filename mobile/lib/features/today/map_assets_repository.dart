import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/auth_state.dart';
import 'map_models.dart';

class MapAssetsRepository {
  const MapAssetsRepository(this._ref);

  final Ref _ref;

  Future<List<MapAsset>> fetchAssets(Set<String> types) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .get(
          '/api/v1/field/map-assets',
          queryParameters: {'types': types.join(','), 'limit': 1000},
        );
    final items = (response.data['items'] as List).cast<Map>();
    return items
        .map((item) => MapAsset.fromJson(item.cast<String, dynamic>()))
        .toList();
  }

  Future<MapAsset> updateLocation({
    required String type,
    required String id,
    required double latitude,
    required double longitude,
  }) async {
    final response = await _ref
        .read(apiClientProvider)
        .dio
        .patch(
          '/api/v1/field/map-assets/$type/$id/location',
          data: {'latitude': latitude, 'longitude': longitude},
        );
    return MapAsset.fromJson((response.data as Map).cast<String, dynamic>());
  }
}

final mapAssetsRepositoryProvider = Provider<MapAssetsRepository>(
  MapAssetsRepository.new,
);

final selectedMapAssetTypesProvider = StateProvider<Set<String>>(
  (ref) => {...defaultMapAssetTypes},
);

final mapAssetsProvider = FutureProvider<List<MapAsset>>((ref) {
  final types = ref.watch(selectedMapAssetTypesProvider);
  if (types.isEmpty) return Future.value([]);
  return ref.watch(mapAssetsRepositoryProvider).fetchAssets(types);
});
