import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/offline/database.dart';
import '../auth/auth_state.dart';
import '../execution/execution_controller.dart';
import 'job_models.dart';

class MeSummary {
  const MeSummary({
    required this.name,
    required this.openJobs,
    required this.completedToday,
  });

  final String name;
  final int openJobs;
  final int completedToday;
}

/// A job list plus whether it came from the offline cache (drives the banner).
class JobList {
  const JobList(this.jobs, {this.fromCache = false});

  final List<JobSummary> jobs;
  final bool fromCache;
}

JobSummary _summaryFromCache(CachedJob row) => JobSummary(
  id: row.id,
  title: row.title,
  status: row.status,
  workType: row.workType,
  priority: row.priority,
  scheduledStart: row.scheduledStart,
);

class JobsRepository {
  JobsRepository(this._read);

  final Ref _read;

  Future<MeSummary> fetchMe() async {
    final response = await _read
        .read(apiClientProvider)
        .dio
        .get('/api/v1/field/me');
    final data = (response.data as Map).cast<String, dynamic>();
    return MeSummary(
      name: data['name'] as String? ?? '',
      openJobs: data['open_jobs'] as int? ?? 0,
      completedToday: data['completed_today'] as int? ?? 0,
    );
  }

  Future<JobList> fetchJobs({String? status}) async {
    final sync = _read.read(syncServiceProvider);
    try {
      final response = await _read
          .read(apiClientProvider)
          .dio
          .get(
            '/api/v1/field/jobs',
            queryParameters: {'status': ?status, 'limit': 200},
          );
      final items = (response.data['items'] as List).cast<Map>();
      await sync.cacheJobs(items); // keep the offline cache warm
      return JobList(
        items
            .map((item) => JobSummary.fromJson(item.cast<String, dynamic>()))
            .toList(),
      );
    } on DioException {
      // Offline / server unreachable: serve the cache so the tech still works.
      final cached = await sync.readCachedJobs(status: status);
      if (cached.isEmpty) rethrow;
      return JobList(cached.map(_summaryFromCache).toList(), fromCache: true);
    }
  }

  Future<JobDetail> fetchDetail(String jobId) async {
    final sync = _read.read(syncServiceProvider);
    try {
      final response = await _read
          .read(apiClientProvider)
          .dio
          .get('/api/v1/field/jobs/$jobId');
      final data = (response.data as Map).cast<String, dynamic>();
      await sync.cacheJobDetail(jobId, data);
      return JobDetail.fromJson(data);
    } on DioException {
      final cached = await sync.readCachedDetail(jobId);
      if (cached == null) rethrow;
      return JobDetail.fromJson(cached);
    }
  }

  Future<JobLocation> updateLocation({
    required String jobId,
    required double latitude,
    required double longitude,
  }) async {
    final sync = _read.read(syncServiceProvider);
    final response = await _read
        .read(apiClientProvider)
        .dio
        .patch(
          '/api/v1/field/jobs/$jobId/location',
          data: {'latitude': latitude, 'longitude': longitude},
        );
    final data = (response.data as Map).cast<String, dynamic>();
    final locationData = ((data['location'] as Map?) ?? data)
        .cast<String, dynamic>();
    final location = JobLocation.fromJson(locationData);

    final cached = await sync.readCachedDetail(jobId);
    if (cached != null) {
      cached['location'] = location.toJson();
      await sync.cacheJobDetail(jobId, cached);
    }
    return location;
  }
}

final jobsRepositoryProvider = Provider<JobsRepository>(JobsRepository.new);

final meProvider = FutureProvider<MeSummary>((ref) {
  ref.watch(authControllerProvider);
  return ref.watch(jobsRepositoryProvider).fetchMe();
});

final jobsFilterProvider = StateProvider<String?>((ref) => null);

final jobsListProvider = FutureProvider<JobList>((ref) {
  final filter = ref.watch(jobsFilterProvider);
  return ref.watch(jobsRepositoryProvider).fetchJobs(status: filter);
});

final jobDetailProvider = FutureProvider.family<JobDetail, String>(
  (ref, jobId) => ref.watch(jobsRepositoryProvider).fetchDetail(jobId),
);
