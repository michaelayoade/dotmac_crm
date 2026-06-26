import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/location/map_coordinates.dart';
import '../execution/execution_controller.dart';
import '../jobs/job_models.dart';
import '../jobs/jobs_providers.dart';
import '../jobs/location_pin_screen.dart';
import 'asset_pin_screen.dart';
import 'map_assets_repository.dart';
import 'map_models.dart';

final mapPinsProvider = FutureProvider<List<JobPin>>((ref) async {
  final jobs = (await ref.watch(jobsListProvider.future)).jobs;
  final db = ref.watch(syncServiceProvider).db;
  final cached = await db.select(db.cachedJobs).get();
  final detailById = {for (final row in cached) row.id: row.detailJson};
  return buildJobPins(jobs, detailById);
});

class MapScreen extends ConsumerWidget {
  const MapScreen({super.key, this.showTiles = true});

  /// Disabled in widget tests so no tile HTTP requests are made.
  final bool showTiles;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pins = ref.watch(mapPinsProvider);
    final assets = ref.watch(mapAssetsProvider);
    final selectedTypes = ref.watch(selectedMapAssetTypesProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Job map'),
        actions: [
          pins.maybeWhen(
            data: (items) => TextButton.icon(
              key: const Key('edit-pins-button'),
              onPressed: () => _showPinListSheet(
                context,
                ref,
                items.where((pin) => pin.hasValidCoordinates).toList(),
                (assets.valueOrNull ?? const <MapAsset>[])
                    .where((asset) => asset.hasValidCoordinates)
                    .toList(),
              ),
              icon: const Icon(Icons.push_pin_outlined),
              label: const Text('Edit'),
            ),
            orElse: () => TextButton.icon(
              key: const Key('edit-pins-button'),
              onPressed: null,
              icon: const Icon(Icons.push_pin_outlined),
              label: const Text('Edit'),
            ),
          ),
        ],
      ),
      body: pins.when(
        data: (items) {
          final validPins = items
              .where((pin) => pin.hasValidCoordinates)
              .toList();
          final assetItems = (assets.valueOrNull ?? const <MapAsset>[])
              .where((asset) => asset.hasValidCoordinates)
              .toList();
          final center = validPins.isNotEmpty
              ? safeLatLng(validPins.first.latitude, validPins.first.longitude)!
              : assetItems.isNotEmpty
              ? safeLatLng(
                  assetItems.first.latitude,
                  assetItems.first.longitude,
                )!
              : defaultMapCenter;
          return Stack(
            children: [
              FlutterMap(
                options: MapOptions(
                  initialCenter: center,
                  initialZoom: 12,
                  cameraConstraint: finiteMapCameraConstraint,
                ),
                children: [
                  if (showTiles)
                    TileLayer(
                      urlTemplate:
                          'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                      userAgentPackageName: 'io.dotmac.dotmac_field',
                    ),
                  MarkerLayer(
                    markers: [
                      for (final asset in assetItems)
                        Marker(
                          point: safeLatLng(asset.latitude, asset.longitude)!,
                          width: 38,
                          height: 38,
                          child: GestureDetector(
                            key: Key('asset-${asset.type}-${asset.id}'),
                            onTap: () => _showAssetSheet(context, ref, asset),
                            child: Icon(
                              _assetIcon(asset.type),
                              size: 30,
                              color: _assetColor(asset.type),
                            ),
                          ),
                        ),
                      for (final pin in validPins)
                        Marker(
                          point: safeLatLng(pin.latitude, pin.longitude)!,
                          width: 44,
                          height: 44,
                          child: GestureDetector(
                            key: Key('pin-${pin.id}'),
                            onTap: () => _showJobSheet(context, ref, pin),
                            child: Icon(
                              Icons.location_pin,
                              size: 40,
                              color: AppColors.status(pin.status),
                            ),
                          ),
                        ),
                    ],
                  ),
                  if (showTiles)
                    const Align(
                      alignment: Alignment.bottomLeft,
                      child: Padding(
                        padding: EdgeInsets.all(4),
                        child: Text(
                          '© OpenStreetMap contributors',
                          style: TextStyle(fontSize: 10),
                        ),
                      ),
                    ),
                ],
              ),
              _LayerSelector(
                selectedTypes: selectedTypes,
                loadingAssets: assets.isLoading,
                onChanged: (type, selected) {
                  final next = {...selectedTypes};
                  if (selected) {
                    next.add(type);
                  } else {
                    next.remove(type);
                  }
                  ref.read(selectedMapAssetTypesProvider.notifier).state = next;
                },
              ),
            ],
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => const Center(child: Text('Could not load the map')),
      ),
    );
  }

  void _showJobSheet(BuildContext context, WidgetRef ref, JobPin pin) {
    showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: Icon(
                Icons.assignment_outlined,
                color: AppColors.status(pin.status),
              ),
              title: Text(pin.title),
              subtitle: Text(pin.status.replaceAll('_', ' ')),
              trailing: const Icon(Icons.chevron_right),
              onTap: () {
                Navigator.pop(sheetContext);
                context.push('/jobs/${pin.id}');
              },
            ),
            ListTile(
              leading: const Icon(Icons.push_pin_outlined),
              title: const Text('Edit pin location'),
              onTap: () async {
                Navigator.pop(sheetContext);
                final changed = await Navigator.of(context).push<bool>(
                  MaterialPageRoute(
                    builder: (_) => LocationPinScreen(
                      jobId: pin.id,
                      initialLocation: JobLocation(
                        latitude: pin.latitude,
                        longitude: pin.longitude,
                        source: 'cached',
                      ),
                    ),
                  ),
                );
                if (changed == true) ref.invalidate(mapPinsProvider);
              },
            ),
          ],
        ),
      ),
    );
  }

  void _showAssetSheet(BuildContext context, WidgetRef ref, MapAsset asset) {
    final label = mapAssetTypeLabels[asset.type] ?? asset.type;
    showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: Icon(
                _assetIcon(asset.type),
                color: _assetColor(asset.type),
              ),
              title: Text(asset.title),
              subtitle: Text(
                [
                  label,
                  if (asset.subtitle != null) asset.subtitle!,
                  if (asset.status != null) asset.status!,
                ].join(' · '),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.push_pin_outlined),
              title: const Text('Edit asset location'),
              onTap: () async {
                Navigator.pop(sheetContext);
                final changed = await Navigator.of(context).push<bool>(
                  MaterialPageRoute(
                    builder: (_) => AssetPinScreen(asset: asset),
                  ),
                );
                if (changed == true) ref.invalidate(mapAssetsProvider);
              },
            ),
          ],
        ),
      ),
    );
  }

  void _showPinListSheet(
    BuildContext context,
    WidgetRef ref,
    List<JobPin> pins,
    List<MapAsset> assets,
  ) {
    showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
              child: Text(
                'Edit map pin',
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            if (pins.isEmpty && assets.isEmpty)
              const ListTile(
                leading: Icon(Icons.info_outline),
                title: Text('No pins loaded yet'),
              ),
            for (final pin in pins)
              ListTile(
                leading: Icon(
                  Icons.location_pin,
                  color: AppColors.status(pin.status),
                ),
                title: Text(pin.title),
                subtitle: Text(pin.status.replaceAll('_', ' ')),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  final changed = await Navigator.of(context).push<bool>(
                    MaterialPageRoute(
                      builder: (_) => LocationPinScreen(
                        jobId: pin.id,
                        initialLocation: JobLocation(
                          latitude: pin.latitude,
                          longitude: pin.longitude,
                          source: 'cached',
                        ),
                      ),
                    ),
                  );
                  if (changed == true) ref.invalidate(mapPinsProvider);
                },
              ),
            for (final asset in assets)
              ListTile(
                leading: Icon(
                  _assetIcon(asset.type),
                  color: _assetColor(asset.type),
                ),
                title: Text(asset.title),
                subtitle: Text(mapAssetTypeLabels[asset.type] ?? asset.type),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  final changed = await Navigator.of(context).push<bool>(
                    MaterialPageRoute(
                      builder: (_) => AssetPinScreen(asset: asset),
                    ),
                  );
                  if (changed == true) ref.invalidate(mapAssetsProvider);
                },
              ),
          ],
        ),
      ),
    );
  }
}

class _LayerSelector extends StatelessWidget {
  const _LayerSelector({
    required this.selectedTypes,
    required this.loadingAssets,
    required this.onChanged,
  });

  final Set<String> selectedTypes;
  final bool loadingAssets;
  final void Function(String type, bool selected) onChanged;

  @override
  Widget build(BuildContext context) {
    return Positioned(
      top: 12,
      left: 12,
      right: 12,
      child: Material(
        color: Theme.of(context).colorScheme.surface,
        elevation: 2,
        borderRadius: BorderRadius.circular(8),
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Row(
            children: [
              if (loadingAssets)
                const Padding(
                  padding: EdgeInsets.only(right: 8),
                  child: SizedBox.square(
                    dimension: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
              for (final entry in mapAssetTypeLabels.entries)
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: FilterChip(
                    label: Text(entry.value),
                    selected: selectedTypes.contains(entry.key),
                    onSelected: (selected) => onChanged(entry.key, selected),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

IconData _assetIcon(String type) => switch (type) {
  'olt' => Icons.router_outlined,
  'fdh' => Icons.hub_outlined,
  'fiber_access_point' => Icons.device_hub_outlined,
  'splice_closure' => Icons.join_inner_outlined,
  'wireless_mast' => Icons.cell_tower_outlined,
  'service_building' => Icons.apartment_outlined,
  _ => Icons.place_outlined,
};

Color _assetColor(String type) => switch (type) {
  'olt' => Colors.deepPurple,
  'fdh' => Colors.teal,
  'fiber_access_point' => Colors.indigo,
  'splice_closure' => Colors.orange,
  'wireless_mast' => Colors.redAccent,
  'service_building' => Colors.brown,
  _ => Colors.blueGrey,
};
