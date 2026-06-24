import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:latlong2/latlong.dart';

import '../../app/theme.dart';
import '../execution/execution_controller.dart';
import '../jobs/job_models.dart';
import '../jobs/jobs_providers.dart';
import '../jobs/location_pin_screen.dart';
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

    return Scaffold(
      appBar: AppBar(title: const Text('Job map')),
      body: pins.when(
        data: (items) {
          final center = items.isNotEmpty
              ? LatLng(items.first.latitude, items.first.longitude)
              : const LatLng(6.5244, 3.3792); // Lagos default
          return FlutterMap(
            options: MapOptions(initialCenter: center, initialZoom: 12),
            children: [
              if (showTiles)
                TileLayer(
                  urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                  userAgentPackageName: 'io.dotmac.dotmac_field',
                ),
              MarkerLayer(
                markers: [
                  for (final pin in items)
                    Marker(
                      point: LatLng(pin.latitude, pin.longitude),
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
}
