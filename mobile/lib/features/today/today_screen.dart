import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../app/theme.dart';
import '../../app/widgets/page_header.dart';
import '../../app/widgets/section_header.dart';
import '../../app/widgets/stat_tile.dart';
import '../jobs/job_models.dart';
import '../jobs/jobs_providers.dart';
import '../jobs/widgets/job_card.dart';
import '../location/location_tracking_controller.dart';
import '../profile/profile_screen.dart';

const _filters = <(String?, String)>[
  (null, 'All'),
  ('dispatched', 'Assigned'),
  ('in_progress', 'Active'),
  ('completed', 'Done'),
];

class TodayScreen extends ConsumerWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final me = ref.watch(meProvider);
    final jobs = ref.watch(todayJobsProvider);
    final filter = ref.watch(jobsFilterProvider);
    final jobList = jobs.value?.jobs ?? const <JobSummary>[];
    final firstName = _firstName(me.value?.name);
    final todayLabel = DateFormat('EEEE · d MMM').format(DateTime.now());

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          onRefresh: () async {
            ref.invalidate(meProvider);
            ref.invalidate(todayJobsProvider);
          },
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(
                child: PageHeader(
                  eyebrow: todayLabel.toUpperCase(),
                  title: firstName == null ? 'Hi there' : 'Hi, $firstName',
                  subtitle: jobList.isEmpty
                      ? 'Nothing scheduled yet'
                      : '${jobList.length} ${jobList.length == 1 ? 'job' : 'jobs'} on your route today',
                  trailing: HeaderActions(name: me.value?.name),
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpace.page),
                sliver: SliverToBoxAdapter(
                  child: Column(
                    children: [
                      const SyncStatusBar(),
                      const SizedBox(height: AppSpace.md),
                      Row(
                        children: [
                          Expanded(
                            child: StatTile(
                              value: '${me.value?.openJobs ?? 0}'.padLeft(2, '0'),
                              label: 'Assigned',
                              highlighted: true,
                            ),
                          ),
                          const SizedBox(width: AppSpace.md),
                          Expanded(
                            child: StatTile(
                              value: '${me.value?.completedToday ?? 0}'.padLeft(2, '0'),
                              label: 'Done today',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: AppSpace.lg),
                      const _ShiftSection(),
                      if (jobs.value?.fromCache ?? false) ...[
                        const SizedBox(height: AppSpace.sm),
                        const _OfflineBanner(),
                      ],
                      const SizedBox(height: AppSpace.md),
                      _NextUpHeader(jobList: jobList),
                      const SizedBox(height: AppSpace.sm),
                      _FilterRow(selected: filter),
                    ],
                  ),
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpace.page,
                  AppSpace.md,
                  AppSpace.page,
                  0,
                ),
                sliver: jobs.when(
                  data: (list) => list.jobs.isEmpty
                      ? const SliverFillRemaining(
                          hasScrollBody: false,
                          child: _EmptyJobs(),
                        )
                      : SliverList.separated(
                          itemCount: list.jobs.length,
                          separatorBuilder: (_, _) =>
                              const SizedBox(height: AppSpace.md),
                          itemBuilder: (context, index) {
                            final job = list.jobs[index];
                            return JobCard(
                              job: job,
                              onTap: () => context.push('/jobs/${job.id}'),
                            );
                          },
                        ),
                  loading: () => const SliverFillRemaining(
                    hasScrollBody: false,
                    child: Center(child: CircularProgressIndicator()),
                  ),
                  error: (_, _) => const SliverFillRemaining(
                    hasScrollBody: false,
                    child: _JobsError(),
                  ),
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpace.page,
                  AppSpace.lg,
                  AppSpace.page,
                  AppSpace.xl,
                ),
                sliver: SliverToBoxAdapter(
                  child: Column(
                    children: const [
                      _QuickActions(),
                      SizedBox(height: AppSpace.lg),
                      _MapSnippet(),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

String? _firstName(String? name) {
  final value = name?.trim();
  if (value == null || value.isEmpty) return null;
  return value.split(RegExp(r'\s+')).first;
}

class _NextUpHeader extends StatelessWidget {
  const _NextUpHeader({required this.jobList});

  final List<JobSummary> jobList;

  @override
  Widget build(BuildContext context) {
    final next = jobList.where((j) => j.status == 'in_progress').isNotEmpty;
    return Row(
      children: [
        Expanded(
          child: SectionHeader(next ? 'Next Up' : 'Today\'s Route'),
        ),
        Text(
          next ? 'Sharing' : 'Not sharing',
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                color: appMutedText(context),
              ),
        ),
      ],
    );
  }
}

class _ShiftSection extends StatelessWidget {
  const _ShiftSection();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: const [
        Padding(
          padding: EdgeInsets.symmetric(horizontal: 4),
          child: Row(
            children: [
              Expanded(child: SectionHeader('Next Up')),
            ],
          ),
        ),
        SizedBox(height: AppSpace.xs),
        LocationSharingControls(),
      ],
    );
  }
}

class _FilterRow extends ConsumerWidget {
  const _FilterRow({required this.selected});

  final String? selected;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          for (final (value, label) in _filters) ...[
            ChoiceChip(
              label: Text(label),
              selected: selected == value,
              showCheckmark: false,
              onSelected: (_) =>
                  ref.read(jobsFilterProvider.notifier).state = value,
              selectedColor: AppColors.surfaceLowest,
              backgroundColor: Theme.of(context).brightness == Brightness.dark
                  ? AppColors.darkContainer
                  : AppColors.surfaceHigh,
              side: BorderSide.none,
              labelStyle: Theme.of(context).textTheme.titleSmall?.copyWith(
                    color: selected == value
                        ? Theme.of(context).colorScheme.onSurface
                        : appMutedText(context),
                  ),
            ),
            const SizedBox(width: AppSpace.sm),
          ],
        ],
      ),
    );
  }
}

class _QuickActions extends StatelessWidget {
  const _QuickActions();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionHeader('Quick Actions'),
        const SizedBox(height: AppSpace.sm),
        Row(
          children: [
            Expanded(
              child: _ActionCard(
                icon: Icons.inventory_2_outlined,
                label: 'Request Materials',
                onTap: () => context.push('/materials'),
              ),
            ),
            const SizedBox(width: AppSpace.sm),
            Expanded(
              child: _ActionCard(
                icon: Icons.receipt_long_outlined,
                label: 'Log Expense',
                onTap: () => context.push('/expenses'),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Theme.of(context).brightness == Brightness.dark
            ? AppColors.darkContainer
            : AppColors.surfaceHigh,
        borderRadius: BorderRadius.circular(AppRadii.md),
        border: Border.all(color: appOutline(context).withValues(alpha: 0.3)),
        boxShadow: appSoftShadow(isDark),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(AppRadii.md),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 16),
            child: Column(
              children: [
                Icon(icon, color: AppColors.inkSoft, size: 28),
                const SizedBox(height: AppSpace.sm),
                Text(
                  label,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _MapSnippet extends StatelessWidget {
  const _MapSnippet();

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionHeader('Map'),
        const SizedBox(height: AppSpace.sm),
        Container(
          height: 160,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(AppRadii.md),
            gradient: LinearGradient(
              colors: [
                AppColors.primarySoft.withValues(alpha: isDark ? 0.25 : 0.8),
                Colors.lightBlue.shade100.withValues(alpha: isDark ? 0.15 : 0.65),
              ],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
            boxShadow: appSoftShadow(isDark),
          ),
          child: Stack(
            children: [
              Positioned.fill(
                child: Opacity(
                  opacity: 0.16,
                  child: CustomPaint(painter: _MapGridPainter()),
                ),
              ),
              Positioned(
                left: 14,
                bottom: 14,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 8,
                  ),
                  decoration: BoxDecoration(
                    color: appSurface(context).withValues(alpha: 0.95),
                    borderRadius: BorderRadius.circular(AppRadii.sm),
                    border: Border.all(
                      color: appOutline(context).withValues(alpha: 0.35),
                    ),
                  ),
                  child: Text(
                    'Current: North Plaza (4.2 mi away)',
                    style: Theme.of(context).textTheme.bodyLarge,
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _MapGridPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.secondary.withValues(alpha: 0.2)
      ..strokeWidth = 1;
    const gap = 24.0;
    for (double x = 0; x < size.width; x += gap) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (double y = 0; y < size.height; y += gap) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _EmptyJobs extends StatelessWidget {
  const _EmptyJobs();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpace.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.check_circle_outline_rounded,
              size: 40,
              color: AppColors.primary.withValues(alpha: 0.7),
            ),
            const SizedBox(height: AppSpace.md),
            Text('All clear', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: AppSpace.xs),
            Text(
              'No jobs on your route in this view. Pull down to refresh.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}

class _JobsError extends StatelessWidget {
  const _JobsError();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpace.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.cloud_off_rounded,
              size: 30,
              color: appMutedText(context),
            ),
            const SizedBox(height: AppSpace.md),
            Text(
              'Could not load your jobs. Pull down to try again.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}

class SyncStatusBar extends ConsumerWidget {
  const SyncStatusBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingOutboxProvider).value?.length ?? 0;
    final photos = ref.watch(pendingPhotosProvider).value ?? 0;
    final conflicts = ref.watch(conflictOutboxProvider).value?.length ?? 0;
    final queued = pending + photos;
    if (queued == 0 && conflicts == 0) return const SizedBox.shrink();

    return InkWell(
      key: const Key('sync-status-bar'),
      onTap: () => context.go('/profile'),
      borderRadius: BorderRadius.circular(AppRadii.md),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        decoration: BoxDecoration(
          color: AppColors.errorSoft,
          borderRadius: BorderRadius.circular(AppRadii.md),
        ),
        child: Row(
          children: [
            const Icon(Icons.error_outline, color: AppColors.error),
            const SizedBox(width: AppSpace.sm),
            Expanded(
              child: Text(
                conflicts > 0 ? '$conflicts need review' : '$queued queued',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      color: AppColors.error,
                    ),
              ),
            ),
            const Icon(Icons.chevron_right, color: AppColors.error),
          ],
        ),
      ),
    );
  }
}

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    return Row(
      key: const Key('offline-banner'),
      children: [
        Icon(Icons.cloud_off_outlined, size: 16, color: appMutedText(context)),
        const SizedBox(width: AppSpace.sm),
        Text(
          'Offline - showing saved jobs',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}
