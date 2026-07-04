import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../core/offline/draft_store.dart';
import '../materials/material_models.dart';
import '../materials/materials_providers.dart';
import 'sales_models.dart';
import 'sales_providers.dart';

class SalesScreen extends ConsumerWidget {
  const SalesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final orders = ref.watch(salesOrdersProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Sales'),
        actions: [
          IconButton(
            tooltip: 'New sales order',
            onPressed: () => context.push('/sales/new'),
            icon: const Icon(Icons.add),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(salesOrdersProvider),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          children: [
            FilledButton.icon(
              onPressed: () => context.push('/sales/new'),
              icon: const Icon(Icons.add),
              label: const Text('Create sales order'),
            ),
            const SizedBox(height: 16),
            Text(
              'Recent orders',
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            orders.when(
              data: (items) {
                if (items.isEmpty) {
                  return const Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: Center(child: Text('No sales orders yet')),
                  );
                }
                return Column(
                  children: [
                    for (final order in items) _SalesOrderTile(order: order),
                  ],
                );
              },
              loading: () => const Padding(
                padding: EdgeInsets.only(top: 48),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (_, _) => const Padding(
                padding: EdgeInsets.only(top: 48),
                child: Center(child: Text('Could not load sales orders')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SalesOrderTile extends StatelessWidget {
  const _SalesOrderTile({required this.order});

  final SalesOrder order;

  @override
  Widget build(BuildContext context) {
    final date = order.createdAt == null
        ? null
        : DateFormat('d MMM, HH:mm').format(order.createdAt!.toLocal());
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: const Icon(Icons.receipt_long_outlined),
        title: Text(order.displayNumber),
        subtitle: Text(
          [
            order.status.replaceAll('_', ' '),
            order.paymentStatus.replaceAll('_', ' '),
            ?date,
          ].join(' · '),
        ),
        trailing: Text('${order.currency} ${order.total.toStringAsFixed(2)}'),
        onTap: () => context.push('/sales/${order.id}'),
      ),
    );
  }
}

class SalesOrderDetailScreen extends ConsumerWidget {
  const SalesOrderDetailScreen({super.key, required this.id});

  final String id;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final order = ref.watch(salesOrderProvider(id));
    return Scaffold(
      appBar: AppBar(title: const Text('Sales order')),
      body: order.when(
        data: (item) => RefreshIndicator(
          onRefresh: () async {
            ref
              ..invalidate(salesOrdersProvider)
              ..invalidate(salesOrderProvider(id));
          },
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            children: [
              Text(
                item.displayNumber,
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                DateFormat(
                  'd MMM yyyy, HH:mm',
                ).format((item.createdAt ?? DateTime.now()).toLocal()),
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 16),
              _OrderSummaryCard(order: item),
              const SizedBox(height: 12),
              _OrderLinesCard(order: item),
              if (item.notes != null && item.notes!.trim().isNotEmpty) ...[
                const SizedBox(height: 12),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Notes',
                          style: Theme.of(context).textTheme.titleSmall,
                        ),
                        const SizedBox(height: 8),
                        Text(item.notes!),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) =>
            const Center(child: Text('Could not load this sales order')),
      ),
    );
  }
}

class _OrderSummaryCard extends StatelessWidget {
  const _OrderSummaryCard({required this.order});

  final SalesOrder order;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Customer', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 6),
            Text(order.customerDisplay),
            const Divider(height: 24),
            _SummaryRow(
              label: 'Status',
              value: order.status.replaceAll('_', ' '),
            ),
            _SummaryRow(
              label: 'Payment',
              value: order.paymentStatus.replaceAll('_', ' '),
            ),
            _SummaryRow(
              label: 'Subtotal',
              value: _money(order.currency, order.subtotal),
            ),
            _SummaryRow(
              label: 'Total',
              value: _money(order.currency, order.total),
            ),
            _SummaryRow(
              label: 'Balance due',
              value: _money(order.currency, order.balanceDue),
            ),
          ],
        ),
      ),
    );
  }
}

class _SummaryRow extends StatelessWidget {
  const _SummaryRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(child: Text(label)),
          Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

class _OrderLinesCard extends StatelessWidget {
  const _OrderLinesCard({required this.order});

  final SalesOrder order;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Lines', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            if (order.lines.isEmpty)
              const Text('No line items')
            else
              for (final line in order.lines)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              line.description,
                              style: const TextStyle(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '${_formatQuantity(line.quantity)} x ${_money(order.currency, line.unitPrice)}',
                            ),
                          ],
                        ),
                      ),
                      Text(_money(order.currency, line.amount)),
                    ],
                  ),
                ),
          ],
        ),
      ),
    );
  }
}

class NewSalesOrderScreen extends ConsumerStatefulWidget {
  const NewSalesOrderScreen({
    super.key,
    this.initialCustomerId,
    this.initialCustomerLabel,
    this.initialCustomerRef,
  });

  final String? initialCustomerId;
  final String? initialCustomerLabel;
  final String? initialCustomerRef;

  @override
  ConsumerState<NewSalesOrderScreen> createState() =>
      _NewSalesOrderScreenState();
}

class _NewSalesOrderScreenState extends ConsumerState<NewSalesOrderScreen> {
  final _customerSearch = TextEditingController();
  final _itemSearch = TextEditingController();
  final _description = TextEditingController();
  final _quantity = TextEditingController(text: '1');
  final _unitPrice = TextEditingController(text: '0');
  final _notes = TextEditingController();

  SalesCustomer? _selectedCustomer;
  InventoryItem? _selectedItem;
  final _lines = <SalesOrderLineDraft>[];
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final customerId = widget.initialCustomerId;
    final customerLabel = widget.initialCustomerLabel;
    if (customerId != null && customerLabel != null) {
      _selectedCustomer = SalesCustomer(
        id: customerId,
        label: customerLabel,
        ref: widget.initialCustomerRef ?? 'person:$customerId',
      );
      _customerSearch.text = customerLabel;
    }
    Future.microtask(_loadDraft);
  }

  @override
  void dispose() {
    _customerSearch.dispose();
    _itemSearch.dispose();
    _description.dispose();
    _quantity.dispose();
    _unitPrice.dispose();
    _notes.dispose();
    super.dispose();
  }

  void _selectItem(InventoryItem item) {
    setState(() {
      _selectedItem = item;
      _itemSearch.text = item.displayName;
      if (_description.text.trim().isEmpty) {
        _description.text = item.name;
      }
      final price = item.unitPrice;
      if (price != null && price > 0) {
        _unitPrice.text = price.toStringAsFixed(2);
      }
    });
  }

  void _addLine() {
    final description = _description.text.trim();
    final quantity = double.tryParse(_quantity.text.trim()) ?? 0;
    final unitPrice = double.tryParse(_unitPrice.text.trim()) ?? 0;
    if (description.isEmpty || quantity <= 0 || unitPrice < 0) return;
    setState(() {
      _lines.add(
        SalesOrderLineDraft(
          item: _selectedItem,
          description: description,
          quantity: quantity,
          unitPrice: unitPrice,
        ),
      );
      _selectedItem = null;
      _itemSearch.clear();
      _description.clear();
      _quantity.text = '1';
      _unitPrice.text = '0';
    });
  }

  Future<void> _loadDraft() async {
    final draft = await ref.read(draftStoreProvider).load(salesOrderDraftId);
    if (!mounted || draft == null) return;
    final customer = (draft['customer'] as Map?)?.cast<String, dynamic>();
    setState(() {
      if (widget.initialCustomerId == null && customer != null) {
        _selectedCustomer = SalesCustomer.fromJson(customer);
        _customerSearch.text = _selectedCustomer!.label;
      }
      _notes.text = draft['notes'] as String? ?? '';
      _lines
        ..clear()
        ..addAll(_salesDraftLines(draft['lines']));
    });
  }

  Future<void> _saveDraft() async {
    await ref
        .read(draftStoreProvider)
        .save(
          id: salesOrderDraftId,
          type: 'sales_order',
          payload: {
            'customer': _selectedCustomer == null
                ? null
                : _salesCustomerJson(_selectedCustomer!),
            'notes': _notes.text,
            'lines': _lines.map(_salesDraftLineJson).toList(),
          },
        );
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('Draft saved')));
  }

  Future<void> _submit() async {
    final customer = _selectedCustomer;
    if (customer == null || _lines.isEmpty || _saving) return;
    setState(() => _saving = true);
    try {
      final order = await ref
          .read(salesRepositoryProvider)
          .createOrder(customer: customer, lines: _lines, notes: _notes.text);
      ref.invalidate(salesOrdersProvider);
      await ref.read(draftStoreProvider).delete(salesOrderDraftId);
      if (mounted) {
        context.go('/sales/${order.id}');
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _editLine(int index) {
    final line = _lines[index];
    setState(() {
      _lines.removeAt(index);
      _selectedItem = line.item;
      _itemSearch.text = line.item?.displayName ?? '';
      _description.text = line.description;
      _quantity.text = _formatQuantity(line.quantity);
      _unitPrice.text = line.unitPrice.toStringAsFixed(2);
    });
  }

  void _removeLine(int index) {
    setState(() => _lines.removeAt(index));
  }

  @override
  Widget build(BuildContext context) {
    final customers = ref.watch(customerSearchProvider);
    final inventory = ref.watch(inventorySearchProvider);
    final total = _lines.fold<double>(0, (sum, line) => sum + line.amount);

    return Scaffold(
      appBar: AppBar(title: const Text('New sales order')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _customerSearch,
            decoration: const InputDecoration(
              labelText: 'Search customer',
              prefixIcon: Icon(Icons.person_search_outlined),
            ),
            onChanged: (value) {
              setState(() => _selectedCustomer = null);
              ref.read(customerSearchQueryProvider.notifier).state = value;
            },
          ),
          const SizedBox(height: 8),
          customers.when(
            data: (items) => _CustomerSuggestions(
              items: items,
              selectedCustomer: _selectedCustomer,
              onSelected: (customer) => setState(() {
                _selectedCustomer = customer;
                _customerSearch.text = customer.label;
              }),
            ),
            loading: () => const LinearProgressIndicator(),
            error: (_, _) => const Text('Customer search failed'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _notes,
            decoration: const InputDecoration(labelText: 'Notes'),
            maxLines: 3,
          ),
          const SizedBox(height: 24),
          Text('Add item', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _itemSearch,
            decoration: const InputDecoration(
              labelText: 'Search inventory',
              prefixIcon: Icon(Icons.search),
            ),
            onChanged: (value) {
              setState(() => _selectedItem = null);
              ref.read(inventorySearchQueryProvider.notifier).state = value;
            },
          ),
          const SizedBox(height: 8),
          inventory.when(
            data: (items) => _InventorySuggestions(
              items: items,
              selectedItem: _selectedItem,
              onSelected: _selectItem,
            ),
            loading: () => const LinearProgressIndicator(),
            error: (_, _) => const Text('Inventory search failed'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _description,
            decoration: const InputDecoration(labelText: 'Description'),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _quantity,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Quantity'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _unitPrice,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Unit price'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const Key('add-sales-line-action'),
            onPressed: _addLine,
            icon: const Icon(Icons.add),
            label: const Text('Add line'),
          ),
          const SizedBox(height: 16),
          for (final (index, line) in _lines.indexed)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.receipt_long_outlined),
              title: Text(line.description),
              subtitle: Text(
                '${_formatQuantity(line.quantity)} x NGN ${line.unitPrice.toStringAsFixed(2)}',
              ),
              trailing: Wrap(
                spacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(_money('NGN', line.amount)),
                  IconButton(
                    tooltip: 'Edit line',
                    onPressed: () => _editLine(index),
                    icon: const Icon(Icons.edit_outlined),
                  ),
                  IconButton(
                    tooltip: 'Remove line',
                    onPressed: () => _removeLine(index),
                    icon: const Icon(Icons.delete_outline),
                  ),
                ],
              ),
            ),
          if (_lines.isNotEmpty)
            Align(
              alignment: Alignment.centerRight,
              child: Text(
                'Total NGN ${total.toStringAsFixed(2)}',
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
            ),
          const SizedBox(height: 96),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              OutlinedButton.icon(
                onPressed: _saving ? null : _saveDraft,
                icon: const Icon(Icons.save_outlined),
                label: const Text('Save draft'),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  onPressed:
                      _selectedCustomer == null || _lines.isEmpty || _saving
                      ? null
                      : _submit,
                  child: Text(_saving ? 'Submitting...' : 'Submit sales order'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

String _formatQuantity(double value) => value == value.roundToDouble()
    ? value.toInt().toString()
    : value.toString();

String _money(String currency, double value) =>
    '$currency ${value.toStringAsFixed(2)}';

Map<String, dynamic> _salesCustomerJson(SalesCustomer customer) => {
  'id': customer.id,
  'label': customer.label,
  'ref': customer.ref,
};

Map<String, dynamic> _salesDraftLineJson(SalesOrderLineDraft line) => {
  'item': line.item == null ? null : _salesInventoryItemJson(line.item!),
  'description': line.description,
  'quantity': line.quantity,
  'unit_price': line.unitPrice,
};

List<SalesOrderLineDraft> _salesDraftLines(Object? raw) {
  if (raw is! List) return const [];
  return raw.whereType<Map>().map((line) {
    final data = line.cast<String, dynamic>();
    final item = (data['item'] as Map?)?.cast<String, dynamic>();
    return SalesOrderLineDraft(
      item: item == null ? null : InventoryItem.fromJson(item),
      description: data['description'] as String? ?? 'Item',
      quantity: _doubleDraft(data['quantity']),
      unitPrice: _doubleDraft(data['unit_price']),
    );
  }).toList();
}

Map<String, dynamic> _salesInventoryItemJson(InventoryItem item) => {
  'id': item.id,
  'name': item.name,
  'sku': item.sku,
  'unit': item.unit,
  'unit_price': item.unitPrice,
  'currency': item.currency,
  'available_quantity': item.availableQuantity,
};

double _doubleDraft(Object? value) => switch (value) {
  num() => value.toDouble(),
  String() => double.tryParse(value) ?? 0,
  _ => 0,
};

class _CustomerSuggestions extends StatelessWidget {
  const _CustomerSuggestions({
    required this.items,
    required this.selectedCustomer,
    required this.onSelected,
  });

  final List<SalesCustomer> items;
  final SalesCustomer? selectedCustomer;
  final ValueChanged<SalesCustomer> onSelected;

  @override
  Widget build(BuildContext context) {
    final selected = selectedCustomer;
    if (selected != null) {
      return InputDecorator(
        decoration: const InputDecoration(labelText: 'Selected customer'),
        child: Row(
          children: [
            Expanded(
              child: Text(
                selected.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: 8),
            const Icon(Icons.check_circle_outline, size: 18),
          ],
        ),
      );
    }
    if (items.isEmpty) return const SizedBox.shrink();
    return ConstrainedBox(
      constraints: const BoxConstraints(maxHeight: 220),
      child: ListView.separated(
        shrinkWrap: true,
        itemCount: items.length.clamp(0, 6),
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final customer = items[index];
          return ListTile(
            dense: true,
            contentPadding: const EdgeInsets.symmetric(horizontal: 12),
            leading: const Icon(Icons.person_outline, size: 20),
            title: Text(
              customer.label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            onTap: () => onSelected(customer),
          );
        },
      ),
    );
  }
}

class _InventorySuggestions extends StatelessWidget {
  const _InventorySuggestions({
    required this.items,
    required this.selectedItem,
    required this.onSelected,
  });

  final List<InventoryItem> items;
  final InventoryItem? selectedItem;
  final ValueChanged<InventoryItem> onSelected;

  @override
  Widget build(BuildContext context) {
    final selected = selectedItem;
    if (selected != null) {
      return InputDecorator(
        decoration: const InputDecoration(labelText: 'Selected item'),
        child: Row(
          children: [
            Expanded(
              child: Text(
                selected.displayName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: 8),
            const Icon(Icons.check_circle_outline, size: 18),
          ],
        ),
      );
    }
    if (items.isEmpty) return const SizedBox.shrink();
    return ConstrainedBox(
      constraints: const BoxConstraints(maxHeight: 220),
      child: ListView.separated(
        shrinkWrap: true,
        itemCount: items.length.clamp(0, 6),
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final item = items[index];
          return ListTile(
            dense: true,
            contentPadding: const EdgeInsets.symmetric(horizontal: 12),
            leading: const Icon(Icons.inventory_2_outlined, size: 20),
            title: Text(
              item.displayName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            onTap: () => onSelected(item),
          );
        },
      ),
    );
  }
}
