import '../materials/material_models.dart';

class SalesCustomer {
  const SalesCustomer({
    required this.id,
    required this.label,
    required this.ref,
  });

  final String id;
  final String label;
  final String ref;

  factory SalesCustomer.fromJson(Map<String, dynamic> json) => SalesCustomer(
    id: json['id'].toString(),
    label: json['label'] as String? ?? 'Customer',
    ref: json['ref'] as String? ?? '',
  );
}

class SalesOrderLineDraft {
  const SalesOrderLineDraft({
    this.item,
    required this.description,
    required this.quantity,
    required this.unitPrice,
  });

  final InventoryItem? item;
  final String description;
  final double quantity;
  final double unitPrice;

  double get amount => quantity * unitPrice;

  Map<String, dynamic> toJson() => {
    if (item != null) 'inventory_item_id': item!.id,
    'description': description,
    'quantity': quantity,
    'unit_price': unitPrice,
  };
}

class SalesOrderLine {
  const SalesOrderLine({
    required this.id,
    required this.description,
    required this.quantity,
    required this.unitPrice,
    required this.amount,
    this.inventoryItemId,
  });

  final String id;
  final String description;
  final double quantity;
  final double unitPrice;
  final double amount;
  final String? inventoryItemId;

  factory SalesOrderLine.fromJson(Map<String, dynamic> json) => SalesOrderLine(
    id: json['id'].toString(),
    description: json['description'] as String? ?? 'Item',
    quantity: _double(json['quantity']),
    unitPrice: _double(json['unit_price']),
    amount: _double(json['amount']),
    inventoryItemId: json['inventory_item_id']?.toString(),
  );
}

class SalesOrder {
  const SalesOrder({
    required this.id,
    required this.status,
    required this.paymentStatus,
    required this.currency,
    required this.total,
    required this.balanceDue,
    this.orderNumber,
    this.notes,
    this.createdAt,
    this.lines = const [],
  });

  final String id;
  final String status;
  final String paymentStatus;
  final String currency;
  final double total;
  final double balanceDue;
  final String? orderNumber;
  final String? notes;
  final DateTime? createdAt;
  final List<SalesOrderLine> lines;

  factory SalesOrder.fromJson(Map<String, dynamic> json) => SalesOrder(
    id: json['id'].toString(),
    status: json['status'] as String? ?? 'draft',
    paymentStatus: json['payment_status'] as String? ?? 'pending',
    currency: json['currency'] as String? ?? 'NGN',
    total: _double(json['total']),
    balanceDue: _double(json['balance_due']),
    orderNumber: json['order_number'] as String?,
    notes: json['notes'] as String?,
    createdAt: json['created_at'] is String
        ? DateTime.tryParse(json['created_at'] as String)
        : null,
    lines: ((json['lines'] as List?) ?? [])
        .cast<Map>()
        .map((line) => SalesOrderLine.fromJson(line.cast<String, dynamic>()))
        .toList(),
  );

  String get displayNumber => orderNumber ?? id;
}

double _double(Object? value) => switch (value) {
  num() => value.toDouble(),
  String() => double.tryParse(value) ?? 0,
  _ => 0,
};
