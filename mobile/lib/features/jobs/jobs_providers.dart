import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/auth_state.dart';
import 'job_models.dart';

class MeSummary {
  const MeSummary({required this.name, required this.openJobs, required this.completedToday});

  final String name;
  final int openJobs;
  final int completedToday;
}

class JobsRepository {
  JobsRepository(this._read);

  final Ref _read;

  Future<MeSummary> fetchMe() async {
    final response = await _read.read(apiClientProvider).dio.get('/api/v1/field/me');
    final data = (response.data as Map).cast<String, dynamic>();
    return MeSummary(
      name: data['name'] as String? ?? '',
      openJobs: data['open_jobs'] as int? ?? 0,
      completedToday: data['completed_today'] as int? ?? 0,
    );
  }

  Future<List<JobSummary>> fetchJobs({String? status}) async {
    final response = await _read.read(apiClientProvider).dio.get(
      '/api/v1/field/jobs',
      queryParameters: {'status': ?status, 'limit': 200},
    );
    final items = (response.data['items'] as List).cast<Map>();
    return items.map((item) => JobSummary.fromJson(item.cast<String, dynamic>())).toList();
  }

  Future<JobDetail> fetchDetail(String jobId) async {
    final response = await _read.read(apiClientProvider).dio.get('/api/v1/field/jobs/$jobId');
    return JobDetail.fromJson((response.data as Map).cast<String, dynamic>());
  }
}

final jobsRepositoryProvider = Provider<JobsRepository>(JobsRepository.new);

final meProvider = FutureProvider<MeSummary>((ref) => ref.watch(jobsRepositoryProvider).fetchMe());

final jobsFilterProvider = StateProvider<String?>((ref) => null);

final jobsListProvider = FutureProvider<List<JobSummary>>((ref) {
  final filter = ref.watch(jobsFilterProvider);
  return ref.watch(jobsRepositoryProvider).fetchJobs(status: filter);
});

final jobDetailProvider = FutureProvider.family<JobDetail, String>(
  (ref, jobId) => ref.watch(jobsRepositoryProvider).fetchDetail(jobId),
);
