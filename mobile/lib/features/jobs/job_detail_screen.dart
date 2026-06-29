import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../app/theme.dart';
import '../execution/completion_wizard.dart';
import '../execution/execution_controller.dart';
import 'job_models.dart';
import 'jobs_providers.dart';
import 'location_pin_screen.dart';

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
      loading: () =>
          const Scaffold(body: Center(child: CircularProgressIndicator())),
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
          IconButton(
            tooltip: 'Request materials',
            onPressed: () => context.push(
              '/materials/new?workOrderId=${Uri.encodeComponent(job.id)}',
            ),
            icon: const Icon(Icons.inventory_2_outlined),
          ),
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(
              child: Row(
                children: [
                  Icon(Icons.circle, size: 10, color: statusColor),
                  const SizedBox(width: 6),
                  Text(job.status.replaceAll('_', ' ')),
                ],
              ),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            job.title,
            style: Theme.of(
              context,
            ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700),
          ),
          if (detail.ticketRef != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                'Ticket ${detail.ticketRef}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          const SizedBox(height: 16),
          _LocationCard(jobId: job.id, location: detail.location),
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
                    Text(
                      'Scope of work',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
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
                    Text(
                      'Materials',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: 8),
                    for (final material in detail.materials)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(
                          children: [
                            Expanded(
                              child: Text(
                                material['item_name'] as String? ?? 'Item',
                              ),
                            ),
                            Text('×${material['quantity']}'),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ],
          if (detail.notes.isNotEmpty) ...[
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
                    for (final note in detail.notes)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Text(note['body'] as String? ?? ''),
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
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    FilledButton(
                      key: const Key('primary-action'),
                      onPressed: () async {
                        if (action == 'complete') {
                          await Navigator.of(context).push(
                            MaterialPageRoute(
                              builder: (_) => CompletionWizard(jobId: job.id),
                            ),
                          );
                        } else {
                          await ref
                              .read(executionControllerProvider.notifier)
                              .transition(job.id, action);
                        }
                        ref.invalidate(jobDetailProvider(job.id));
                      },
                      child: Text(actionLabel(action)),
                    ),
                    TextButton(
                      key: const Key('unable-action'),
                      onPressed: () =>
                          promptUnableToComplete(context, ref, job.id),
                      child: const Text("Can't complete this job"),
                    ),
                  ],
                ),
              ),
            ),
    );
  }
}

/// Field outcomes for a visit that can't be completed. Keys mirror the backend
/// ``unable_to_complete`` reasons; labels are tech-facing.
const List<({String key, String label})> kUnableReasons = [
  (key: 'customer_absent', label: 'Customer not home'),
  (key: 'no_access', label: 'Could not access site'),
  (key: 'site_not_ready', label: 'Site not ready'),
  (key: 'needs_parts', label: 'Missing parts/materials'),
  (key: 'unsafe', label: 'Unsafe conditions'),
  (key: 'other', label: 'Other'),
];

/// Ask why the job can't be completed, then record the failed visit (which
/// cancels the job server-side with the chosen reason).
Future<void> promptUnableToComplete(
  BuildContext context,
  WidgetRef ref,
  String jobId,
) async {
  final reason = await showModalBottomSheet<String>(
    context: context,
    builder: (sheetContext) => SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              "Why can't this job be completed?",
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ),
          for (final reasonOption in kUnableReasons)
            ListTile(
              key: Key('reason-${reasonOption.key}'),
              title: Text(reasonOption.label),
              onTap: () => Navigator.of(sheetContext).pop(reasonOption.key),
            ),
        ],
      ),
    ),
  );
  if (reason == null) return;
  await ref
      .read(executionControllerProvider.notifier)
      .unableToComplete(jobId, reason: reason);
  ref.invalidate(jobDetailProvider(jobId));
  if (context.mounted) Navigator.of(context).maybePop();
}

class _LocationCard extends ConsumerWidget {
  const _LocationCard({required this.jobId, required this.location});

  final String jobId;
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
                Expanded(
                  child: Text(location.addressText ?? 'No address on file'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                if (uri != null)
                  OutlinedButton.icon(
                    key: const Key('navigate-button'),
                    onPressed: () => ref.read(uriLauncherProvider)(uri),
                    icon: const Icon(Icons.navigation_outlined),
                    label: const Text('Navigate'),
                  ),
                OutlinedButton.icon(
                  key: const Key('edit-location-button'),
                  onPressed: () async {
                    final changed = await Navigator.of(context).push<bool>(
                      MaterialPageRoute(
                        builder: (_) => LocationPinScreen(
                          jobId: jobId,
                          initialLocation: location,
                        ),
                      ),
                    );
                    if (changed == true) {
                      ref.invalidate(jobDetailProvider(jobId));
                    }
                  },
                  icon: const Icon(Icons.push_pin_outlined),
                  label: Text(
                    location.hasCoordinates ? 'Edit pin' : 'Pin location',
                  ),
                ),
              ],
            ),
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
            CircleAvatar(
              child: Text((customer.name ?? '?').substring(0, 1).toUpperCase()),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    customer.name ?? 'Customer',
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (customer.servicePlan != null)
                    Text(
                      customer.servicePlan!,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                ],
              ),
            ),
            if (customer.phone != null)
              IconButton(
                key: const Key('call-button'),
                onPressed: () => ref.read(uriLauncherProvider)(
                  Uri.parse('tel:${customer.phone}'),
                ),
                icon: const Icon(Icons.call_outlined),
                tooltip: 'Call customer',
              ),
          ],
        ),
      ),
    );
  }
}
