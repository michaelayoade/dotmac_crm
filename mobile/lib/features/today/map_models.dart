import 'dart:convert';

import '../jobs/job_models.dart';

class JobPin {
  const JobPin({
    required this.id,
    required this.title,
    required this.status,
    required this.latitude,
    required this.longitude,
  });

  final String id;
  final String title;
  final String status;
  final double latitude;
  final double longitude;
}

/// Build map pins from job summaries + cached detail JSON (keyed by job id).
/// Jobs without resolved coordinates are skipped — the map shows what it
/// knows; the list view remains the complete source of truth.
List<JobPin> buildJobPins(List<JobSummary> jobs, Map<String, String?> detailJsonById) {
  final pins = <JobPin>[];
  for (final job in jobs) {
    final raw = detailJsonById[job.id];
    if (raw == null) continue;
    final detail = (jsonDecode(raw) as Map).cast<String, dynamic>();
    final location = (detail['location'] as Map?)?.cast<String, dynamic>();
    final lat = (location?['latitude'] as num?)?.toDouble();
    final lng = (location?['longitude'] as num?)?.toDouble();
    if (lat == null || lng == null) continue;
    pins.add(JobPin(id: job.id, title: job.title, status: job.status, latitude: lat, longitude: lng));
  }
  return pins;
}
