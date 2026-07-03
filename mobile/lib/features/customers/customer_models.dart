class CustomerLookupResult {
  const CustomerLookupResult({
    required this.id,
    required this.label,
    required this.ref,
    this.phone,
    this.addressText,
    this.accountStatus,
    this.servicePlan,
    this.explicitEmail,
    this.recentJobs = const [],
    this.recentTickets = const [],
  });

  final String id;
  final String label;
  final String ref;
  final String? phone;
  final String? addressText;
  final String? accountStatus;
  final String? servicePlan;
  final String? explicitEmail;
  final List<CustomerRecentWork> recentJobs;
  final List<CustomerRecentWork> recentTickets;

  factory CustomerLookupResult.fromJson(Map<String, dynamic> json) =>
      CustomerLookupResult(
        id: json['id'].toString(),
        label: json['label'] as String? ?? 'Customer',
        ref: json['ref'] as String? ?? '',
        phone: _string(json, const ['phone', 'phone_number', 'mobile']),
        addressText: _string(json, const [
          'address_text',
          'address',
          'service_address',
        ]),
        accountStatus: _string(json, const [
          'account_status',
          'status',
          'party_status',
        ]),
        servicePlan: _string(json, const ['service_plan', 'plan']),
        explicitEmail: _string(json, const ['email']),
        recentJobs: _workList(json['recent_jobs'] ?? json['jobs']),
        recentTickets: _workList(json['recent_tickets'] ?? json['tickets']),
      );

  factory CustomerLookupResult.fromQuery(Map<String, String> query) =>
      CustomerLookupResult(
        id: query['customerId'] ?? '',
        label: query['customerLabel'] ?? 'Customer',
        ref: query['customerRef'] ?? '',
        phone: _blankToNull(query['phone']),
        addressText: _blankToNull(query['addressText']),
        accountStatus: _blankToNull(query['accountStatus']),
        servicePlan: _blankToNull(query['servicePlan']),
        explicitEmail: _blankToNull(query['email']),
      );

  Map<String, String> toQueryParameters() {
    final params = {
      'customerId': id,
      'customerLabel': label,
      'customerRef': ref,
    };
    final emailValue = email;
    if (emailValue != null) params['email'] = emailValue;
    if (phone != null) params['phone'] = phone!;
    if (addressText != null) params['addressText'] = addressText!;
    if (accountStatus != null) params['accountStatus'] = accountStatus!;
    if (servicePlan != null) params['servicePlan'] = servicePlan!;
    return params;
  }

  String? get email {
    if (explicitEmail != null) return explicitEmail;
    final match = RegExp(r'\(([^()@\s]+@[^()@\s]+)\)$').firstMatch(label);
    return match?.group(1);
  }
}

class CustomerRecentWork {
  const CustomerRecentWork({required this.title, this.status, this.reference});

  final String title;
  final String? status;
  final String? reference;

  factory CustomerRecentWork.fromJson(Map<String, dynamic> json) =>
      CustomerRecentWork(
        title:
            _string(json, const ['title', 'subject', 'summary', 'name']) ??
            'Recent work',
        status: _string(json, const ['status']),
        reference: _string(json, const ['reference', 'ticket_ref', 'id']),
      );
}

String? _string(Map<String, dynamic> json, List<String> keys) {
  for (final key in keys) {
    final value = json[key];
    if (value is String && value.trim().isNotEmpty) return value.trim();
    if (value != null && value is! Map && value is! List) {
      final text = value.toString().trim();
      if (text.isNotEmpty) return text;
    }
  }
  return null;
}

List<CustomerRecentWork> _workList(Object? value) {
  final items = switch (value) {
    final List list => list,
    {'items': final List list} => list,
    _ => const [],
  };
  return [
    for (final item in items)
      if (item is Map)
        CustomerRecentWork.fromJson(item.cast<String, dynamic>()),
  ];
}

String? _blankToNull(String? value) {
  final text = value?.trim();
  return text == null || text.isEmpty ? null : text;
}
