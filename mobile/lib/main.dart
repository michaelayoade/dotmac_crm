import 'dart:io';
import 'dart:typed_data';

import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'app/app.dart';
import 'core/offline/connectivity.dart';
import 'core/offline/database.dart';
import 'core/offline/sync_service.dart';
import 'core/photos/photo_queue.dart';
import 'core/push/fcm_push_source.dart';
import 'core/push/push_registrar.dart';
import 'features/auth/auth_state.dart';
import 'features/execution/completion_wizard.dart';
import 'features/execution/execution_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final documents = await getApplicationDocumentsDirectory();
  final dbFile = File(p.join(documents.path, 'dotmac_field.sqlite'));
  final photoDir = Directory(p.join(documents.path, 'field_photos'));
  await photoDir.create(recursive: true);

  final db = AppDatabase(NativeDatabase(dbFile));

  // FCM push, when Firebase is configured (else null → NoopPushSource).
  final fcm = await FcmPushSource.tryCreate();

  runApp(
    ProviderScope(
      overrides: [
        if (fcm != null) pushSourceProvider.overrideWithValue(fcm),
        syncServiceProvider.overrideWith((ref) {
          final sync = SyncService(
            db: db,
            api: ref.watch(apiClientProvider),
            connectivity: DeviceConnectivity(),
          );
          Future.microtask(sync.flushAll);
          ref.onDispose(sync.dispose);
          ref.onDispose(db.close);
          return sync;
        }),
        photoCaptureProvider.overrideWith((ref) {
          final queue = PhotoQueue(
            db: db,
            source: CameraImageSource(),
            location: ref.watch(locationSourceProvider),
            storageDir: photoDir,
          );
          return ({String? workOrderId, String? installationProjectId}) {
            return queue.captureForJob(
              workOrderId: workOrderId,
              installationProjectId: installationProjectId,
            );
          };
        }),
        signatureSinkProvider.overrideWith((ref) {
          final queue = PhotoQueue(
            db: db,
            source: CameraImageSource(),
            location: ref.watch(locationSourceProvider),
            storageDir: photoDir,
          );
          return ({required String workOrderId, required Uint8List png}) {
            return queue.enqueueImageBytes(
              png,
              kind: 'signature',
              workOrderId: workOrderId,
            );
          };
        }),
      ],
      child: const DotmacFieldApp(),
    ),
  );
}
