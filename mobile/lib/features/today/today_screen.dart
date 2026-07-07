import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../app/theme.dart';
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
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpace.lg,
                  AppSpace.md,
                  AppSpace.lg,
                  AppSpace.sm,
                ),
                sliver: SliverToBoxAdapter(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _Greeting(me: me, jobCount: jobList.length),
                      const SizedBox(height: AppSpace.lg),
                      _Summary(me: me, jobs: jobList),
                      const SizedBox(height: AppSpace.md),
                      const SyncStatusBar(),
                      const LocationSharingControls(),
                      if (jobs.value?.fromCache ?? false)
                        const _OfflineBanner(),
                      const SizedBox(height: AppSpace.md),
                      _FilterRow(selected: filter),
                    ],
                  ),
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpace.lg,
                  AppSpace.sm,
                  AppSpace.lg,
                  0,
                ),
                sliver: SliverToBoxAdapter(
                  child: SectionHeader(
                    filter == null
                        ? 'Next up'
                        : _filters.firstWhere((f) => f.$1 == filter).$2,
                  ),
                ),
              ),
              jobs.when(
                data: (list) => list.jobs.isEmpty
                    ? const SliverFillRemaining(
                        hasScrollBody: false,
                        child: _EmptyJobs(),
                      )
                    : SliverPadding(
                        padding: const EdgeInsets.fromLTRB(
                          AppSpace.lg,
                          0,
                          AppSpace.lg,
                          AppSpace.xxl + 8,
                        ),
                        sliver: SliverList.separated(
                          itemCount: list.jobs.length,
                          separatorBuilder: (_, _) =>
                              const SizedBox(height: AppSpace.md - 1),
                          itemBuilder: (context, index) {
                            final job = list.jobs[index];
                            return JobCard(
                              job: job,
                              onTap: () => context.push('/jobs/${job.id}'),
                            );
                          },
                        ),
                      ),
                loading: () => const SliverFillRemaining(
                  hasScrollBody: false,
                  child: Center(child: CircularProgressIndicator()),
                ),
                error: (error, _) => const SliverFillRemaining(
                  hasScrollBody: false,
                  child: _JobsError(),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Greeting extends StatelessWidget {
  const _Greeting({required this.me, required this.jobCount});

  final AsyncValue<MeSummary> me;
  final int jobCount;

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final date = DateFormat('EEEE · d MMM').format(now);
    final name = me.value?.name.trim();
    final first = (name == null || name.isEmpty)
        ? null
        : name.split(RegExp(r'\s+')).first;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                date.toUpperCase(),
                style: const TextStyle(
                  fontFamily: 'PlusJakartaSans',
                  fontSize: 11.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                  color: AppColors.primaryDeep,
                ),
              ),
              const SizedBox(height: 3),
              Text(
                first == null ? 'Hi there' : 'Hi, $first',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const SizedBox(height: 2),
              Text(
                jobCount == 0
                    ? 'Nothing scheduled yet'
                    : '$jobCount ${jobCount == 1 ? 'job' : 'jobs'} on your route today',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
        const SizedBox(width: AppSpace.md),
        _Avatar(name: name),
      ],
    );
  }
}

class _Avatar extends StatelessWidget {
  const _Avatar({required this.name});
  final String? name;

  String get _initials {
    final n = name?.trim();
    if (n == null || n.isEmpty) return '?';
    final parts = n.split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    if (parts.length == 1) return parts.first.characters.first.toUpperCase();
    return (parts.first.characters.first + parts.last.characters.first)
        .toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 44,
      height: 44,
      decoration: const BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [AppColors.primary, AppColors.primaryDeep],
        ),
      ),
      alignment: Alignment.center,
      child: Text(
        _initials,
        style: const TextStyle(
          fontFamily: 'Outfit',
          fontSize: 16,
          fontWeight: FontWeight.w800,
          color: Colors.white,
        ),
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.me, required this.jobs});

  final AsyncValue<MeSummary> me;
  final List<JobSummary> jobs;

  String? get _nextTime {
    final upcoming = jobs
        .where((j) => j.status != 'completed' && j.scheduledStart != null)
        .toList()
      ..sort((a, b) => a.scheduledStart!.compareTo(b.scheduledStart!));
    if (upcoming.isEmpty) return null;
    return DateFormat.Hm().format(upcoming.first.scheduledStart!.toLocal());
  }

  @override
  Widget build(BuildContext context) {
    final assigned = me.value?.openJobs;
    final done = me.value?.completedToday;
    final next = _nextTime;
    return Row(
      children: [
        Expanded(
          child: StatTile(
            value: assigned?.toString() ?? '—',
            label: 'Assigned',
            highlighted: true,
          ),
        ),
        const SizedBox(width: AppSpace.sm + 1),
        Expanded(
          child: StatTile(value: done?.toString() ?? '—', label: 'Done today'),
        ),
        const SizedBox(width: AppSpace.sm + 1),
        Expanded(child: StatTile(value: next ?? '—', label: 'Next job')),
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
            FilterChip(
              label: Text(label),
              selected: selected == value,
              showCheckmark: false,
              onSelected: (_) =>
                  ref.read(jobsFilterProvider.notifier).state = value,
              labelStyle: TextStyle(
                fontFamily: 'PlusJakartaSans',
                fontWeight: FontWeight.w600,
                fontSize: 12.5,
                color: selected == value
                    ? AppColors.primaryDeep
                    : Theme.of(context).textTheme.bodyMedium?.color,
              ),
              selectedColor: AppColors.primary.withValues(alpha: 0.14),
              side: BorderSide(
                color: selected == value
                    ? AppColors.primary
                    : Theme.of(context).dividerColor,
              ),
            ),
            const SizedBox(width: AppSpace.sm),
          ],
        ],
      ),
    );
  }
}

class _EmptyJobs extends StatelessWidget {
  const _EmptyJobs();

  @override
  Widget build(BuildContext context) {
    final faint = Theme.of(context).brightness == Brightness.dark
        ? AppColors.inkFaintDark
        : AppColors.inkFaint;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpace.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: AppColors.primary.withValues(alpha: 0.1),
                shape: BoxShape.circle,
              ),
              child: const Icon(
                Icons.check_circle_outline_rounded,
                size: 30,
                color: AppColors.primary,
              ),
            ),
            const SizedBox(height: AppSpace.lg),
            Text('All clear', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: AppSpace.xs),
            Text(
              'No jobs on your route in this view.\nPull down to refresh.',
              textAlign: TextAlign.center,
              style: Theme.of(
                context,
              ).textTheme.bodyMedium?.copyWith(color: faint),
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
        padding: const EdgeInsets.all(AppSpace.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.cloud_off_rounded,
              size: 30,
              color: AppColors.inkFaint,
            ),
            const SizedBox(height: AppSpace.md),
            Text(
              "Couldn't load your jobs.\nPull down to try again.",
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}

/// Compact sync state: queued work + items needing review, tap → Profile.
/// Calm by design — amber for attention, never red.
class SyncStatusBar extends ConsumerWidget {
  const SyncStatusBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingOutboxProvider).value?.length ?? 0;
    final photos = ref.watch(pendingPhotosProvider).value ?? 0;
    final conflicts = ref.watch(conflictOutboxProvider).value?.length ?? 0;
    final queued = pending + photos;
    if (queued == 0 && conflicts == 0) return const SizedBox.shrink();

    const amber = Color(0xFFF59E0B);
    final parts = <String>[
      if (queued > 0) '$queued queued',
      if (conflicts > 0) '$conflicts need review',
    ];
    return Padding(
      padding: const EdgeInsets.only(top: AppSpace.md),
      child: InkWell(
        key: const Key('sync-status-bar'),
        onTap: () => context.go('/profile'),
        borderRadius: BorderRadius.circular(AppRadii.control),
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpace.md,
            vertical: 10,
          ),
          decoration: BoxDecoration(
            color: amber.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(AppRadii.control),
          ),
          child: Row(
            children: [
              const Icon(Icons.sync_rounded, size: 17, color: amber),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: Text(
                  parts.join(' · '),
                  style: Theme.of(
                    context,
                  ).textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600),
                ),
              ),
              const Icon(Icons.chevron_right_rounded, size: 18, color: amber),
            ],
          ),
        ),
      ),
    );
  }
}

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    final faint = Theme.of(context).brightness == Brightness.dark
        ? AppColors.inkFaintDark
        : AppColors.inkFaint;
    return Padding(
      padding: const EdgeInsets.only(top: AppSpace.sm),
      child: Row(
        key: const Key('offline-banner'),
        children: [
          Icon(Icons.cloud_off_outlined, size: 15, color: faint),
          const SizedBox(width: AppSpace.sm),
          Text(
            'Offline — showing saved jobs',
            style: Theme.of(
              context,
            ).textTheme.bodySmall?.copyWith(color: faint),
          ),
        ],
      ),
    );
  }
}
