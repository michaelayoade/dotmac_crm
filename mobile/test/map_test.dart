import 'dart:convert';

import 'package:dotmac_field/features/jobs/job_models.dart';
import 'package:dotmac_field/features/today/map_assets_repository.dart';
import 'package:dotmac_field/features/today/map_models.dart';
import 'package:dotmac_field/features/today/map_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

JobSummary _job(String id, {String status = 'dispatched'}) => JobSummary(
  id: id,
  title: 'Job $id',
  status: status,
  workType: 'install',
  priority: 'normal',
);

String _detailWith({double? lat, double? lng}) => jsonEncode({
  'location': {
    'latitude': lat,
    'longitude': lng,
    'address_text': 'x',
    'source': 'geocoded',
  },
});

void main() {
  test('buildJobPins skips jobs without cached coordinates', () {
    final pins = buildJobPins(
      [_job('a'), _job('b'), _job('c')],
      {
        'a': _detailWith(lat: 6.5, lng: 3.4),
        'b': _detailWith(lat: null, lng: null),
        // c has no cached detail at all
      },
    );
    expect(pins.single.id, 'a');
    expect(pins.single.latitude, 6.5);
  });

  testWidgets('map renders a marker per pinned job', (tester) async {
    final pins = [
      const JobPin(
        id: 'a',
        title: 'Job a',
        status: 'dispatched',
        latitude: 6.5,
        longitude: 3.4,
      ),
      const JobPin(
        id: 'b',
        title: 'Job b',
        status: 'in_progress',
        latitude: 6.51,
        longitude: 3.41,
      ),
    ];
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapPinsProvider.overrideWith((ref) async => pins),
          mapAssetsProvider.overrideWith((ref) async => []),
        ],
        child: const MaterialApp(home: MapScreen(showTiles: false)),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('pin-a')), findsOneWidget);
    expect(find.byKey(const Key('pin-b')), findsOneWidget);
    expect(find.byKey(const Key('edit-pins-button')), findsOneWidget);
  });

  testWidgets('tapping a pin opens the job sheet', (tester) async {
    final pins = [
      const JobPin(
        id: 'a',
        title: 'Job a',
        status: 'dispatched',
        latitude: 6.5,
        longitude: 3.4,
      ),
    ];
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapPinsProvider.overrideWith((ref) async => pins),
          mapAssetsProvider.overrideWith((ref) async => []),
        ],
        child: const MaterialApp(home: MapScreen(showTiles: false)),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('pin-a')));
    await tester.pumpAndSettle();
    expect(find.text('Job a'), findsOneWidget);
    expect(find.text('Edit pin location'), findsOneWidget);
  });

  testWidgets('edit pins button opens pinned job list', (tester) async {
    final pins = [
      const JobPin(
        id: 'a',
        title: 'Job a',
        status: 'dispatched',
        latitude: 6.5,
        longitude: 3.4,
      ),
    ];
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapPinsProvider.overrideWith((ref) async => pins),
          mapAssetsProvider.overrideWith((ref) async => []),
        ],
        child: const MaterialApp(home: MapScreen(showTiles: false)),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('edit-pins-button')));
    await tester.pumpAndSettle();

    expect(find.text('Edit map pin'), findsOneWidget);
    expect(find.text('Job a'), findsOneWidget);
  });

  testWidgets('edit pins button stays visible with no pins', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapPinsProvider.overrideWith((ref) async => []),
          mapAssetsProvider.overrideWith((ref) async => []),
        ],
        child: const MaterialApp(home: MapScreen(showTiles: false)),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('edit-pins-button')));
    await tester.pumpAndSettle();

    expect(find.text('No pins loaded yet'), findsOneWidget);
  });

  testWidgets('map renders crm asset pins and layer filters', (tester) async {
    final assets = [
      const MapAsset(
        id: 'olt-1',
        type: 'olt',
        title: 'OLT Alpha',
        latitude: 9.1,
        longitude: 7.4,
      ),
    ];
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          mapPinsProvider.overrideWith((ref) async => []),
          mapAssetsProvider.overrideWith((ref) async => assets),
        ],
        child: const MaterialApp(home: MapScreen(showTiles: false)),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('asset-olt-olt-1')), findsOneWidget);
    expect(find.text('OLT'), findsOneWidget);
    expect(find.text('FDH'), findsOneWidget);

    await tester.tap(find.byKey(const Key('asset-olt-olt-1')));
    await tester.pumpAndSettle();
    expect(find.text('OLT Alpha'), findsOneWidget);
    expect(find.text('Edit asset location'), findsOneWidget);
  });
}
