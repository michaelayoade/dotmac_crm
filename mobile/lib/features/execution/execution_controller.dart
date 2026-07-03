import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../core/location/device_location.dart';
import '../../core/location/location_source.dart';
import '../../core/offline/sync_service.dart';

/// Real devices use geolocator; tests override with fakes. kIsWeb and
/// headless test binaries never construct the plugin path.
final locationSourceProvider = Provider<LocationSource>((ref) {
  if (kIsWeb) return const UnavailableLocation();
  return GeolocatorLocationSource();
});

/// Provided at app bootstrap once the drift database is opened.
final syncServiceProvider = Provider<SyncService>(
  (ref) => throw UnimplementedError('overridden at bootstrap'),
);

/// Local timer bookkeeping: server-side auto-stop is authoritative; the
/// client records its own start/stop pair as a worklog entry.
class ActiveTimer {
  const ActiveTimer({required this.jobId, required this.startedAt});

  final String jobId;
  final DateTime startedAt;
}

class ExecutionController extends Notifier<ActiveTimer?> {
  @override
  ActiveTimer? build() => null;

  SyncService get _sync => ref.read(syncServiceProvider);

  static const _uuid = Uuid();

  /// Queue a job transition. Every event carries a client UUID (server-side
  /// idempotency) and best-effort GPS. Returns the client_event_id.
  Future<String> transition(
    String jobId,
    String event, {
    String? note,
    Map<String, dynamic>? payload,
  }) async {
    final clientEventId = _uuid.v4();
    final position = await ref.read(locationSourceProvider).current();
    await _sync.enqueue(
      kind: 'transition',
      clientRef: clientEventId,
      payload: {
        'work_order_id': jobId,
        'event': event,
        'client_event_id': clientEventId,
        'occurred_at': DateTime.now().toUtc().toIso8601String(),
        'latitude': ?position?.latitude,
        'longitude': ?position?.longitude,
        'note': ?note,
        'payload': ?payload,
      },
    );

    if (event == 'start') {
      state = ActiveTimer(jobId: jobId, startedAt: DateTime.now().toUtc());
    }
    if (event == 'hold' ||
        event == 'complete' ||
        event == 'unable_to_complete') {
      await _stopTimer(jobId);
    }

    // Best-effort immediate delivery; offline entries stay queued.
    await _sync.flushOutbox();
    return clientEventId;
  }

  /// Record a failed visit (customer absent, no access, …). Cancels the job
  /// server-side with the reason; bypasses the completion-evidence gate.
  Future<String> unableToComplete(
    String jobId, {
    required String reason,
    String? note,
  }) {
    return transition(
      jobId,
      'unable_to_complete',
      note: note,
      payload: {'reason': reason},
    );
  }

  Future<String> addNote(
    String jobId,
    String body, {
    List<String> attachmentIds = const [],
  }) async {
    final trimmed = body.trim();
    if (trimmed.isEmpty) {
      throw ArgumentError.value(body, 'body', 'Note body is required');
    }
    final clientRef = _uuid.v4();
    await _sync.enqueue(
      kind: 'note',
      clientRef: clientRef,
      payload: {
        'work_order_id': jobId,
        'body': trimmed,
        'attachment_ids': attachmentIds,
      },
    );
    try {
      await _sync.flushOutbox();
    } catch (_) {
      // The note is already queued locally. A transient immediate-sync failure
      // should not make the save action look failed to the technician.
    }
    return clientRef;
  }

  Future<void> _stopTimer(String jobId) async {
    final timer = state;
    if (timer == null || timer.jobId != jobId) return;
    state = null;
    // One entry per worklog submission: reuse the outbox ref as the entry's
    // client_ref so a retried flush dedupes server-side instead of duplicating.
    final clientRef = _uuid.v4();
    await _sync.enqueue(
      kind: 'worklog',
      clientRef: clientRef,
      payload: {
        'work_order_id': jobId,
        'entries': [
          {
            'client_ref': clientRef,
            'start_at': timer.startedAt.toIso8601String(),
            'end_at': DateTime.now().toUtc().toIso8601String(),
          },
        ],
      },
    );
  }
}

final executionControllerProvider =
    NotifierProvider<ExecutionController, ActiveTimer?>(
      ExecutionController.new,
    );
