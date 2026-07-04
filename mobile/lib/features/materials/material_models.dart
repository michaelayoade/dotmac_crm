class InventoryItem {
  const InventoryItem({
    required this.id,
    required this.name,
    this.sku,
    this.unit,
    this.unitPrice,
    this.currency,
    this.availableQuantity,
    this.stockByLocation = const [],
  });

  final String id;
  final String name;
  final String? sku;
  final String? unit;
  final double? unitPrice;
  final String? currency;
  final int? availableQuantity;
  final List<InventoryLocationStock> stockByLocation;

  String get displayName => sku == null || sku!.isEmpty ? name : '$name ($sku)';

  factory InventoryItem.fromJson(Map<String, dynamic> json) => InventoryItem(
    id: json['id'].toString(),
    name: json['name'] as String? ?? 'Item',
    sku: json['sku'] as String?,
    unit: json['unit'] as String?,
    unitPrice: _double(json['unit_price']),
    currency: json['currency'] as String?,
    availableQuantity: _int(
      json['available_quantity'] ?? json['quantity_available'],
    ),
    stockByLocation: _stockByLocation(json),
  );
}

class InventoryLocation {
  const InventoryLocation({required this.id, required this.name, this.code});

  final String id;
  final String name;
  final String? code;

  factory InventoryLocation.fromJson(Map<String, dynamic> json) =>
      InventoryLocation(
        id: json['id'].toString(),
        name: json['name'] as String? ?? 'Location',
        code: json['code'] as String?,
      );
}

class InventoryLocationStock {
  const InventoryLocationStock({
    required this.locationId,
    required this.availableQuantity,
    this.locationName,
    this.locationCode,
  });

  final String locationId;
  final String? locationName;
  final String? locationCode;
  final int availableQuantity;

  String get displayLocation {
    final code = locationCode;
    if (locationName == null || locationName!.isEmpty) {
      return code == null || code.isEmpty ? locationId : code;
    }
    return code == null || code.isEmpty
        ? locationName!
        : '$locationName ($code)';
  }

  factory InventoryLocationStock.fromJson(Map<String, dynamic> json) {
    final location = (json['location'] as Map?)?.cast<String, dynamic>();
    return InventoryLocationStock(
      locationId:
          json['location_id']?.toString() ??
          location?['id']?.toString() ??
          'location',
      locationName:
          json['location_name'] as String? ??
          json['name'] as String? ??
          location?['name'] as String?,
      locationCode:
          json['location_code'] as String? ?? location?['code'] as String?,
      availableQuantity:
          _int(
            json['available_quantity'] ??
                json['quantity_available'] ??
                json['quantity'],
          ) ??
          0,
    );
  }
}

class MaterialRequestItemDraft {
  const MaterialRequestItemDraft({
    required this.item,
    required this.quantity,
    this.notes,
  });

  final InventoryItem item;
  final int quantity;
  final String? notes;

  Map<String, dynamic> toJson() => {
    'item_id': item.id,
    'quantity': quantity,
    if (notes != null && notes!.trim().isNotEmpty) 'notes': notes!.trim(),
  };
}

class MaterialRequestItem {
  const MaterialRequestItem({
    required this.id,
    required this.itemId,
    required this.quantity,
    this.itemName,
    this.notes,
    this.approvedQuantity,
    this.issuedQuantity,
    this.fulfilledQuantity,
  });

  final String id;
  final String itemId;
  final int quantity;
  final String? itemName;
  final String? notes;
  final int? approvedQuantity;
  final int? issuedQuantity;
  final int? fulfilledQuantity;

  factory MaterialRequestItem.fromJson(
    Map<String, dynamic> json,
  ) => MaterialRequestItem(
    id: json['id'].toString(),
    itemId: json['item_id'].toString(),
    quantity: _int(json['quantity']) ?? 0,
    itemName:
        json['item_name'] as String? ??
        (json['item'] is Map ? (json['item'] as Map)['name'] as String? : null),
    notes: json['notes'] as String?,
    approvedQuantity: _int(json['approved_quantity']),
    issuedQuantity: _int(json['issued_quantity'] ?? json['quantity_issued']),
    fulfilledQuantity: _int(
      json['fulfilled_quantity'] ?? json['quantity_fulfilled'],
    ),
  );
}

class MaterialRequest {
  const MaterialRequest({
    required this.id,
    required this.status,
    this.number,
    this.priority,
    this.notes,
    this.workOrderId,
    this.projectId,
    this.ticketId,
    this.sourceLocationId,
    this.sourceLocationName,
    this.destinationLocationId,
    this.destinationLocationName,
    this.approvalNotes,
    this.rejectionReason,
    this.issueNotes,
    this.createdAt,
    this.submittedAt,
    this.approvedAt,
    this.rejectedAt,
    this.issuedAt,
    this.fulfilledAt,
    this.items = const [],
  });

  final String id;
  final String status;
  final String? number;
  final String? priority;
  final String? notes;
  final String? workOrderId;
  final String? projectId;
  final String? ticketId;
  final String? sourceLocationId;
  final String? sourceLocationName;
  final String? destinationLocationId;
  final String? destinationLocationName;
  final String? approvalNotes;
  final String? rejectionReason;
  final String? issueNotes;
  final DateTime? createdAt;
  final DateTime? submittedAt;
  final DateTime? approvedAt;
  final DateTime? rejectedAt;
  final DateTime? issuedAt;
  final DateTime? fulfilledAt;
  final List<MaterialRequestItem> items;

  factory MaterialRequest.fromJson(Map<String, dynamic> json) =>
      MaterialRequest(
        id: json['id'].toString(),
        status: json['status'] as String? ?? 'draft',
        number: json['number'] as String?,
        priority: json['priority'] as String?,
        notes: json['notes'] as String?,
        workOrderId: json['work_order_id']?.toString(),
        projectId: json['project_id']?.toString(),
        ticketId: json['ticket_id']?.toString(),
        sourceLocationId: _locationId(json, 'source'),
        sourceLocationName: _locationName(json, 'source'),
        destinationLocationId: _locationId(json, 'destination'),
        destinationLocationName: _locationName(json, 'destination'),
        approvalNotes:
            json['approval_notes'] as String? ??
            json['approved_notes'] as String?,
        rejectionReason:
            json['rejection_reason'] as String? ??
            json['rejected_reason'] as String? ??
            json['rejection_notes'] as String?,
        issueNotes:
            json['issue_notes'] as String? ?? json['issued_notes'] as String?,
        createdAt: _date(json['created_at']),
        submittedAt: _date(json['submitted_at']),
        approvedAt: _date(json['approved_at']),
        rejectedAt: _date(json['rejected_at']),
        issuedAt: _date(json['issued_at']),
        fulfilledAt: _date(json['fulfilled_at']),
        items: ((json['items'] as List?) ?? [])
            .cast<Map>()
            .map(
              (item) =>
                  MaterialRequestItem.fromJson(item.cast<String, dynamic>()),
            )
            .toList(),
      );

  String get displayNumber => number ?? id;

  String? get sourceLocationLabel => sourceLocationName ?? sourceLocationId;

  String? get destinationLocationLabel =>
      destinationLocationName ?? destinationLocationId;
}

int? _int(Object? value) => switch (value) {
  int() => value,
  num() => value.toInt(),
  String() => int.tryParse(value),
  _ => null,
};

double? _double(Object? value) => switch (value) {
  num() => value.toDouble(),
  String() => double.tryParse(value),
  _ => null,
};

DateTime? _date(Object? value) =>
    value is String ? DateTime.tryParse(value) : null;

List<InventoryLocationStock> _stockByLocation(Map<String, dynamic> json) {
  final raw =
      json['stock_by_location'] ??
      json['location_stock'] ??
      json['stocks'] ??
      json['locations'];
  if (raw is! List) return const [];
  return raw
      .whereType<Map>()
      .map(
        (item) => InventoryLocationStock.fromJson(item.cast<String, dynamic>()),
      )
      .toList();
}

String? _locationId(Map<String, dynamic> json, String prefix) {
  final location = (json['${prefix}_location'] as Map?)
      ?.cast<String, dynamic>();
  return json['${prefix}_location_id']?.toString() ??
      location?['id']?.toString();
}

String? _locationName(Map<String, dynamic> json, String prefix) {
  final location = (json['${prefix}_location'] as Map?)
      ?.cast<String, dynamic>();
  return json['${prefix}_location_name'] as String? ??
      location?['name'] as String? ??
      location?['code'] as String?;
}
