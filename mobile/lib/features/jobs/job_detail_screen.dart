import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../app/theme.dart';
import 'job_models.dart';
import 'jobs_providers.dart';

/// Launcher abstraction so widget tests assert the URI without opening apps.
typedef UriLauncher = Future<bool> Function(Uri uri);

final uriLauncherProvider = Provider<UriLauncher>((ref) => launchUrl);

class JobDetailScreen extends ConsumerWidget {
  const JobDetailScreen({super.key, required this.jobId});

  final String jobId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(jobDetailProvider(jobId));

    return detail.when(
      data: (data) => _JobDetailView(detail: data),
      loading: () => const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => Scaffold(
        appBar: AppBar(),
        body: const Center(child: Text('Could not load this job')),
      ),
    );
  }
}

class _JobDetailView extends ConsumerWidget {
  const _JobDetailView({required this.detail});

  final JobDetail detail;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final job = detail.job;
    final action = primaryActionFor(job.status);
    final statusColor = AppColors.status(job.status);

    return Scaffold(
      appBar: AppBar(
        title: Text(job.workType.toUpperCase()),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(
              child: Row(children: [
                Icon(Icons.circle, size: 10, color: statusColor),
                const SizedBox(width: 6),
                Text(job.status.replaceAll('_', ' ')),
              ]),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(job.title, style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700)),
          if (detail.ticketRef != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('Ticket ${detail.ticketRef}', style: Theme.of(context).textTheme.bodySmall),
            ),
          const SizedBox(height: 16),
          _LocationCard(location: detail.location),
          if (detail.customer != null) ...[
            const SizedBox(height: 12),
            _CustomerCard(customer: detail.customer!),
          ],
          if (job.description != null && job.description!.isNotEmpty) ...[
            const SizedBox(height: 12),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Scope of work', style: Theme.of(context).textTheme.titleSmall),
                    const SizedBox(height: 8),
                    Text(job.description!),
                  ],
                ),
              ),
            ),
          ],
          if (detail.materials.isNotEmpty) ...[
            const SizedBox(height: 12),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Materials', style: Theme.of(context).textTheme.titleSmall),
                    const SizedBox(height: 8),
                    for (final material in detail.materials)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(
                          children: [
                            Expanded(child: Text(material['item_name'] as String? ?? 'Item')),
                            Text('×${material['quantity']}'),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ],
          const SizedBox(height: 96),
        ],
      ),
      bottomNavigationBar: action == null
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: FilledButton(
                  key: const Key('primary-action'),
                  onPressed: () {
                    // Wired to the transition outbox in the execution task.
                  },
                  child: Text(actionLabel(action)),
                ),
              ),
            ),
    );
  }
}

class _LocationCard extends ConsumerWidget {
  const _LocationCard({required this.location});

  final JobLocation location;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final uri = location.mapsUri;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.place_outlined, size: 20),
                const SizedBox(width: 8),
                Expanded(child: Text(location.addressText ?? 'No address on file')),
              ],
            ),
            if (uri != null) ...[
              const SizedBox(height: 12),
              OutlinedButton.icon(
                key: const Key('navigate-button'),
                onPressed: () => ref.read(uriLauncherProvider)(uri),
                icon: const Icon(Icons.navigation_outlined),
                label: const Text('Navigate'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _CustomerCard extends ConsumerWidget {
  const _CustomerCard({required this.customer});

  final JobCustomer customer;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            CircleAvatar(child: Text((customer.name ?? '?').substring(0, 1).toUpperCase())),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(customer.name ?? 'Customer',
                      style: Theme.of(context).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w600)),
                  if (customer.servicePlan != null)
                    Text(customer.servicePlan!, style: Theme.of(context).textTheme.bodySmall),
                ],
              ),
            ),
            if (customer.phone != null)
              IconButton(
                key: const Key('call-button'),
                onPressed: () => ref.read(uriLauncherProvider)(Uri.parse('tel:${customer.phone}')),
                icon: const Icon(Icons.call_outlined),
                tooltip: 'Call customer',
              ),
          ],
        ),
      ),
    );
  }
}
