import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'customer_models.dart';
import 'customer_providers.dart';

class CustomerLookupScreen extends ConsumerStatefulWidget {
  const CustomerLookupScreen({super.key});

  @override
  ConsumerState<CustomerLookupScreen> createState() =>
      _CustomerLookupScreenState();
}

class _CustomerLookupScreenState extends ConsumerState<CustomerLookupScreen> {
  final _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final results = ref.watch(customerLookupResultsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Customers')),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(customerLookupResultsProvider),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              key: const Key('customer-lookup-search'),
              controller: _search,
              decoration: const InputDecoration(
                labelText: 'Search CRM customers',
                prefixIcon: Icon(Icons.person_search_outlined),
              ),
              onChanged: (value) =>
                  ref.read(customerLookupQueryProvider.notifier).state = value,
            ),
            const SizedBox(height: 16),
            results.when(
              data: (items) {
                if (_search.text.trim().isEmpty) {
                  return const _EmptyState(
                    icon: Icons.search,
                    text: 'Search by customer name or email',
                  );
                }
                if (items.isEmpty) {
                  return const _EmptyState(
                    icon: Icons.person_off_outlined,
                    text: 'No customers found',
                  );
                }
                return Column(
                  children: [
                    for (final customer in items)
                      _CustomerResultCard(customer: customer),
                  ],
                );
              },
              loading: () => const LinearProgressIndicator(),
              error: (_, _) => const _EmptyState(
                icon: Icons.cloud_off_outlined,
                text: 'Customer search failed',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CustomerResultCard extends StatelessWidget {
  const _CustomerResultCard({required this.customer});

  final CustomerLookupResult customer;

  @override
  Widget build(BuildContext context) {
    final email = customer.email;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const CircleAvatar(child: Icon(Icons.person_outline)),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        customer.label,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.titleMedium
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      if (email != null)
                        Text(
                          email,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  onPressed: () => _openSalesOrder(context, customer),
                  icon: const Icon(Icons.receipt_long_outlined),
                  label: const Text('Sales order'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  void _openSalesOrder(BuildContext context, CustomerLookupResult customer) {
    context.push(
      Uri(
        path: '/sales/new',
        queryParameters: {
          'customerId': customer.id,
          'customerLabel': customer.label,
          'customerRef': customer.ref,
        },
      ).toString(),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 48),
      child: Center(
        child: Column(
          children: [
            Icon(icon, size: 36),
            const SizedBox(height: 8),
            Text(text),
          ],
        ),
      ),
    );
  }
}
