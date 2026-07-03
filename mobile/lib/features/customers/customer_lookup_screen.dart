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

class _CustomerResultCard extends ConsumerWidget {
  const _CustomerResultCard({required this.customer});

  final CustomerLookupResult customer;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final email = customer.email;
    final details = [
      if (customer.phone != null) (Icons.call_outlined, customer.phone!),
      if (customer.addressText != null)
        (Icons.location_on_outlined, customer.addressText!),
      if (customer.accountStatus != null)
        (Icons.verified_user_outlined, customer.accountStatus!),
      if (customer.servicePlan != null)
        (Icons.speed_outlined, customer.servicePlan!),
    ];
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
                IconButton(
                  tooltip: 'View customer',
                  onPressed: () => _openDetails(context, customer),
                  icon: const Icon(Icons.chevron_right),
                ),
              ],
            ),
            if (details.isNotEmpty) ...[
              const SizedBox(height: 12),
              for (final detail in details)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: _DetailLine(icon: detail.$1, text: detail.$2),
                ),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (customer.phone != null)
                  IconButton.outlined(
                    tooltip: 'Call customer',
                    onPressed: () => _launch(ref, 'tel:${customer.phone}'),
                    icon: const Icon(Icons.call_outlined),
                  ),
                if (customer.phone != null)
                  IconButton.outlined(
                    tooltip: 'WhatsApp customer',
                    onPressed: () => _launch(
                      ref,
                      'https://wa.me/${_digits(customer.phone!)}',
                    ),
                    icon: const Icon(Icons.chat_outlined),
                  ),
                if (email != null)
                  IconButton.outlined(
                    tooltip: 'Email customer',
                    onPressed: () => _launch(ref, 'mailto:$email'),
                    icon: const Icon(Icons.email_outlined),
                  ),
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

  void _openDetails(BuildContext context, CustomerLookupResult customer) {
    context.push(
      Uri(
        path: '/customers/${customer.id}',
        queryParameters: customer.toQueryParameters(),
      ).toString(),
      extra: customer,
    );
  }

  void _openSalesOrder(BuildContext context, CustomerLookupResult customer) {
    context.push(
      Uri(
        path: '/sales/new',
        queryParameters: customer.toQueryParameters(),
      ).toString(),
    );
  }

  Future<void> _launch(WidgetRef ref, String value) async {
    await ref.read(customerUriLauncherProvider).call(Uri.parse(value));
  }
}

class CustomerDetailScreen extends ConsumerWidget {
  const CustomerDetailScreen({super.key, required this.customer});

  final CustomerLookupResult customer;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final email = customer.email;
    return Scaffold(
      appBar: AppBar(title: const Text('Customer')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              const CircleAvatar(radius: 28, child: Icon(Icons.person_outline)),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  customer.label,
                  style: Theme.of(
                    context,
                  ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Details',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 12),
                  _InfoRow(label: 'Phone', value: customer.phone),
                  _InfoRow(label: 'Email', value: email),
                  _InfoRow(label: 'Address', value: customer.addressText),
                  _InfoRow(
                    label: 'Account status',
                    value: customer.accountStatus,
                  ),
                  _InfoRow(label: 'Service plan', value: customer.servicePlan),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (customer.phone != null)
                FilledButton.icon(
                  onPressed: () => ref
                      .read(customerUriLauncherProvider)
                      .call(Uri.parse('tel:${customer.phone}')),
                  icon: const Icon(Icons.call_outlined),
                  label: const Text('Call'),
                ),
              if (customer.phone != null)
                FilledButton.icon(
                  onPressed: () => ref
                      .read(customerUriLauncherProvider)
                      .call(
                        Uri.parse('https://wa.me/${_digits(customer.phone!)}'),
                      ),
                  icon: const Icon(Icons.chat_outlined),
                  label: const Text('WhatsApp'),
                ),
              if (email != null)
                FilledButton.icon(
                  onPressed: () => ref
                      .read(customerUriLauncherProvider)
                      .call(Uri.parse('mailto:$email')),
                  icon: const Icon(Icons.email_outlined),
                  label: const Text('Email'),
                ),
              OutlinedButton.icon(
                onPressed: () => context.push(
                  Uri(
                    path: '/sales/new',
                    queryParameters: customer.toQueryParameters(),
                  ).toString(),
                ),
                icon: const Icon(Icons.receipt_long_outlined),
                label: const Text('Sales order'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _RecentWorkCard(title: 'Recent jobs', items: customer.recentJobs),
          const SizedBox(height: 12),
          _RecentWorkCard(
            title: 'Recent tickets',
            items: customer.recentTickets,
          ),
        ],
      ),
    );
  }
}

class _DetailLine extends StatelessWidget {
  const _DetailLine({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 18),
        const SizedBox(width: 8),
        Expanded(
          child: Text(text, maxLines: 2, overflow: TextOverflow.ellipsis),
        ),
      ],
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String? value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 112, child: Text(label)),
          Expanded(
            child: Text(
              value ?? 'Not available',
              style: TextStyle(
                fontWeight: value == null ? FontWeight.normal : FontWeight.w600,
                color: value == null
                    ? Theme.of(context).colorScheme.onSurfaceVariant
                    : null,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RecentWorkCard extends StatelessWidget {
  const _RecentWorkCard({required this.title, required this.items});

  final String title;
  final List<CustomerRecentWork> items;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            if (items.isEmpty)
              const Text('No recent activity available')
            else
              for (final item in items)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(item.title),
                  subtitle: Text(
                    [
                      if (item.reference != null) item.reference!,
                      if (item.status != null) item.status!,
                    ].join(' · '),
                  ),
                ),
          ],
        ),
      ),
    );
  }
}

String _digits(String value) => value.replaceAll(RegExp(r'[^0-9]'), '');

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
