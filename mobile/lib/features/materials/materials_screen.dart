import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import 'material_models.dart';
import 'materials_providers.dart';

const _priorities = ['low', 'medium', 'high', 'urgent'];

class MaterialsScreen extends ConsumerWidget {
  const MaterialsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final requests = ref.watch(materialRequestsProvider);
    final inventory = ref.watch(inventorySearchProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Materials'),
        actions: [
          IconButton(
            tooltip: 'New request',
            onPressed: () => context.push('/materials/new'),
            icon: const Icon(Icons.add),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(materialRequestsProvider);
          ref.invalidate(inventorySearchProvider);
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              key: const Key('inventory-search'),
              decoration: const InputDecoration(
                labelText: 'Search inventory',
                prefixIcon: Icon(Icons.search),
              ),
              onChanged: (value) =>
                  ref.read(inventorySearchQueryProvider.notifier).state = value,
            ),
            const SizedBox(height: 12),
            inventory.when(
              data: (items) => _InventoryPreview(items: items),
              loading: () => const LinearProgressIndicator(),
              error: (_, _) =>
                  const Text('Inventory is not available right now'),
            ),
            const SizedBox(height: 24),
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Requests',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                FilledButton.icon(
                  onPressed: () => context.push('/materials/new'),
                  icon: const Icon(Icons.add),
                  label: const Text('Request'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            requests.when(
              data: (items) {
                if (items.isEmpty) {
                  return const Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: Center(child: Text('No material requests yet')),
                  );
                }
                return Column(
                  children: [
                    for (final request in items)
                      _MaterialRequestTile(request: request),
                  ],
                );
              },
              loading: () => const Padding(
                padding: EdgeInsets.only(top: 48),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (_, _) => const Padding(
                padding: EdgeInsets.only(top: 48),
                child: Center(child: Text('Could not load material requests')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _InventoryPreview extends StatelessWidget {
  const _InventoryPreview({required this.items});

  final List<InventoryItem> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Text('No inventory items found'),
      );
    }
    return Column(
      children: [
        for (final item in items.take(5))
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.inventory_2_outlined),
            title: Text(item.name),
            subtitle: Text(
              [item.sku, item.unit].whereType<String>().join(' · '),
            ),
            trailing: item.availableQuantity == null
                ? null
                : Text('${item.availableQuantity} available'),
          ),
      ],
    );
  }
}

class _MaterialRequestTile extends StatelessWidget {
  const _MaterialRequestTile({required this.request});

  final MaterialRequest request;

  @override
  Widget build(BuildContext context) {
    final date = request.createdAt == null
        ? null
        : DateFormat('d MMM, HH:mm').format(request.createdAt!.toLocal());
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: const Icon(Icons.assignment_outlined),
        title: Text(request.number ?? 'Request ${request.id}'),
        subtitle: Text(
          [
            request.status.replaceAll('_', ' '),
            if (request.priority != null) request.priority,
            ?date,
          ].join(' · '),
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => context.push('/materials/${request.id}'),
      ),
    );
  }
}

class MaterialRequestDetailScreen extends ConsumerWidget {
  const MaterialRequestDetailScreen({super.key, required this.id});

  final String id;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final request = ref.watch(materialRequestProvider(id));
    return Scaffold(
      appBar: AppBar(title: const Text('Material request')),
      body: request.when(
        data: (data) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(
              data.displayNumber,
              style: Theme.of(
                context,
              ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                Chip(label: Text(data.status.replaceAll('_', ' '))),
                if (data.priority != null) Chip(label: Text(data.priority!)),
              ],
            ),
            if (data.notes != null && data.notes!.isNotEmpty) ...[
              const SizedBox(height: 16),
              Text(data.notes!),
            ],
            const SizedBox(height: 24),
            Text('Items', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (data.items.isEmpty)
              const Text('No items on this request')
            else
              for (final item in data.items)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(item.itemName ?? item.itemId),
                  subtitle: item.notes == null ? null : Text(item.notes!),
                  trailing: Text('x${item.quantity}'),
                ),
          ],
        ),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) =>
            const Center(child: Text('Could not load this request')),
      ),
    );
  }
}

class NewMaterialRequestScreen extends ConsumerStatefulWidget {
  const NewMaterialRequestScreen({super.key, this.initialWorkOrderId});

  final String? initialWorkOrderId;

  @override
  ConsumerState<NewMaterialRequestScreen> createState() =>
      _NewMaterialRequestScreenState();
}

class _NewMaterialRequestScreenState
    extends ConsumerState<NewMaterialRequestScreen> {
  final _notes = TextEditingController();
  final _workOrderId = TextEditingController();
  final _projectId = TextEditingController();
  final _ticketId = TextEditingController();
  final _quantity = TextEditingController(text: '1');
  final _itemNotes = TextEditingController();
  String _priority = 'medium';
  InventoryItem? _selectedItem;
  final _items = <MaterialRequestItemDraft>[];
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _workOrderId.text = widget.initialWorkOrderId ?? '';
  }

  @override
  void dispose() {
    _notes.dispose();
    _workOrderId.dispose();
    _projectId.dispose();
    _ticketId.dispose();
    _quantity.dispose();
    _itemNotes.dispose();
    super.dispose();
  }

  void _addItem() {
    final selected = _selectedItem;
    final quantity = int.tryParse(_quantity.text.trim()) ?? 0;
    if (selected == null || quantity < 1) return;
    setState(() {
      _items.add(
        MaterialRequestItemDraft(
          item: selected,
          quantity: quantity,
          notes: _itemNotes.text,
        ),
      );
      _selectedItem = null;
      _quantity.text = '1';
      _itemNotes.clear();
    });
  }

  Future<void> _submit() async {
    if (_items.isEmpty || _saving) return;
    setState(() => _saving = true);
    try {
      final request = await ref
          .read(materialsRepositoryProvider)
          .createRequest(
            priority: _priority,
            notes: _notes.text,
            workOrderId: _workOrderId.text,
            projectId: _projectId.text,
            ticketId: _ticketId.text,
            items: _items,
          );
      ref.invalidate(materialRequestsProvider);
      if (mounted) context.go('/materials/${request.id}');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final inventory = ref.watch(inventorySearchProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('New material request')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          DropdownButtonFormField<String>(
            initialValue: _priority,
            decoration: const InputDecoration(labelText: 'Priority'),
            items: [
              for (final priority in _priorities)
                DropdownMenuItem(value: priority, child: Text(priority)),
            ],
            onChanged: (value) =>
                setState(() => _priority = value ?? _priority),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _workOrderId,
            decoration: const InputDecoration(labelText: 'Work order ID'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _projectId,
            decoration: const InputDecoration(labelText: 'Project ID'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _ticketId,
            decoration: const InputDecoration(labelText: 'Ticket ID'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _notes,
            decoration: const InputDecoration(labelText: 'Notes'),
            maxLines: 3,
          ),
          const SizedBox(height: 24),
          Text('Add items', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            decoration: const InputDecoration(
              labelText: 'Search inventory',
              prefixIcon: Icon(Icons.search),
            ),
            onChanged: (value) =>
                ref.read(inventorySearchQueryProvider.notifier).state = value,
          ),
          const SizedBox(height: 8),
          inventory.when(
            data: (items) => DropdownButtonFormField<InventoryItem>(
              initialValue: _selectedItem,
              decoration: const InputDecoration(labelText: 'Item'),
              items: [
                for (final item in items)
                  DropdownMenuItem(
                    value: item,
                    child: Text(
                      item.sku == null
                          ? item.name
                          : '${item.name} (${item.sku})',
                    ),
                  ),
              ],
              onChanged: (value) => setState(() => _selectedItem = value),
            ),
            loading: () => const LinearProgressIndicator(),
            error: (_, _) => const Text('Inventory search failed'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _quantity,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Quantity'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _itemNotes,
            decoration: const InputDecoration(labelText: 'Item notes'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _addItem,
            icon: const Icon(Icons.add),
            label: const Text('Add item'),
          ),
          const SizedBox(height: 16),
          for (final item in _items)
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(item.item.name),
              subtitle: item.notes == null || item.notes!.isEmpty
                  ? null
                  : Text(item.notes!),
              trailing: Text('x${item.quantity}'),
            ),
          const SizedBox(height: 96),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: FilledButton(
            onPressed: _items.isEmpty || _saving ? null : _submit,
            child: Text(_saving ? 'Submitting...' : 'Submit request'),
          ),
        ),
      ),
    );
  }
}
