import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../app/theme.dart';
import '../../app/widgets/page_header.dart';
import '../jobs/job_models.dart';
import '../jobs/jobs_providers.dart';
import '../jobs/widgets/job_card.dart';

class ScheduleScreen extends ConsumerWidget {
  const ScheduleScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobs = ref.watch(allAssignedJobsProvider);
    final me = ref.watch(meProvider);

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          onRefresh: () async => ref.invalidate(allAssignedJobsProvider),
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(
                child: PageHeader(
                  title: 'Schedule',
                  trailing: HeaderActions(name: me.value?.name),
                  compact: true,
                ),
              ),
              jobs.when(
                data: (list) {
                  final items = list.jobs;
                  if (items.isEmpty) {
                    return SliverFillRemaining(
                      hasScrollBody: false,
                      child: ListView(
                        physics: const AlwaysScrollableScrollPhysics(),
                        padding: const EdgeInsets.symmetric(
                          horizontal: AppSpace.page,
                        ),
                        children: [
                          if (list.fromCache) const _OfflineBanner(),
                          const SizedBox(height: 160),
                          Text(
                            'No assigned work yet',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodyLarge,
                          ),
                        ],
                      ),
                    );
                  }

                  final groups = _groupJobsByDay(items);
                  return SliverPadding(
                    padding: const EdgeInsets.fromLTRB(
                      AppSpace.page,
                      8,
                      AppSpace.page,
                      AppSpace.xl,
                    ),
                    sliver: SliverList(
                      delegate: SliverChildListDelegate([
                        if (list.fromCache) const _OfflineBanner(),
                        for (final group in groups) ...[
                          Padding(
                            padding: const EdgeInsets.only(
                              top: AppSpace.lg,
                              bottom: AppSpace.md,
                            ),
                            child: Text(
                              _dayLabel(group.day),
                              style: Theme.of(context)
                                  .textTheme
                                  .headlineSmall
                                  ?.copyWith(color: appMutedText(context)),
                            ),
                          ),
                          for (final job in group.jobs) ...[
                            JobCard(
                              job: job,
                              onTap: () => context.push('/jobs/${job.id}'),
                            ),
                            const SizedBox(height: AppSpace.md),
                          ],
                        ],
                        const SizedBox(height: 32),
                        Center(
                          child: Container(
                            width: 112,
                            height: 6,
                            decoration: BoxDecoration(
                              color: appOutline(context).withValues(alpha: 0.4),
                              borderRadius: BorderRadius.circular(AppRadii.full),
                            ),
                          ),
                        ),
                        const SizedBox(height: 12),
                        Center(
                          child: Text(
                            'End of schedule',
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                        ),
                      ]),
                    ),
                  );
                },
                loading: () => const SliverFillRemaining(
                  hasScrollBody: false,
                  child: Center(child: CircularProgressIndicator()),
                ),
                error: (_, _) => SliverFillRemaining(
                  hasScrollBody: false,
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    children: [
                      const SizedBox(height: 160),
                      Center(
                        child: Text(
                          'Could not load your schedule - pull to retry',
                          style: Theme.of(context).textTheme.bodyLarge,
                        ),
                      ),
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

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        key: const Key('schedule-offline-banner'),
        children: [
          Icon(Icons.cloud_off_outlined, size: 16, color: appMutedText(context)),
          const SizedBox(width: 8),
          Text(
            'Offline - showing saved schedule',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _JobDayGroup {
  const _JobDayGroup(this.day, this.jobs);

  final DateTime? day;
  final List<JobSummary> jobs;
}

List<_JobDayGroup> _groupJobsByDay(List<JobSummary> jobs) {
  final sorted = [...jobs]
    ..sort((a, b) {
      final aDate = a.scheduledStart;
      final bDate = b.scheduledStart;
      if (aDate == null && bDate == null) return a.title.compareTo(b.title);
      if (aDate == null) return 1;
      if (bDate == null) return -1;
      return aDate.compareTo(bDate);
    });
  final groups = <DateTime?, List<JobSummary>>{};
  for (final job in sorted) {
    final scheduled = job.scheduledStart?.toLocal();
    final day = scheduled == null
        ? null
        : DateTime(scheduled.year, scheduled.month, scheduled.day);
    groups.putIfAbsent(day, () => []).add(job);
  }
  return [
    for (final entry in groups.entries) _JobDayGroup(entry.key, entry.value),
  ];
}

String _dayLabel(DateTime? day) {
  if (day == null) return 'Unscheduled';
  return DateFormat('EEEE, d MMM').format(day);
}
