import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../execution/execution_controller.dart';
import '../jobs/job_detail_screen.dart' show uriLauncherProvider;
import 'trace_recorder.dart';
import 'vendor_providers.dart';

class VendorProjectsScreen extends ConsumerWidget {
  const VendorProjectsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projects = ref.watch(vendorProjectsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('My projects')),
      body: projects.when(
        data: (items) => items.isEmpty
            ? const Center(child: Text('No assigned projects'))
            : ListView.separated(
                padding: const EdgeInsets.all(16),
                itemCount: items.length,
                separatorBuilder: (_, _) => const SizedBox(height: 12),
                itemBuilder: (context, index) {
                  final item = items[index];
                  final project = item.project;
                  return Card(
                    child: ListTile(
                      title: Text('Project ${project.id.substring(0, 8)}'),
                      subtitle: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (project.notes != null && project.notes!.isNotEmpty) Text(project.notes!),
                          if (item.lifecycle != null) ...[
                            const SizedBox(height: 6),
                            VendorLifecycleChips(lifecycle: item.lifecycle!),
                          ],
                        ],
                      ),
                      isThreeLine: item.lifecycle != null,
                      trailing: Chip(
                        label: Text(project.status.replaceAll('_', ' ')),
                        backgroundColor: AppColors.status(project.status).withValues(alpha: 0.15),
                      ),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(builder: (_) => VendorProjectDetailScreen(projectId: project.id)),
                      ),
                    ),
                  );
                },
              ),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => const Center(child: Text('Could not load projects')),
      ),
    );
  }
}

class VendorProjectDetailScreen extends ConsumerWidget {
  const VendorProjectDetailScreen({super.key, required this.projectId});

  final String projectId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(vendorProjectDetailProvider(projectId));
    return Scaffold(
      appBar: AppBar(title: const Text('Project')),
      body: detail.when(
        data: (data) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (data.site != null && data.site!.hasContact) ...[
              VendorSiteCard(site: data.site!),
              const SizedBox(height: 12),
            ],
            if (data.lifecycle != null) ...[
              VendorLifecycleChips(lifecycle: data.lifecycle!),
              const SizedBox(height: 12),
            ],
            if (data.rejectedForResubmission != null)
              Card(
                color: Theme.of(context).colorScheme.errorContainer,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Resubmission needed',
                          style: Theme.of(context).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
                      if (data.rejectedForResubmission!.reviewNotes != null) ...[
                        const SizedBox(height: 4),
                        Text(data.rejectedForResubmission!.reviewNotes!),
                      ],
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 12),
            Text('Submissions', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (data.submissions.isEmpty) const Text('None yet'),
            for (final submission in data.submissions)
              ListTile(
                dense: true,
                leading: Icon(Icons.route_outlined, color: AppColors.status(submission.status)),
                title: Text(submission.status.replaceAll('_', ' ')),
                subtitle: submission.actualLengthMeters != null
                    ? Text('${submission.actualLengthMeters!.toStringAsFixed(0)} m')
                    : null,
              ),
            const SizedBox(height: 96),
          ],
        ),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => const Center(child: Text('Could not load project')),
      ),
      bottomNavigationBar: detail.maybeWhen(
        data: (data) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: FilledButton.icon(
              key: const Key('start-capture'),
              icon: const Icon(Icons.route),
              label: Text(data.rejectedForResubmission != null ? 'Recapture as-built' : 'Capture as-built'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => AsBuiltCaptureScreen(
                    projectId: projectId,
                    prefillLengthMeters: data.rejectedForResubmission?.actualLengthMeters,
                  ),
                ),
              ),
            ),
          ),
        ),
        orElse: () => null,
      ),
    );
  }
}

class AsBuiltCaptureScreen extends ConsumerStatefulWidget {
  const AsBuiltCaptureScreen({super.key, required this.projectId, this.prefillLengthMeters});

  final String projectId;
  final double? prefillLengthMeters;

  @override
  ConsumerState<AsBuiltCaptureScreen> createState() => _AsBuiltCaptureScreenState();
}

class _AsBuiltCaptureScreenState extends ConsumerState<AsBuiltCaptureScreen> {
  final recorder = TraceRecorder();
  Timer? _sampler;
  bool _submitting = false;

  @override
  void dispose() {
    _sampler?.cancel();
    super.dispose();
  }

  void _toggleRecording() {
    setState(() {
      if (recorder.recording) {
        recorder.stop();
        _sampler?.cancel();
      } else {
        recorder.start();
        _sampler = Timer.periodic(const Duration(seconds: 3), (_) async {
          final point = await ref.read(locationSourceProvider).current();
          if (point != null && mounted) setState(() => recorder.addPoint(point));
        });
      }
    });
  }

  Future<void> _submit() async {
    setState(() => _submitting = true);
    await ref.read(vendorRepositoryProvider).submitAsBuilt(
          projectId: widget.projectId,
          geojson: recorder.toGeoJson(),
          actualLengthMeters: recorder.distanceMeters,
        );
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('As-built submitted — will sync when online')),
      );
      Navigator.of(context).pop(true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('As-built capture')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (widget.prefillLengthMeters != null)
              Text(
                'Previous submission: ${widget.prefillLengthMeters!.toStringAsFixed(0)} m (rejected)',
                key: const Key('prefill-banner'),
              ),
            const SizedBox(height: 12),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  children: [
                    Text(
                      '${recorder.points.length} points · ${recorder.distanceMeters.toStringAsFixed(0)} m',
                      key: const Key('trace-pill'),
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 16),
                    FilledButton.icon(
                      key: const Key('record-toggle'),
                      onPressed: _toggleRecording,
                      icon: Icon(recorder.recording ? Icons.stop : Icons.fiber_manual_record),
                      label: Text(recorder.recording ? 'Stop recording' : 'Start walking the route'),
                    ),
                  ],
                ),
              ),
            ),
            const Spacer(),
            FilledButton(
              key: const Key('submit-asbuilt'),
              onPressed: !recorder.recording && recorder.hasUsableTrace && !_submitting ? _submit : null,
              child: const Text('Review & submit'),
            ),
          ],
        ),
      ),
    );
  }
}

/// "Who to call, where to go" — the site bundle on a vendor project (#122).
class VendorSiteCard extends ConsumerWidget {
  const VendorSiteCard({super.key, required this.site});

  final VendorSite site;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Site contact', style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (site.name != null && site.name!.isNotEmpty) Text(site.name!),
                      if (site.addressText != null && site.addressText!.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(site.addressText!, style: theme.textTheme.bodySmall),
                      ],
                    ],
                  ),
                ),
                if (site.phone != null)
                  IconButton(
                    key: const Key('vendor-call-button'),
                    tooltip: 'Call site contact',
                    icon: const Icon(Icons.call_outlined),
                    onPressed: () => ref.read(uriLauncherProvider)(Uri.parse('tel:${site.phone}')),
                  ),
              ],
            ),
            if (site.accessNotes != null && site.accessNotes!.isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: theme.colorScheme.secondaryContainer.withValues(alpha: 0.4),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.vpn_key_outlined, size: 16),
                    const SizedBox(width: 8),
                    Expanded(child: Text(site.accessNotes!, style: theme.textTheme.bodySmall)),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Chips for the bid → approval → as-built → payment lifecycle (#123). Only the
/// stages the crew has reached are shown.
class VendorLifecycleChips extends StatelessWidget {
  const VendorLifecycleChips({super.key, required this.lifecycle});

  final VendorLifecycle lifecycle;

  static Widget? _chip(String prefix, VendorStageState? stage) {
    if (stage == null || !stage.isPresent) return null;
    final status = stage.status!;
    final text = stage.label != null ? '$prefix: ${status.replaceAll('_', ' ')} · ${stage.label}'
        : '$prefix: ${status.replaceAll('_', ' ')}';
    return Chip(
      label: Text(text),
      visualDensity: VisualDensity.compact,
      backgroundColor: AppColors.status(status).withValues(alpha: 0.15),
    );
  }

  @override
  Widget build(BuildContext context) {
    final chips = <Widget?>[
      _chip('Quote', lifecycle.quote),
      _chip('As-built', lifecycle.asBuilt),
      _chip('Billing', lifecycle.billing),
    ].whereType<Widget>().toList();
    if (chips.isEmpty) return const SizedBox.shrink();
    return Wrap(spacing: 6, runSpacing: 6, children: chips);
  }
}
