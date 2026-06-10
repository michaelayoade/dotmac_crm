import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../auth/auth_state.dart';
import '../execution/execution_controller.dart';

class VendorProject {
  const VendorProject({required this.id, required this.status, this.notes});

  final String id;
  final String status;
  final String? notes;

  factory VendorProject.fromJson(Map<String, dynamic> json) => VendorProject(
        id: json['id'] as String,
        status: json['status'] as String? ?? 'unknown',
        notes: json['notes'] as String?,
      );
}

class AsBuiltSubmission {
  const AsBuiltSubmission({required this.id, required this.status, this.actualLengthMeters, this.reviewNotes});

  final String id;
  final String status;
  final double? actualLengthMeters;
  final String? reviewNotes;

  factory AsBuiltSubmission.fromJson(Map<String, dynamic> json) => AsBuiltSubmission(
        id: json['id'] as String,
        status: json['status'] as String? ?? 'submitted',
        actualLengthMeters: (json['actual_length_meters'] as num?)?.toDouble(),
        reviewNotes: json['review_notes'] as String?,
      );
}

class VendorProjectDetail {
  const VendorProjectDetail({
    required this.project,
    this.submissions = const [],
    this.rejectedForResubmission,
  });

  final VendorProject project;
  final List<AsBuiltSubmission> submissions;

  /// Set when the latest submission was rejected: the capture flow pre-fills
  /// from it so the crew fixes rather than restarts.
  final AsBuiltSubmission? rejectedForResubmission;

  factory VendorProjectDetail.fromJson(Map<String, dynamic> json) => VendorProjectDetail(
        project: VendorProject.fromJson((json['project'] as Map).cast<String, dynamic>()),
        submissions: ((json['submissions'] as List?) ?? [])
            .cast<Map>()
            .map((s) => AsBuiltSubmission.fromJson(s.cast<String, dynamic>()))
            .toList(),
        rejectedForResubmission: json['rejected_for_resubmission'] != null
            ? AsBuiltSubmission.fromJson(
                (json['rejected_for_resubmission'] as Map).cast<String, dynamic>())
            : null,
      );
}

class VendorRepository {
  VendorRepository(this._ref);

  final Ref _ref;

  Future<List<VendorProject>> fetchProjects() async {
    final response = await _ref.read(apiClientProvider).dio.get('/api/v1/field/projects');
    final items = (response.data['items'] as List).cast<Map>();
    return items.map((item) => VendorProject.fromJson(item.cast<String, dynamic>())).toList();
  }

  Future<VendorProjectDetail> fetchDetail(String projectId) async {
    final response = await _ref.read(apiClientProvider).dio.get('/api/v1/field/projects/$projectId');
    return VendorProjectDetail.fromJson((response.data as Map).cast<String, dynamic>());
  }

  /// Queue an as-built submission through the offline outbox.
  Future<String> submitAsBuilt({
    required String projectId,
    required Map<String, dynamic> geojson,
    required double actualLengthMeters,
    String? variationReason,
  }) async {
    final clientRef = const Uuid().v4();
    await _ref.read(syncServiceProvider).enqueue(
      kind: 'as_built',
      clientRef: clientRef,
      payload: {
        'project_id': projectId,
        'geojson': geojson,
        'actual_length_meters': double.parse(actualLengthMeters.toStringAsFixed(1)),
        'variation_reason': ?variationReason,
      },
    );
    await _ref.read(syncServiceProvider).flushOutbox();
    return clientRef;
  }
}

final vendorRepositoryProvider = Provider<VendorRepository>(VendorRepository.new);

final vendorProjectsProvider =
    FutureProvider<List<VendorProject>>((ref) => ref.watch(vendorRepositoryProvider).fetchProjects());

final vendorProjectDetailProvider = FutureProvider.family<VendorProjectDetail, String>(
  (ref, projectId) => ref.watch(vendorRepositoryProvider).fetchDetail(projectId),
);
