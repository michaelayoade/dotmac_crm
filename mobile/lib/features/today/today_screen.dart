import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../jobs/jobs_providers.dart';
import '../jobs/widgets/job_card.dart';

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
    final jobs = ref.watch(jobsListProvider);
    final filter = ref.watch(jobsFilterProvider);

    return Scaffold(
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: () async {
            ref.invalidate(meProvider);
            ref.invalidate(jobsListProvider);
          },
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                sliver: SliverToBoxAdapter(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      me.when(
                        data: (data) => Text(
                          'Hello, ${data.name.split(' ').first}',
                          style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        loading: () => const SizedBox(height: 32),
                        error: (_, _) => const SizedBox(height: 32),
                      ),
                      const SizedBox(height: 12),
                      me.when(
                        data: (data) => Row(
                          children: [
                            _MetricTile(value: '${data.openJobs}', label: 'open'),
                            const SizedBox(width: 12),
                            _MetricTile(value: '${data.completedToday}', label: 'done today'),
                          ],
                        ),
                        loading: () => const SizedBox.shrink(),
                        error: (_, _) => const SizedBox.shrink(),
                      ),
                      const SizedBox(height: 16),
                      SingleChildScrollView(
                        scrollDirection: Axis.horizontal,
                        child: Row(
                          children: [
                            for (final (value, label) in _filters) ...[
                              FilterChip(
                                label: Text(label),
                                selected: filter == value,
                                onSelected: (_) => ref.read(jobsFilterProvider.notifier).state = value,
                              ),
                              const SizedBox(width: 8),
                            ],
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              jobs.when(
                data: (items) => items.isEmpty
                    ? const SliverFillRemaining(
                        hasScrollBody: false,
                        child: Center(child: Text('No jobs in this view')),
                      )
                    : SliverPadding(
                        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                        sliver: SliverList.separated(
                          itemCount: items.length,
                          separatorBuilder: (_, _) => const SizedBox(height: 12),
                          itemBuilder: (context, index) {
                            final job = items[index];
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
                error: (error, _) => SliverFillRemaining(
                  hasScrollBody: false,
                  child: Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text('Could not load jobs — pull to retry', textAlign: TextAlign.center),
                    ),
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

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.value, required this.label});

  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 14),
          child: Column(
            children: [
              Text(value, style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w700)),
              Text(label, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
      ),
    );
  }
}
