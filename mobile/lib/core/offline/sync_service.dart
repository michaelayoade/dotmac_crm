import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:drift/drift.dart';

import '../api/api_client.dart';
import 'connectivity.dart';
import 'database.dart';

/// Pluggable clock/delay so throttle behavior is testable without real time.
typedef DelayFn = Future<void> Function(Duration duration);

/// Maps an outbox entry kind to its API call.
class OutboxRouting {
  static (String method, String path) route(String kind, Map<String, dynamic> payload) {
    return switch (kind) {
      'transition' => ('POST', '/api/v1/field/jobs/${payload['work_order_id']}/transition'),
      'note' => ('POST', '/api/v1/field/jobs/${payload['work_order_id']}/notes'),
      'worklog' => ('POST', '/api/v1/field/jobs/${payload['work_order_id']}/worklogs'),
      'material_consume' => ('POST', '/api/v1/field/jobs/${payload['work_order_id']}/materials/consume'),
      'as_built' => ('POST', '/api/v1/field/projects/${payload['project_id']}/as-built'),
      _ => throw ArgumentError('Unknown outbox kind: $kind'),
    };
  }
}

class SyncService {
  SyncService({
    required this.db,
    required this.api,
    required this.connectivity,
    DelayFn? delay,
    this.throttle = const Duration(seconds: 1),
  }) : _delay = delay ?? Future.delayed {
    _subscription = connectivity.onlineChanges.listen((online) {
      if (online) unawaited(flushOutbox());
    });
  }

  final AppDatabase db;
  final ApiClient api;
  final ConnectivitySource connectivity;
  final Duration throttle;
  final DelayFn _delay;

  StreamSubscription<bool>? _subscription;
  bool _flushing = false;

  Future<void> dispose() async {
    await _subscription?.cancel();
  }

  // ---- Down-sync ---------------------------------------------------------

  Future<int> downSyncJobs() async {
    final response = await api.dio.get('/api/v1/field/jobs', queryParameters: {'limit': 200});
    final items = (response.data['items'] as List).cast<Map>();
    final now = DateTime.now().toUtc();
    await db.batch((batch) {
      for (final item in items) {
        batch.insert(
          db.cachedJobs,
          CachedJobsCompanion.insert(
            id: item['id'] as String,
            title: item['title'] as String,
            status: item['status'] as String,
            workType: item['work_type'] as String,
            priority: item['priority'] as String,
            scheduledStart: Value(
              item['scheduled_start'] != null ? DateTime.parse(item['scheduled_start'] as String) : null,
            ),
            cachedAt: now,
          ),
          onConflict: DoUpdate(
            (old) => CachedJobsCompanion.custom(
              title: Constant(item['title'] as String),
              status: Constant(item['status'] as String),
              cachedAt: Constant(now),
            ),
          ),
        );
      }
    });
    return items.length;
  }

  Future<void> cacheJobDetail(String jobId, Map<String, dynamic> detail) async {
    await (db.update(db.cachedJobs)..where((row) => row.id.equals(jobId))).write(
      CachedJobsCompanion(detailJson: Value(jsonEncode(detail))),
    );
  }

  // ---- Outbox ------------------------------------------------------------

  Future<void> enqueue({
    required String kind,
    required String clientRef,
    required Map<String, dynamic> payload,
  }) async {
    await db.into(db.outboxEntries).insert(
          OutboxEntriesCompanion.insert(
            clientRef: clientRef,
            kind: kind,
            payloadJson: jsonEncode(payload),
            createdAt: DateTime.now().toUtc(),
          ),
          mode: InsertMode.insertOrIgnore, // retried enqueues are no-ops
        );
  }

  Future<List<OutboxEntry>> pending() => (db.select(db.outboxEntries)
        ..where((row) => row.status.equals('pending'))
        ..orderBy([(row) => OrderingTerm.asc(row.seq)]))
      .get();

  /// Flush pending entries FIFO. One failure stops the flush (order matters:
  /// a note may reference a transition); conflicts are parked, not dropped.
  Future<int> flushOutbox() async {
    if (_flushing) return 0;
    if (!await connectivity.isOnline) return 0;
    _flushing = true;
    var sent = 0;
    try {
      for (final entry in await pending()) {
        final payload = (jsonDecode(entry.payloadJson) as Map).cast<String, dynamic>();
        final (method, path) = OutboxRouting.route(entry.kind, payload);
        try {
          await api.dio.request(
            path,
            data: payload,
            options: Options(method: method),
          );
          await _mark(entry, 'sent');
          sent++;
        } on DioException catch (error) {
          final status = error.response?.statusCode;
          if (status == 429) {
            final retryAfter = int.tryParse(
                  error.response?.headers.value('Retry-After') ?? '',
                ) ??
                5;
            await _delay(Duration(seconds: retryAfter));
            await _bumpAttempts(entry, 'rate limited');
            break; // re-flush later, keep FIFO order
          }
          if (status == 409) {
            // Structured conflict (job reassigned/cancelled): park it for
            // review. Evidence is never dropped.
            await _mark(entry, 'conflict', error: _detail(error));
            continue;
          }
          if (status != null && status >= 400 && status < 500) {
            // Permanent rejection: park as conflict for review.
            await _mark(entry, 'conflict', error: _detail(error));
            continue;
          }
          await _bumpAttempts(entry, _detail(error));
          break; // network/server trouble: stop, retry on next trigger
        }
        await _delay(throttle);
      }
    } finally {
      _flushing = false;
    }
    return sent;
  }

  String _detail(DioException error) {
    final data = error.response?.data;
    if (data is Map && data['detail'] != null) return data['detail'].toString();
    return error.message ?? 'request failed';
  }

  Future<void> _mark(OutboxEntry entry, String status, {String? error}) async {
    await (db.update(db.outboxEntries)..where((row) => row.seq.equals(entry.seq))).write(
      OutboxEntriesCompanion(
        status: Value(status),
        lastError: Value(error),
        attempts: Value(entry.attempts + 1),
      ),
    );
  }

  Future<void> _bumpAttempts(OutboxEntry entry, String error) async {
    await (db.update(db.outboxEntries)..where((row) => row.seq.equals(entry.seq))).write(
      OutboxEntriesCompanion(
        attempts: Value(entry.attempts + 1),
        lastError: Value(error),
      ),
    );
  }
}
