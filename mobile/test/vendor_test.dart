import 'dart:convert';
import 'dart:ffi';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dotmac_field/core/api/api_client.dart';
import 'package:dotmac_field/core/api/token_store.dart';
import 'package:dotmac_field/core/offline/connectivity.dart';
import 'package:dotmac_field/core/offline/database.dart';
import 'package:dotmac_field/core/offline/sync_service.dart';
import 'package:dotmac_field/features/auth/auth_state.dart';
import 'package:dotmac_field/features/execution/execution_controller.dart';
import 'package:dotmac_field/features/vendor/trace_recorder.dart';
import 'package:dotmac_field/features/vendor/vendor_providers.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqlite3/open.dart';

import 'helpers/fake_http.dart';

void main() {
  if (Platform.isLinux) {
    open.overrideFor(OperatingSystem.linux, () => DynamicLibrary.open('libsqlite3.so.0'));
  }

  group('trace recorder', () {
    test('accumulates points, filters jitter, computes distance', () {
      final recorder = TraceRecorder()..start();
      recorder.addPoint((latitude: 6.4281, longitude: 3.4216));
      recorder.addPoint((latitude: 6.4281, longitude: 3.4216)); // jitter: dropped
      recorder.addPoint((latitude: 6.4290, longitude: 3.4216)); // ~100 m north
      recorder.stop();

      expect(recorder.points.length, 2);
      expect(recorder.distanceMeters, closeTo(100, 5));
      expect(recorder.hasUsableTrace, isTrue);
    });

    test('geojson is a LineString of lng,lat pairs', () {
      final recorder = TraceRecorder()..start();
      recorder.addPoint((latitude: 6.0, longitude: 3.0));
      recorder.addPoint((latitude: 6.001, longitude: 3.001));
      final geojson = recorder.toGeoJson();

      expect(geojson['type'], 'LineString');
      expect(geojson['coordinates'], [
        [3.0, 6.0],
        [3.001, 6.001],
      ]);
    });

    test('single point is not a usable trace', () {
      final recorder = TraceRecorder()..start();
      recorder.addPoint((latitude: 6.0, longitude: 3.0));
      expect(recorder.hasUsableTrace, isFalse);
    });
  });

  group('vendor repository', () {
    late AppDatabase db;
    late SyncService sync;
    late ProviderContainer container;
    late FakeHttpAdapter adapter;

    final freshToken = fakeJwt(expiry: DateTime.now().toUtc().add(const Duration(minutes: 15)));

    setUp(() async {
      db = AppDatabase(NativeDatabase.memory());
      adapter = FakeHttpAdapter();
      final store = InMemoryTokenStore();
      await store.save(accessToken: freshToken, refreshToken: 'r', loginMode: LoginMode.vendor);
      final dio = Dio(BaseOptions(baseUrl: 'https://test.local'));
      dio.httpClientAdapter = adapter;
      final client = ApiClient(baseUrl: 'https://test.local', tokenStore: store, dio: dio);
      sync = SyncService(
        db: db,
        api: client,
        connectivity: FakeConnectivity(online: false),
        delay: (_) async {},
      );
      container = ProviderContainer(overrides: [
        apiClientProvider.overrideWithValue(client),
        syncServiceProvider.overrideWithValue(sync),
      ]);
    });

    tearDown(() async {
      container.dispose();
      await sync.dispose();
      await db.close();
    });

    test('detail exposes resubmission pre-fill', () async {
      adapter.on('GET', '/api/v1/field/projects/p-1', (_) => (200, {
            'project': {'id': 'p-1', 'status': 'in_progress'},
            'submissions': [
              {'id': 's-2', 'status': 'rejected', 'actual_length_meters': 120.0, 'review_notes': 'Path too short'},
            ],
            'rejected_for_resubmission': {
              'id': 's-2',
              'status': 'rejected',
              'actual_length_meters': 120.0,
              'review_notes': 'Path too short',
            },
            'attachment_count': 2,
          }));

      final detail = await container.read(vendorRepositoryProvider).fetchDetail('p-1');
      expect(detail.rejectedForResubmission, isNotNull);
      expect(detail.rejectedForResubmission!.actualLengthMeters, 120.0);
      expect(detail.rejectedForResubmission!.reviewNotes, 'Path too short');
    });

    test('submit queues an as_built outbox entry with the right shape', () async {
      final recorder = TraceRecorder()..start();
      recorder.addPoint((latitude: 6.0, longitude: 3.0));
      recorder.addPoint((latitude: 6.001, longitude: 3.001));
      recorder.stop();

      await container.read(vendorRepositoryProvider).submitAsBuilt(
            projectId: 'p-1',
            geojson: recorder.toGeoJson(),
            actualLengthMeters: recorder.distanceMeters,
          );

      final rows = await db.select(db.outboxEntries).get();
      expect(rows.single.kind, 'as_built');
      final payload = (jsonDecode(rows.single.payloadJson) as Map).cast<String, dynamic>();
      expect(payload['project_id'], 'p-1');
      expect(payload['geojson']['type'], 'LineString');
      expect(payload['actual_length_meters'], greaterThan(100));
    });
  });
}
