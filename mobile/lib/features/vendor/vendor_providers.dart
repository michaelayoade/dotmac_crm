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

/// A stage of a job's lifecycle (quote / as-built / billing). Any field may be
/// null until the crew reaches that stage.
class VendorStageState {
  const VendorStageState({this.status, this.label});

  final String? status;

  /// A short human line for the chip subtitle (e.g. total, invoice no.).
  final String? label;

  bool get isPresent => status != null;
}

/// Per-job lifecycle: bid → approval → as-built → payment. Mirrors the backend
/// VendorProjectLifecycle bundle (#123).
class VendorLifecycle {
  const VendorLifecycle({this.quote, this.asBuilt, this.billing});

  final VendorStageState? quote;
  final VendorStageState? asBuilt;
  final VendorStageState? billing;

  static String? _money(num? total, String? currency) {
    if (total == null) return null;
    final amount = total.toStringAsFixed(0);
    return currency != null ? '$currency $amount' : amount;
  }

  factory VendorLifecycle.fromJson(Map<String, dynamic> json) {
    final quote = (json['quote'] as Map?)?.cast<String, dynamic>();
    final asBuilt = (json['as_built'] as Map?)?.cast<String, dynamic>();
    final billing = (json['billing'] as Map?)?.cast<String, dynamic>();
    return VendorLifecycle(
      quote: quote == null
          ? null
          : VendorStageState(
              status: quote['status'] as String?,
              label: _money(quote['total'] as num?, quote['currency'] as String?),
            ),
      asBuilt: asBuilt == null
          ? null
          : VendorStageState(status: asBuilt['status'] as String?),
      billing: billing == null
          ? null
          : VendorStageState(
              status: billing['status'] as String?,
              label: (billing['erp_synced'] as bool? ?? false)
                  ? 'Synced to ERP'
                  : _money(billing['total'] as num?, billing['currency'] as String?),
            ),
    );
  }
}

/// Who to call and where to go — the site bundle from the project detail (#122).
class VendorSite {
  const VendorSite({
    this.name,
    this.phone,
    this.email,
    this.addressText,
    this.accessNotes,
  });

  final String? name;
  final String? phone;
  final String? email;
  final String? addressText;
  final String? accessNotes;

  bool get hasContact => (name != null && name!.isNotEmpty) || phone != null;

  factory VendorSite.fromJson(Map<String, dynamic> json) => VendorSite(
        name: json['name'] as String?,
        phone: json['phone'] as String?,
        email: json['email'] as String?,
        addressText: json['address_text'] as String?,
        accessNotes: json['access_notes'] as String?,
      );
}

/// A list row: the project plus its lifecycle state.
class VendorProjectListItem {
  const VendorProjectListItem({required this.project, this.lifecycle});

  final VendorProject project;
  final VendorLifecycle? lifecycle;

  factory VendorProjectListItem.fromJson(Map<String, dynamic> json) => VendorProjectListItem(
        project: VendorProject.fromJson((json['project'] as Map).cast<String, dynamic>()),
        lifecycle: json['lifecycle'] != null
            ? VendorLifecycle.fromJson((json['lifecycle'] as Map).cast<String, dynamic>())
            : null,
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
    this.site,
    this.lifecycle,
    this.submissions = const [],
    this.rejectedForResubmission,
  });

  final VendorProject project;
  final VendorSite? site;
  final VendorLifecycle? lifecycle;
  final List<AsBuiltSubmission> submissions;

  /// Set when the latest submission was rejected: the capture flow pre-fills
  /// from it so the crew fixes rather than restarts.
  final AsBuiltSubmission? rejectedForResubmission;

  factory VendorProjectDetail.fromJson(Map<String, dynamic> json) => VendorProjectDetail(
        project: VendorProject.fromJson((json['project'] as Map).cast<String, dynamic>()),
        site: json['site'] != null
            ? VendorSite.fromJson((json['site'] as Map).cast<String, dynamic>())
            : null,
        lifecycle: json['lifecycle'] != null
            ? VendorLifecycle.fromJson((json['lifecycle'] as Map).cast<String, dynamic>())
            : null,
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

  Future<List<VendorProjectListItem>> fetchProjects() async {
    final response = await _ref.read(apiClientProvider).dio.get('/api/v1/field/projects');
    final items = (response.data['items'] as List).cast<Map>();
    return items.map((item) => VendorProjectListItem.fromJson(item.cast<String, dynamic>())).toList();
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
    FutureProvider<List<VendorProjectListItem>>((ref) => ref.watch(vendorRepositoryProvider).fetchProjects());

final vendorProjectDetailProvider = FutureProvider.family<VendorProjectDetail, String>(
  (ref, projectId) => ref.watch(vendorRepositoryProvider).fetchDetail(projectId),
);
