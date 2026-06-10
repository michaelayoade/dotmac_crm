import 'dart:convert';

import 'package:dotmac_field/features/jobs/job_models.dart';
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
      'location': {'latitude': lat, 'longitude': lng, 'address_text': 'x', 'source': 'geocoded'},
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
      const JobPin(id: 'a', title: 'Job a', status: 'dispatched', latitude: 6.5, longitude: 3.4),
      const JobPin(id: 'b', title: 'Job b', status: 'in_progress', latitude: 6.51, longitude: 3.41),
    ];
    await tester.pumpWidget(ProviderScope(
      overrides: [mapPinsProvider.overrideWith((ref) async => pins)],
      child: const MaterialApp(home: MapScreen(showTiles: false)),
    ));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('pin-a')), findsOneWidget);
    expect(find.byKey(const Key('pin-b')), findsOneWidget);
  });

  testWidgets('tapping a pin opens the job sheet', (tester) async {
    final pins = [
      const JobPin(id: 'a', title: 'Job a', status: 'dispatched', latitude: 6.5, longitude: 3.4),
    ];
    await tester.pumpWidget(ProviderScope(
      overrides: [mapPinsProvider.overrideWith((ref) async => pins)],
      child: const MaterialApp(home: MapScreen(showTiles: false)),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('pin-a')));
    await tester.pumpAndSettle();
    expect(find.text('Job a'), findsOneWidget);
  });
}
