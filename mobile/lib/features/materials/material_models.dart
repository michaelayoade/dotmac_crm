class InventoryItem {
  const InventoryItem({
    required this.id,
    required this.name,
    this.sku,
    this.unit,
    this.availableQuantity,
  });

  final String id;
  final String name;
  final String? sku;
  final String? unit;
  final int? availableQuantity;

  factory InventoryItem.fromJson(Map<String, dynamic> json) => InventoryItem(
    id: json['id'].toString(),
    name: json['name'] as String? ?? 'Item',
    sku: json['sku'] as String?,
    unit: json['unit'] as String?,
    availableQuantity: _int(
      json['available_quantity'] ?? json['quantity_available'],
    ),
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
  });

  final String id;
  final String itemId;
  final int quantity;
  final String? itemName;
  final String? notes;

  factory MaterialRequestItem.fromJson(Map<String, dynamic> json) =>
      MaterialRequestItem(
        id: json['id'].toString(),
        itemId: json['item_id'].toString(),
        quantity: _int(json['quantity']) ?? 0,
        itemName:
            json['item_name'] as String? ??
            (json['item'] is Map
                ? (json['item'] as Map)['name'] as String?
                : null),
        notes: json['notes'] as String?,
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
    this.createdAt,
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
  final DateTime? createdAt;
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
        createdAt: _date(json['created_at']),
        items: ((json['items'] as List?) ?? [])
            .cast<Map>()
            .map(
              (item) =>
                  MaterialRequestItem.fromJson(item.cast<String, dynamic>()),
            )
            .toList(),
      );

  String get displayNumber => number ?? id;
}

int? _int(Object? value) => switch (value) {
  int() => value,
  num() => value.toInt(),
  String() => int.tryParse(value),
  _ => null,
};

DateTime? _date(Object? value) =>
    value is String ? DateTime.tryParse(value) : null;
