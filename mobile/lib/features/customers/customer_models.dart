class CustomerLookupResult {
  const CustomerLookupResult({
    required this.id,
    required this.label,
    required this.ref,
  });

  final String id;
  final String label;
  final String ref;

  factory CustomerLookupResult.fromJson(Map<String, dynamic> json) =>
      CustomerLookupResult(
        id: json['id'].toString(),
        label: json['label'] as String? ?? 'Customer',
        ref: json['ref'] as String? ?? '',
      );

  String? get email {
    final match = RegExp(r'\(([^()@\s]+@[^()@\s]+)\)$').firstMatch(label);
    return match?.group(1);
  }
}
