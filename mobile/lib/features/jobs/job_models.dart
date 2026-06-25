import '../../core/location/map_coordinates.dart';

/// Wire models for the field jobs API. Hand-rolled fromJson keeps the app
/// free of codegen for plain DTOs.
class JobSummary {
  const JobSummary({
    required this.id,
    required this.title,
    required this.status,
    required this.workType,
    required this.priority,
    this.description,
    this.scheduledStart,
    this.scheduledEnd,
    this.estimatedDurationMinutes,
    this.startedAt,
    this.completedAt,
  });

  final String id;
  final String title;
  final String status;
  final String workType;
  final String priority;
  final String? description;
  final DateTime? scheduledStart;
  final DateTime? scheduledEnd;
  final int? estimatedDurationMinutes;
  final DateTime? startedAt;
  final DateTime? completedAt;

  factory JobSummary.fromJson(Map<String, dynamic> json) => JobSummary(
    id: json['id'] as String,
    title: json['title'] as String,
    status: json['status'] as String,
    workType: json['work_type'] as String,
    priority: json['priority'] as String,
    description: json['description'] as String?,
    scheduledStart: _date(json['scheduled_start']),
    scheduledEnd: _date(json['scheduled_end']),
    estimatedDurationMinutes: json['estimated_duration_minutes'] as int?,
    startedAt: _date(json['started_at']),
    completedAt: _date(json['completed_at']),
  );
}

class JobCustomer {
  const JobCustomer({
    this.name,
    this.phone,
    this.addressText,
    this.servicePlan,
    this.accountNumber,
  });

  final String? name;
  final String? phone;
  final String? addressText;
  final String? servicePlan;
  final String? accountNumber;

  factory JobCustomer.fromJson(Map<String, dynamic> json) => JobCustomer(
    name: json['name'] as String?,
    phone: json['phone'] as String?,
    addressText: json['address_text'] as String?,
    servicePlan: json['service_plan'] as String?,
    accountNumber: json['account_number'] as String?,
  );
}

class JobLocation {
  const JobLocation({
    this.latitude,
    this.longitude,
    this.addressText,
    required this.source,
  });

  final double? latitude;
  final double? longitude;
  final String? addressText;
  final String source;

  factory JobLocation.fromJson(Map<String, dynamic> json) => JobLocation(
    latitude: (json['latitude'] as num?)?.toDouble(),
    longitude: (json['longitude'] as num?)?.toDouble(),
    addressText: json['address_text'] as String?,
    source: json['source'] as String? ?? 'none',
  );

  Map<String, dynamic> toJson() => {
    'latitude': latitude,
    'longitude': longitude,
    'address_text': addressText,
    'source': source,
  };

  bool get hasCoordinates {
    return isValidMapCoordinate(latitude, longitude);
  }

  /// Navigation handoff: precise coordinates when geocoded, otherwise a
  /// text search the maps app can resolve. Null when nothing is known.
  Uri? get mapsUri {
    if (hasCoordinates) {
      return Uri.parse('geo:$latitude,$longitude?q=$latitude,$longitude');
    }
    final address = addressText;
    if (address == null || address.isEmpty) return null;
    return Uri.parse('geo:0,0?q=${Uri.encodeComponent(address)}');
  }
}

class JobDetail {
  const JobDetail({
    required this.job,
    required this.location,
    this.customer,
    this.ticketRef,
    this.notes = const [],
    this.materials = const [],
  });

  final JobSummary job;
  final JobLocation location;
  final JobCustomer? customer;
  final String? ticketRef;
  final List<Map<String, dynamic>> notes;
  final List<Map<String, dynamic>> materials;

  factory JobDetail.fromJson(Map<String, dynamic> json) => JobDetail(
    job: JobSummary.fromJson((json['job'] as Map).cast<String, dynamic>()),
    location: JobLocation.fromJson(
      (json['location'] as Map).cast<String, dynamic>(),
    ),
    customer: json['customer'] != null
        ? JobCustomer.fromJson(
            (json['customer'] as Map).cast<String, dynamic>(),
          )
        : null,
    ticketRef: json['ticket_ref'] as String?,
    notes: ((json['notes'] as List?) ?? [])
        .cast<Map>()
        .map((n) => n.cast<String, dynamic>())
        .toList(),
    materials: ((json['materials'] as List?) ?? [])
        .cast<Map>()
        .map((m) => m.cast<String, dynamic>())
        .toList(),
  );
}

/// The single next action per status — the ActionBar shows exactly one.
String? primaryActionFor(String status) => switch (status) {
  'scheduled' => 'accept',
  'dispatched' => 'start',
  'in_progress' => 'complete',
  _ => null,
};

String actionLabel(String action) => switch (action) {
  'accept' => 'Accept job',
  'start' => 'Start job',
  'complete' => 'Complete job',
  _ => action,
};

DateTime? _date(Object? value) =>
    value is String ? DateTime.tryParse(value) : null;
